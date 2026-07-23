---
name: forgeops-completion-report
description: Produce a compact PASS / PARTIAL PASS / FAIL completion report with real blockers and this session's own validation evidence, without repeating implementation narrative already recorded on disk. Use at the end of a bounded ForgeOps checkpoint.
---

Subordinate to this repository's `CLAUDE.md` (see section 12,
"Definition of done") and to any explicit instruction already given in
the current conversation.

## Report contents

1. Verdict: PASS / PARTIAL PASS / FAIL.
2. Files changed (list, not full diffs).
3. Validation evidence actually gathered *this session* - never a
   restated claim from a prior session's `.agent/HANDOFF.md` presented as
   fresh.
4. Real, current blockers - omit resolved ones.
5. Exactly **one** recommended next checkpoint.

## Hard rules

- Do not repeat implementation narrative already recorded in
  `.agent/HANDOFF.md` or `.agent/DECISIONS.md` - reference it by name
  instead of restating it.
- Do not include full test logs or full diffs in the report itself; keep
  raw output on disk and cite the summary.
- Do not fabricate or assume a validation result that was not actually
  run in this session.

## Stop condition

Deliver the report and **stop**. Never automatically begin the
recommended next checkpoint - that requires the operator's explicit
approval in this conversation.
