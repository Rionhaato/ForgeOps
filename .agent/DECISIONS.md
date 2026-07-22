# Decisions

Running log of architecture/process decisions. Newest entries go at the
bottom. Each entry: `## YYYY-MM-DD — <decision title>` followed by 2-3
lines of context/why. This is a history, not a living doc — don't edit
past entries, append new ones.

## 2026-07-21 — ForgeOps directory recovered from a stray TrendForge copy

At session start, `C:\Users\joshd\ForgeOps` was found to contain a
byte-for-byte duplicate of the TrendForge working tree (including live
`.env` files, a database, and uploads) sitting inside a fresh, empty git
repo (branch `master`, 0 commits) — not the clean scaffold the mission
expected. Flagged to the operator rather than guessed at, given the
presence of real secrets on disk and the scale of the discrepancy. The
operator confirmed this was the wrong path and cleaned the directory out
externally before work resumed. Lesson: when the active repo's actual
contents contradict the stated mission premise, stop and confirm before
taking structural or destructive action, even under an otherwise
autonomous mandate.

## 2026-07-21 — ForgeOps supersedes TrendForge's prior "stay minimal" decision

TrendForge's own `.agent/DECISIONS.md` records an explicit operator
decision to keep ForgeOps-style tooling project-local and minimal
("stays project-local tonight, no generic platform"; "packaging kept as
an index, not a restructured kit"), deferring the full multi-project
toolkit. This mission explicitly supersedes that scope decision — the
current priority is the generalized, installable-elsewhere toolkit itself.
The proven 3-script/2-skill/`.agent`-convention core is kept as the
literal starting point; the decision to stop expanding it is what's
discarded. Full reasoning: `docs/architecture-decision.md`.
