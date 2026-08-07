---
name: checkpoint-review
description: Verify a ForgeOps checkpoint's actual diff matches its own description before recommending it be staged or committed. Use before any `git add`/`git commit` in this repository, once a checkpoint is implemented and validated but still sitting uncommitted.
---

Subordinate to this repository's `CLAUDE.md` (see section 9, "State and
handoff rules", and section 12, "Definition of done") and to any
explicit instruction already given in the current conversation.

This formalizes the review every prior checkpoint in this repo has gone
through by hand before a commit - see `.agent/DECISIONS.md`'s entries
for precedent - so it doesn't depend on remembering to do it manually
each time.

## Order of operations

1. Confirm the working directory is actually `C:\Users\joshd\ForgeOps`,
   not the read-only reference repository - re-`cd` explicitly rather
   than trusting shell state to have persisted.
2. Read the checkpoint's own description of itself: the newest
   `.agent/HANDOFF.md` "Completed work" entry, the matching
   `CHANGELOG.md` section, and/or the newest `.agent/DECISIONS.md`
   entry - whichever exist for this checkpoint.
3. `git status --short` for the exact file list, and `git diff --check`
   for whitespace errors.
4. Delegate the file-by-file comparison to the `checkpoint-diff-reviewer`
   subagent: supply it the description from step 2, the file list from
   step 3, and the diff (or enough of it, with permission to `Read` the
   rest) - do not eyeball every file in the main session yourself when a
   checkpoint touches more than two or three files.
5. Read back the subagent's verdict. If it reports DISCREPANCIES FOUND
   or CANNOT VERIFY, surface those plainly - do not soften or resolve
   them yourself without the operator's input.

## Reporting

- State the subagent's verdict plainly: MATCHES / DISCREPANCIES FOUND /
  CANNOT VERIFY.
- List findings, if any, most severe first.
- If MATCHES, name exactly which files are about to be staged - do not
  recommend a broad `git add -A`/`git add .`.

## Hard rules

- Never stage, commit, or push anything yourself, even if the verdict is
  a clean MATCHES.
- Never run the full test suite as part of this review - that's
  `forgeops-validate`'s job, and this skill assumes validation already
  happened per `.agent/HANDOFF.md`'s "Accepted validation evidence".
- Never treat a prior session's or another device's self-reported diff
  summary as verified fact - re-derive the file list and diff yourself
  this session.

## Stop condition

Report the verdict and **stop**. Staging and committing require the
operator's explicit approval in this conversation, given separately from
approval to review.
