# Audit Security Model

What `forgeops audit` reads, what it never reads, what it writes, and the
guarantees this phase actually verified versus merely intends.

## Read-only, verified by test

`forgeops audit` never deletes, moves, edits, stages, commits, ignores,
quarantines, rewrites, or auto-fixes anything in the target repository.
`tests/integration/test_cli_audit.py::test_audit_is_strictly_read_only`
snapshots every file's SHA-256 hash and mtime (excluding `logs/`, the
audit's own documented output area) plus full `git status --porcelain
--ignored` output before and after a run that touches every finding
category at once (a real secret, a dangerous staged file, an untracked
`.env`, an oversized binary), and asserts byte-for-byte equality. This is
an automated guarantee, not a claim taken on faith.

## What is never opened, read, or scanned for content

Determined by category during the single bounded tree walk
(`forgeops/detectors/tree_scan.py`), before any content scanning happens:

| Category | Detection | Content ever read? |
|---|---|---|
| `.env`, `.env.*` (any variant, including `.env.example`) | filename shape | **Never** — path and category reported only |
| Database files (`.db`, `.sqlite`, `.sqlite3`) | extension | **Never** |
| Browser/session state files (`storageState`, `auth-state`, `cookies` in the path) | path substring | **Never** |
| Media/model artifacts (images, video, audio, `.onnx`/`.gguf`/`.safetensors`/`.pt`/`.pth`) | extension | **Never** |
| Generated-artifact directories (`node_modules`, `dist`, `build`, `__pycache__`, `.venv`, etc.) | directory name | **Never** — not descended into at all, so nothing inside is even enumerated |

This is deliberately stricter than "don't print the value" — for these
five categories, ForgeOps's own process never calls `Path.read_text()` or
`Path.read_bytes()` on the file in the first place. A test
(`test_audit_never_reads_env_file_contents`) plants a real-looking secret
value inside a `.env` file and asserts it never appears anywhere in the
audit's JSON or human output — but the stronger guarantee is architectural
(`forgeops/detectors/tree_scan.py` marks these files `sensitive=True` and
excludes them from `scannable_text_files` before `secret_scan.py` ever
sees the list).

## What is scanned for content, and how

Every other text file under the configured size threshold
(`secret_scan_max_file_bytes`, default 2MB) is scanned line-by-line
against the pattern table in `forgeops/security/secret_scan.py`: AWS
access key IDs, OpenAI-style keys, Google API keys, PEM private-key
blocks, generic Bearer tokens, JWTs, and database connection strings.

This deliberately includes source code, markdown, and config files -
**not just files with "secret" in the name** - because TrendForge's own
documented credential-leak incident (`docs/known-failures.md`) happened in
a JSON scene file and a markdown handoff file, neither of which any
filename-based rule would have caught. Only the content-based scan catches
that shape of incident.

### The redaction guarantee

`secret_scan.py`'s `SecretFinding.redacted_match` is a synthetic string
(`<category pattern matched, value redacted>`) constructed without ever
touching the matched substring — not a redaction *applied to* the match,
but a description that never captures it in the first place. Verified by
`test_scan_text_never_includes_matched_value` and
`test_audit_never_prints_the_secret_value`.

### The `forgeops:allow-secret` inline marker

A line containing the literal substring `forgeops:allow-secret` is
skipped entirely by the scanner. Exists because test fixtures
legitimately need to contain fake, secret-shaped strings to test the
scanner itself — see `docs/phase2a-porting-notes.md` for how this was
discovered (dogfooding `forgeops audit` against its own repo initially
flagged its own test suite). This is a narrow, explicit opt-out a human
author writes deliberately on a specific line — not a directory-level or
file-level exclusion, so it can't accidentally suppress a real finding
elsewhere in the same file.

## Known limitations

- **Pattern-based, not semantic.** The scanner cannot tell a real AWS key
  from a syntactically valid but fake/rotated one, and it can miss secret
  shapes it has no pattern for (a bespoke internal token format, for
  example). Treat a clean `forgeops audit` as "no *known* secret shapes
  found," not a formal guarantee of no secrets.
- **Size-capped.** Files over `secret_scan_max_file_bytes` (default 2MB)
  are not scanned at all, to keep audit runtime bounded.
- **No historical scan.** `forgeops audit` inspects the current working
  tree only. A secret committed in the past and later removed from the
  working tree is not detected — that requires a separate git-history
  scan, which is not part of Phase 2A.
- **Nested-git-repo detection does not descend into vendored `.git`
  directories.** If a vendored dependency itself contains a nested
  vendored dependency with its own `.git`, only the outer one is reported.
- **The dangerous-filename check operates on `git status` paths only**
  (staged + modified + untracked), not the full tree — a dangerous file
  that is already tracked and unmodified (committed in a previous,
  unaudited session) is reported via the `env-files-real`/`db-files`/etc.
  category findings, not via `git-safety`, which is specifically about
  *changes* about to be committed.
