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

## 2026-07-21 — Repo-local git identity, not global

The first commit failed with "Author identity unknown" — no global
`.gitconfig` exists on this machine. Rather than run `git config
--global` (against the standing git-safety-protocol instruction not to
touch git config), set `user.name`/`user.email` locally in this repo
only, mirroring TrendForge's own repo-local (not global) identity
convention discovered by reading its local config. Non-destructive,
scoped to this repo, and matches existing practice rather than
introducing a new one.

## 2026-07-21 — Phase 1 placeholders state their implementing phase, never silently no-op

Every structural file created in Phase 1 that isn't yet functionally
complete (installer scripts, the `forgeops` CLI entry point) prints a
clear "not yet implemented, see Phase N" message and exits non-zero,
rather than exiting 0 or doing nothing silently. Chosen so that a future
session (or the operator) running one of these by mistake gets an honest
signal instead of a false "it worked" or a silent failure.
