# Phase 2A Porting Notes

What was generalized from TrendForge's `scripts/forgeops/*.py` (inspected
read-only per `docs/reusable-components.md`), what changed, and why. No
TrendForge source was copied verbatim — every module below was rewritten
against ForgeOps's own architecture.

## `repository_snapshot.py` -> `forgeops/core/git.py`

TrendForge's version: a single script, hardcoded to assume the repo root
is `Path(__file__).resolve().parents[2]` (i.e. "wherever this script
happens to live, three directories up"), printing one flat JSON snapshot
(branch, head, ahead/behind, modified/untracked/staged counts).

Generalized into `forgeops/core/git.py` as a library of small, independently
testable functions (`get_branch_state`, `get_head`, `get_remotes`,
`get_ahead_behind`, `get_status`, `get_operation_state`, `get_last_commit`,
`get_ignored_summary`) rather than one script that prints one shape.
Removed:
- the hardcoded `parents[2]` path assumption — repo root now comes from
  `forgeops.core.paths.resolve_repo_root`, which works from any nested
  directory or an explicit `--repo` path;
- the implicit assumption that HEAD having a symbolic ref means commits
  exist (`symbolic-ref` succeeds even in a freshly `git init`'d repo with
  zero commits — TrendForge's script never needed to distinguish this
  since it only ran inside an already-established repo; ForgeOps's own
  test suite caught this gap via `test_branch_state_no_commits_yet`, see
  `docs/phase2a-validation.md`).
Added: remote URL credential redaction (`redact_remote_url` — TrendForge's
version never handled remotes at all), detached-HEAD detection, merge/
rebase/cherry-pick/revert/bisect state detection, last-commit summary.

## `git_safety_check.py` -> `forgeops/security/dangerous_files.py`

TrendForge's `DANGEROUS_PATTERNS` hardcoded a TrendForge-specific path:
`artifacts/presentation-demo/*` (its own demo-recorder output directory).
Removed entirely — not meaningful outside TrendForge. Kept the shape rules
that generalize cleanly: `.env` variants, `*.mp4`, anything named
`*credentials*`/`*secret*`. Added `*.pem`, `*.key`, `*.mov` (broader,
project-agnostic credential/media shapes).

One thing TrendForge's `SAFE_EXCEPTIONS` already got right and this port
initially got wrong: TrendForge explicitly excluded its own tool paths
(`scripts/forgeops/*secret*`, `scripts/forgeops/*credentials*`) from its
own dangerous-pattern check, because a script *implementing* secret
scanning matches a filename pattern meant to catch files *containing* a
secret. The first working version of `forgeops audit` run against
ForgeOps's own repo (see `docs/phase2a-validation.md`) reproduced exactly
this false positive on `forgeops/security/secret_scan.py`. Fixed more
generally than TrendForge's hardcoded exception: `*credentials*`/`*secret*`
now only apply to non-source-code file extensions (`SOURCE_CODE_SUFFIXES`
in `dangerous_files.py`), so this generalizes to any consumer repo with a
similarly-named module instead of requiring a new hardcoded exception per
project.

## `scan_secret_patterns.py` -> `forgeops/security/secret_scan.py`

Kept TrendForge's core design decision unchanged and treated as a hard
requirement, not a suggestion: **the matched value is never returned,
stored, or printed** — only a category label, file, and line number
(`forgeops`'s version is if anything stricter: even the TrendForge
original's `match=[REDACTED]` convention is replaced with a fully
synthetic description string, `<category pattern matched, value
redacted>`, that never touches the real matched text at all).

Changed:
- TrendForge scanned only `git ls-files` output (tracked files). ForgeOps
  scans everything the bounded tree walk (`forgeops/detectors/tree_scan.py`)
  classifies as "scannable text" — which includes *untracked* files too.
  This is deliberate: TrendForge's own credential-leak incident (see
  `docs/known-failures.md`) happened in files that would still have been
  untracked at the moment a scan could have caught them pre-commit.
  Scanning only tracked files misses exactly that window.
- Expanded `PATTERNS` beyond AWS/OpenAI-style keys and PEM blocks: added
  Google API keys, generic Bearer tokens, JWTs, and database connection
  strings (categories named explicitly in the Phase 2A mission brief that
  TrendForge's original script didn't cover).
- Added `severity` and `remediation` per finding (TrendForge's version had
  neither — findings were a bare `file:line pattern=name` line).
- Added the `forgeops:allow-secret` inline marker (not present in
  TrendForge's version at all) so a line is skipped entirely when it
  contains that literal string. Needed because ForgeOps's own test suite
  legitimately contains fake, secret-shaped fixture strings to test the
  scanner itself — without an allowlist mechanism, `forgeops audit` run
  against ForgeOps's own repo would permanently report itself as blocked.
  See `docs/phase2a-validation.md` for the before/after.

## Not ported

- TrendForge's `scripts/forgeops/README.md` conventions (stdlib-only, no
  network calls, read-only scripts exit 0, gate scripts exit non-zero with
  a clear stderr message, never print a secret value even while scanning
  for one, keep scripts small) were **adopted as conventions**, not ported
  as text — `forgeops/` follows all of them but the module structure is
  entirely new (a package with unit-testable functions, not three
  standalone scripts).
- TrendForge's `.claude/skills/repo-audit/SKILL.md` procedure (pick a
  timestamped `docs/agent-audit/<ts>/` directory, run the three scripts,
  write a `SUMMARY.md`) is **not** what `forgeops audit` does — this
  command is the generalized, single-invocation equivalent, called
  directly rather than following a written-out manual procedure. A
  `repository-audit` skill wrapping this command remains future work
  (Phase 7, `shared/skills/repository-audit/`, per
  `docs/reusable-components.md`).
