---
name: forgeops-validate
description: Run ForgeOps' targeted validation before the full suite, summarize failures concisely, and never mutate anything. Use when validating a change in this repository before claiming it works.
---

Subordinate to this repository's `CLAUDE.md` (see section 8, "Standard
validation commands") and to any explicit instruction already given in
the current conversation.

## Order of operations

1. Targeted tests relevant to the files actually changed - not the full
   suite - during implementation.
2. `python -m compileall -q forgeops tests`
3. `git diff --check`
4. The full suite, `python -m pytest tests -q`, **exactly once**, at the
   final checkpoint gate - not after every small edit.

## Reporting

- Write raw command output to disk (scratchpad or `logs/`) rather than
  pasting full logs or tracebacks into the conversation.
- Summarize failures concisely: file/test name and a one-line reason.
- State PASS / PARTIAL PASS / FAIL plainly, backed by evidence actually
  gathered this session.

## Hard rules

- Never commit, push, or deploy.
- Never run a mutating command against any repository other than the
  one being validated - and never against the read-only reference
  repository named in `CLAUDE.md` section 6, under any circumstance.
- Never rerun a validation step this session already completed with an
  unchanged result unless the underlying code changed since.

## Stop condition

Report the validation evidence and **stop**. Do not proceed to commit or
to a further checkpoint without the operator's explicit approval in this
conversation.
