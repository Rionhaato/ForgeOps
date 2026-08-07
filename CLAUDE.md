# CLAUDE.md

Permanent operating rules for agents working in this repo. Procedures
live in `docs/` and `tests/`, not here — this file is rules and pointers
only. Keep it small; it is loaded into every session's context.

## 1. Project identity

ForgeOps: a Python toolkit (`forgeops/`) plus Claude/Codex plugin
packages (`claude-plugin/`, `codex-plugin/`) that gives coding agents
deterministic project state, targeted testing, safety gates, and
lifecycle hooks. Not a dashboard, server, database, or SaaS product.

## 2. Purpose

Reduce repeated repository investigation, run deterministic scripts
before model reasoning, select tests from changed files instead of
always running everything, and block destructive or credential-leaking
actions by default. Installable into other projects once packaged
(Phase 12+) — this repo is both the toolkit and its own first user.

## 3. Session startup order

1. Read this file.
2. Read `.agent/CURRENT_STATE.json`.
3. Read `.agent/HANDOFF.md`.
4. Read `.agent/DECISIONS.md` only for entries relevant to the current task.
5. Inspect git status (`forgeops status` or `git status --short`).
6. Resume the exact next action recorded in `HANDOFF.md`/`CURRENT_STATE.json`
   instead of reinvestigating already-completed work.

## 4. Sources of truth

| File/dir | Answers |
|---|---|
| `CLAUDE.md` (this file) | Stable operating rules — changes rarely. |
| `.agent/CURRENT_STATE.json` | Current machine-readable state. |
| `.agent/HANDOFF.md` | Human-readable continuation point. |
| `.agent/DECISIONS.md` | Durable architectural decisions, append-only. |
| `docs/` | Detailed design, security model, validation evidence. |
| `logs/` | Raw, sanitized command output (gitignored). |
| `tests/` | The executable behavior contract — if a test doesn't cover it, don't claim it works. |

Never re-derive a fact that one of these already records.

## 5. Architecture rules

- Deterministic scripts run before model reasoning; agents are for
  bounded judgment calls only.
- CLI handlers (`forgeops/cli/*.py`) stay thin: parse args, call a
  `run_*` function, render the result. All logic lives in
  `forgeops/core`, `forgeops/detectors`, `forgeops/security`,
  `forgeops/testing`, `forgeops/state`, `forgeops/worktrees`,
  `forgeops/reporting`.
- Every `run_*` function is a pure function of its arguments (no
  `sys.argv`, `print`, or `sys.exit`) so it is directly unit-testable.
- All repository-inspection commands (`doctor`, `status`, `audit`,
  `changed`, `test --plan`/`--dry-run`) are read-only unless explicitly
  documented otherwise.

## 6. Safety boundaries

- All sensitive output is redacted before it reaches console, JSON, or
  disk — see `docs/audit-security-model.md`.
- Full logs stay on disk under `logs/`; only concise summaries return to
  agents or terminal output.
- Windows paths containing spaces are first-class, not an edge case.
- Every mutating command supports `--dry-run` where practical.
- Unexpected internal failures return `INTERNAL_ERROR` (exit 6) with a
  sanitized message — never a raw traceback by default.
- `C:\Users\joshd\TrendForge` is a read-only reference repository: never
  modify it, never commit to it, never run a mutating ForgeOps command
  against it, and never copy credentials, browser/session state,
  uploads, databases, or generated media from it into this repo.

## 7. Development workflow

Work on a feature branch, never directly on `master`. One coherent
commit per accepted phase, only after all quality gates pass. Never
push; no remote is configured for this repo. Never force-push, reset
--hard, or rewrite history without explicit operator approval.

## 8. Standard validation commands

```
python -m pytest tests -q
python -m compileall -q forgeops tests
python -m forgeops doctor
python -m forgeops status
python -m forgeops audit
git diff --check
git status --short
```

## 9. State and handoff rules

Update `.agent/CURRENT_STATE.json` and `.agent/HANDOFF.md` at the end of
every session/phase. Append to `.agent/DECISIONS.md` when a durable
architectural choice is made — never edit past entries. State files never
contain secret values or full raw logs, only pointers/summaries.

## 10. Delegation rules

Never let two agents or parallel tasks edit overlapping files. Use
isolated git worktrees for bounded parallel implementation once that
tooling exists (not yet built — see `.agent/HANDOFF.md`). Never claim a
command works, or that a test passed, without having actually run it in
this session.

## 11. Approval boundaries

Never push, deploy, authenticate an external service, publish content,
spend money, or perform any financial execution without Joshua's
explicit approval in that conversation. Installing software or
authenticating credentials also requires explicit approval first.

## 12. Definition of done

A change is done only when: targeted tests pass, the full relevant test
suite passes, `git diff --check` is clean, no secrets or generated
artifacts are staged, TrendForge is verified unchanged, and
`.agent/HANDOFF.md` is updated with the exact next action. See
`docs/phase2a-validation.md` and `docs/phase2b-validation.md` for the
standing example of what "validated, not assumed" looks like in this repo.
