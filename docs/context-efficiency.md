# Context-efficiency foundation

Why this exists: ForgeOps sessions were rereading `.agent/CURRENT_STATE.json`,
`.agent/HANDOFF.md`, and large swaths of the repository at the start of
every session, and running broad exploratory reads mid-session for
recovery/audit work. This checkpoint reduces that main-session context
cost without changing who governs the workflow - ForgeOps and `CLAUDE.md`
remain the sole authority; nothing here delegates control elsewhere.

**Context reduction means reducing main-session context consumption, not
claiming zero total token usage anywhere in the system.** A subagent or a
hook still spends tokens/cycles - the point is that cost stays out of the
main conversation's context window.

## The five pieces

1. **`forgeops resume-context`** (`forgeops/cli/resume_context.py`,
   `forgeops/state/resume_context.py`) - a read-only, bounded-size (see
   `RESUME_CONTEXT_MAX_BYTES`, tested in
   `tests/unit/test_resume_context.py`) compact summary of repository
   identity, branch/HEAD, working-tree shape, the last recorded
   checkpoint phase, the latest recorded test count, unresolved
   blockers, the exact next approved task (if any), commands already
   validated ("do not repeat"), the standing approval boundaries, and a
   stop-boundary reminder. It deliberately does not call
   `forgeops.state.checkpoint.build_checkpoint_data` (which triggers a
   full repository tree scan for stack detection) - it only reads
   already-computed narrative fields plus cheap git facts, so it stays
   fast and near-free to run at the start of a session.

2. **Project-local skills** (`.claude/skills/forgeops-resume/`,
   `forgeops-validate/`, `forgeops-completion-report/`) - small
   (<2.5KB) instruction files, invoked explicitly or when clearly
   relevant, that reference `CLAUDE.md` and this doc instead of
   restating them. They replace re-explaining the same resume/validate/
   report procedure in every conversation.

3. **One read-only subagent** (`.claude/agents/forgeops-recovery-reviewer.md`)
   - tools restricted to `Read`, `Grep`, `Glob` only (no `Bash`, `Edit`,
   `Write`, or MCP tools), so broad exploratory recovery-audit reads run
   in an isolated context and only a compact structured summary returns
   to the main session. It cannot run `git` itself; the invoking prompt
   supplies git/resume-context facts, or it reads `.git/HEAD` directly
   as plain text.

4. **Minimal deterministic hooks** (`.claude/hooks/`, wired in
   `.claude/settings.json`, project-local only - no global settings
   touched):
   - `PreToolUse` (`pretooluse_safety.py`) on `Bash|Edit|Write|NotebookEdit`:
     denies (a) any command/write targeting the read-only reference
     repository (`CLAUDE.md` section 6) that isn't a recognized
     read-only verb, (b) destructive git operations (`reset --hard`,
     `clean -f*`, force checkout/restore, force push) anywhere in this
     repo, (c) a short list of obviously catastrophic filesystem
     commands. Fails closed on its own malformed input (no decision -
     normal permission flow still applies) rather than crashing or
     hanging.
   - `SessionEnd` (`sessionend_handoff.py`): invokes the existing,
     already-tested `forgeops handoff` writer once, when a session
     actually ends - deliberately **not** wired to `Stop`, which fires
     after every single assistant turn in this Claude Code version and
     would make a tree-scan-adjacent write happen far too often for
     what "session-end handoff" is meant to mean. Bounded execution
     time (subprocess timeout independent of the hook's own
     `settings.json` timeout); never runs the test suite; never commits
     or pushes (neither does `forgeops handoff` itself); fails loudly to
     stderr but never blocks shutdown.
   - `Notification` - reserved for a future checkpoint. No hook is
     registered for it today; this line exists so a future session knows
     the event point was considered, not forgotten.

5. **This document plus `docs/superpowers-compatibility.md`** - the
   Superpowers compatibility record is prior-art analysis only (see that
   file for the full adopt/adapt/reject matrix); it does not install,
   execute, or grant Superpowers any authority over this repository.

## What stays disabled

- No MCP server is configured or enabled by any of the above.
- No write-capable subagent exists; `forgeops-recovery-reviewer` is
  strictly read-only.
- No automatic commit, push, or worktree creation is wired into any
  hook or skill.
- No global (`~/.claude/settings.json`) configuration was touched -
  everything here is project-local to this repository's `.claude/`
  directory.

## Governance precedence

Unchanged from `docs/superpowers-compatibility.md`:

1. Joshua's explicit decisions and approvals
2. ForgeOps security and governance rules
3. this repository's `CLAUDE.md`
4. ForgeOps-native skills and hooks (this document)
5. explicitly selected third-party workflow patterns
6. Claude Code defaults
