---
name: forgeops-resume
description: Determine the exact safe resume point in the ForgeOps repository using forgeops resume-context plus current git state, without rereading the whole repository. Use at the start of a ForgeOps session or when picking up prior work here.
---

Subordinate to this repository's `CLAUDE.md` and to any explicit
instruction already given in the current conversation - never overrides
either.

## Steps

1. Run `python -m forgeops resume-context --json` (or without `--json`
   for a human-readable form). This is the compact, bounded-size,
   read-only summary - prefer it over rereading `.agent/CURRENT_STATE.json`
   or `.agent/HANDOFF.md` in full.
2. If `resume-context` is unavailable or fails, fall back to reading
   `.agent/HANDOFF.md` directly (small, targeted read) rather than the
   whole repository.
3. Run `git status --short` to catch anything the bounded resume-context
   view may have truncated (it caps file/blocker lists).
4. Report: current phase, unresolved blockers, the exact next approved
   task if one is recorded, and anything in `do_not_repeat` - do not
   re-run validation resume-context already reports as done.

## Hard rules

- Never edit or write any file.
- Never install anything.
- Never infer success or a clean state without evidence from this
  session's own tool output - a memory of a prior claim is not evidence.
- If a warning or blocker is reported, surface it plainly - never smooth
  it over.

## Stop condition

After establishing the resume point, report it and **stop**. Do not
automatically begin implementation - that requires the operator's
explicit go-ahead in this conversation.
