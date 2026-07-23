# `forgeops process-list` and `forgeops cleanup`

Read-only discovery of processes associated with a repository, and a
conservative, defaults-to-dry-run cleanup command for the narrow subset
ForgeOps can prove it's actually responsible for. `cleanup` is the only
mutating command in this toolkit that can affect something outside
`.agent/` — it earns that exception through an unusually strict safety
model, documented in full below.

## The two commands

| Command | Mutates? | Purpose |
|---|---|---|
| `forgeops process-list` | Never | Discover and classify OS processes possibly related to the repository |
| `forgeops cleanup` | Only if `--execute` is passed | Gracefully stop `managed` processes and remove stale registry records |

## Classification model

Every discovered process is classified into exactly one of five stable
values (`forgeops/detectors/process_association.py:CLASSIFICATIONS`):

| Classification | Meaning | `forgeops_managed` | `cleanup_eligible` |
|---|---|---|---|
| `managed` | A `.agent/runtime/PROCESS_REGISTRY.json` record's PID, repository, and start time all match the live process, and its category is an allowed one | `true` | `true` |
| `associated` | Heuristic-only: the command line (its own, or its parent's) contains the repository path — no registry record | `false` | `false`, always |
| `uncertain` | A registry record exists but something about it doesn't add up (wrong repository, or a disallowed category) | `false` | `false` |
| `stale_record` | A registry record exists for this PID and repository, but the live process's start time doesn't match — **PID reuse** | `false` | `false` |
| `unrelated` | No evidence at all | `false` | `false` |

**`cleanup_eligible` is `true` only for `managed`.** No amount of
heuristic command-line evidence (`associated`) is ever sufficient to
terminate a process — that requires an actual registry record whose PID,
repository, and start time all agree with the live process right now.

A common executable name (`python`, `node`, `npm`, `uvicorn`, `vite`,
`git`, `powershell`, `cmd`, ...) is **never**, by itself, evidence of
anything — see
`tests/unit/test_process_association.py::test_common_executable_name_alone_is_never_associated`.
Only an explicit repository-path match in a command line, a parent
process's command line, or a registry record counts.

### Why "uncertain" isn't used for "couldn't read the command line"

A privileged system process (`svchost.exe`, `csrss.exe`, ...) frequently
can't have its command line read at all when ForgeOps runs unelevated.
Classifying every one of those as `uncertain` would flood
`process-list`'s output with hundreds of irrelevant system processes on
any real machine. `uncertain` is reserved for a registry record that
exists but doesn't fully check out (wrong repository, disallowed
category) — a real, specific reason to be cautious. A process with
*zero* evidence either way, readable command line or not, is `unrelated`
and simply isn't reported — see
`tests/unit/test_process_association.py::test_unreadable_command_line_with_no_other_evidence_is_unrelated_not_uncertain`.

## PID-reuse protection

Operating systems reuse process IDs. A registry record that says "PID
4021 is our backend dev server" can become wrong the instant that
process exits and something unrelated is later assigned the same PID.
Every registry record stores `start_time_utc`; before trusting a record,
both `forgeops process-list` and `forgeops cleanup` compare it against
the **live** process's actual start time. A mismatch — the PID exists,
but its start time doesn't match what was recorded — is `stale_record`,
never `managed`, and is never sufficient to terminate anything. This is
the single mechanism generalized from TrendForge's proven
`trendforge-launcher-common.ps1` (`Test-TrendForgeOwnedProcess`) — see
`docs/known-failures.md` "Background services remaining active", which
named this exact design (`forgeops process-list` /
`PROCESS_REGISTRY.json`) back in Phase 0's source audit.

## Runtime-record schema and location

`.agent/runtime/PROCESS_REGISTRY.json` (`forgeops/state/runtime_registry.py`):

```json
{
  "schema_version": 1,
  "records": [
    {
      "pid": 12345,
      "category": "backend-dev-server",
      "repository_root": "C:\\path\\to\\repo",
      "start_time_utc": "2026-07-22T21:13:33Z",
      "command_fingerprint": "a short, sanitized identity - never the full raw command line",
      "creation_source": "whatever created this record",
      "port": 8000,
      "cleanup_policy": "graceful-only",
      "created_at_utc": "2026-07-22T21:00:00Z"
    }
  ]
}
```

Allowed managed categories (`ALLOWED_MANAGED_CATEGORIES`):
`backend-dev-server`, `frontend-dev-server`, `test-runner`,
`build-process`, `forgeops-test-child`. A fixed deny-list
(`NEVER_MANAGED_CATEGORIES`) — `browser`, `editor`, `shell`, `git`,
`claude`, `codex`, `vcs` — is checked first and always wins even if such
a category somehow ended up in `ALLOWED_MANAGED_CATEGORIES` in the
future; a record claiming one of these is always `uncertain`, never
`managed`, regardless of any other evidence.

**No command in this checkpoint writes new registry records during
normal operation** — there is no `forgeops launch`/init mechanism yet
that starts a long-running background process and registers it (out of
scope; `init`, worktrees, agents were explicitly excluded from this
bounded checkpoint). The registry is populated by whatever future launch
mechanism adopts it, or, today, only by tests and the manual validation
recorded in the phase completion report. `process-list`'s discovery does
not depend on the registry being populated — it still reports
`associated`/`uncertain`/`unrelated` processes found via heuristics.

Never stored: full raw command lines, environment values, credentials,
tokens, unrelated user processes, or browser session data.
`command_fingerprint` is a short, sanitized identity string, not a
verbatim command line.

## Cleanup safety model

1. **Default is always dry-run.** `forgeops cleanup` and
   `forgeops cleanup --dry-run` behave identically — report every
   candidate, take zero action. Only `forgeops cleanup --execute` can
   act, and `--dry-run` wins if both flags are somehow given together.
2. **Only `managed` processes are termination candidates.** Built by
   construction, not by convention — `cleanup_eligible` is `False` on
   every other classification's `AssociationResult`, and cleanup's own
   candidate-selection loop only considers registry records that
   `classify_process()` returns as `managed`.
3. **Revalidation immediately before acting.** Even for a candidate that
   was `managed` when the registry was loaded, `--execute` mode re-checks
   `process_exists()` and re-fetches the live start time right before
   attempting termination — closing the gap between an earlier
   snapshot and the moment cleanup acts. A mismatch at this point cancels
   the termination for that candidate; it is never retried or escalated.
4. **Graceful only.** Termination is `taskkill /PID <pid>` — no `/F`
   (force), ever. A bounded wait (default 5s, polled every 0.5s) checks
   whether the process actually stopped. If it's still running, cleanup
   reports failure and stops. **There is no force-kill path in this
   checkpoint** — real-world validation during development found that
   graceful `taskkill` frequently cannot stop a plain console Python
   process on Windows, and that honest "did not stop, not escalating"
   outcome is the intended, safe behavior, not a bug to work around.
5. **Stale records can be removed without touching any process** — a
   registry entry whose process no longer exists, or whose PID was
   reused, is safe to delete from the registry file itself (the file is
   just ForgeOps's own bookkeeping); this still respects the dry-run/
   execute gate (reported first, removed only on `--execute`).
6. **Registry rewritten atomically.** Any change to
   `PROCESS_REGISTRY.json` goes through the same
   `forgeops/state/atomic_write.py` used by `checkpoint`/`handoff` — no
   partially-written registry is ever visible.

### Files `forgeops cleanup` may remove or modify

- `.agent/runtime/PROCESS_REGISTRY.json` — rewritten (never partially)
  when a termination succeeds or a stale record is removed.
- `logs/cleanup/<timestamp>/cleanup.log` — the same logging side-channel
  every other command already uses.

**Nothing else.** No source file, no credential, no log from another
command, no other repository. Enforced by
`tests/integration/test_process_cleanup_scope.py`, including a test that
plants a registry record whose `repository_root` field points at a
*different* repository and confirms that repository is never touched —
cleanup only ever acts against the repository it was explicitly invoked
against, never a path found inside process metadata.

## Windows behavior and limitations

- Process discovery is implemented via `Get-CimInstance Win32_Process`
  (PowerShell) plus `netstat -ano` for listening ports — no `psutil`,
  no other package installed automatically.
- **`working_directory` is always `None`.** `Win32_Process` has no
  native "current working directory" property (unlike POSIX
  `/proc/<pid>/cwd`) — association leans on command-line text and
  parent/child relationships instead. This is a real, permanent platform
  limitation, reported as such rather than faked.
- A command line that can't be read at all (common for privileged system
  processes when running unelevated) is reported as no-evidence
  (`unrelated`), not as a `fail` — see the classification model above.
- Non-Windows platforms: `process-list`/`cleanup` report a clear
  `WARNINGS_PRESENT`-level limitation and return an empty process list —
  they do not error out, and never claim "nothing is running" is the
  same thing as "could not determine."
- `Get-CimInstance`'s `ConvertTo-Json` renders a `DateTime` property
  (`CreationDate`) as the legacy .NET JSON-date form,
  `/Date(<epoch-ms>)/` — not the raw WMI `CIM_DATETIME` string a naive
  implementation might expect. `forgeops/detectors/processes.py` parses
  both forms; this was a real bug caught during manual real-process
  validation (see the phase completion report) before any test suite
  exercised it, because unit tests were originally written against the
  wrong assumed format.

## Command syntax and exit codes

```
forgeops process-list [--json] [--repo PATH]
forgeops cleanup [--json] [--repo PATH] [--dry-run] [--execute]
```

Both share the same seven codes (`docs/cli-exit-codes.md`):
`SUCCESS`(0) — nothing to report/act on; `WARNINGS_PRESENT`(1) — a
discovery limitation, a registry-schema warning, or (cleanup) at least
one reported candidate/action, or a termination that didn't stop in
time; `REPO_NOT_FOUND`(4); `COMMAND_EXECUTION_FAILURE`(5) — git
unavailable, or a registry-write failure; `INTERNAL_ERROR`(6) — an
unexpected bug, via the existing top-level boundary.

## Secret sanitization

Every raw command line is passed through `forgeops.security.redact.redact_text`
**twice**: once at the discovery layer (`forgeops/detectors/processes.py`,
immediately when a `ProcessInfo` is constructed) and again at the CLI
reporting layer (`forgeops/cli/process_list.py:_summarize_command`) —
deliberate defense in depth, not redundancy for its own sake; a test
(`test_sanitized_command_output`) originally caught a real gap where the
second layer wasn't redacting at all. The registry never stores a raw
command line in the first place, only a bounded `command_fingerprint`.
