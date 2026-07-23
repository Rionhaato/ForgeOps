"""Pure, fully deterministic process<->repository association and
cleanup-eligibility classification. No I/O here - takes already-gathered
`ProcessInfo`/`RegistryDocument` data and a repo root, returns a
classification. Every classification decision is driven by explicit
evidence; a common executable name (python, node, npm, uvicorn, vite,
git, powershell, cmd, ...) is never itself evidence of anything - see
`test_process_association.py::test_common_executable_name_alone_is_never_associated`."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from forgeops.detectors.processes import ProcessInfo
from forgeops.state.runtime_registry import (
    ALLOWED_MANAGED_CATEGORIES,
    NEVER_MANAGED_CATEGORIES,
    RegistryDocument,
)

CLASSIFICATIONS = ("managed", "associated", "uncertain", "unrelated", "stale_record")


@dataclass(frozen=True)
class AssociationResult:
    classification: str  # one of CLASSIFICATIONS
    confidence: str  # "high" | "medium" | "low"
    evidence: list[str] = field(default_factory=list)
    forgeops_managed: bool = False
    cleanup_eligible: bool = False
    ineligible_reason: str | None = None

    def __post_init__(self) -> None:
        if self.classification not in CLASSIFICATIONS:
            raise ValueError(f"invalid classification {self.classification!r}")


def normalize_repo_path(path: str) -> str:
    """Case-insensitive, separator-normalized form for substring/equality
    comparison only - never used for actual filesystem access."""
    return str(path).strip().replace("\\", "/").rstrip("/").lower()


def classify_process(
    proc: ProcessInfo,
    repo_root: Path,
    registry: RegistryDocument,
    all_by_pid: dict[int, ProcessInfo] | None = None,
) -> AssociationResult:
    normalized_repo = normalize_repo_path(str(repo_root))
    all_by_pid = all_by_pid or {}

    record = next((r for r in registry.records if r.pid == proc.pid), None)
    if record is not None:
        repo_matches = normalize_repo_path(record.repository_root) == normalized_repo
        if not repo_matches:
            return AssociationResult(
                classification="uncertain",
                confidence="low",
                evidence=["a registry record exists for this PID but for a different repository"],
                ineligible_reason="registry record's repository does not match the target repository",
            )

        start_matches = (
            record.start_time_utc is not None
            and proc.start_time_utc is not None
            and record.start_time_utc == proc.start_time_utc
        )
        if not start_matches:
            return AssociationResult(
                classification="stale_record",
                confidence="low",
                evidence=[
                    "a registry record exists for this PID and repository, but the process's "
                    "current start time does not match the recorded start time (PID reuse suspected)"
                ],
                ineligible_reason="PID reuse suspected: start time mismatch - never sufficient to terminate",
            )

        if record.category in NEVER_MANAGED_CATEGORIES or record.category not in ALLOWED_MANAGED_CATEGORIES:
            return AssociationResult(
                classification="uncertain",
                confidence="low",
                evidence=[f"registry record category {record.category!r} is not an allowed managed category"],
                ineligible_reason=f"category {record.category!r} is never eligible for cleanup regardless of registry record",
            )

        return AssociationResult(
            classification="managed",
            confidence="high",
            evidence=["registry record PID, repository, and start time all match the live process"],
            forgeops_managed=True,
            cleanup_eligible=True,
        )

    # No registry record - heuristic, non-authoritative evidence only.
    # Never sufficient for cleanup eligibility, no matter how much
    # evidence accumulates - cleanup requires an actual registry record.
    evidence: list[str] = []

    if proc.command_line and normalized_repo in normalize_repo_path(proc.command_line):
        evidence.append("command line contains the repository path")

    parent = all_by_pid.get(proc.ppid) if proc.ppid is not None else None
    if parent is not None and parent.command_line and normalized_repo in normalize_repo_path(parent.command_line):
        evidence.append("parent process command line contains the repository path")

    if evidence:
        confidence = "medium" if len(evidence) > 1 else "low"
        return AssociationResult(
            classification="associated",
            confidence=confidence,
            evidence=evidence,
            ineligible_reason="heuristic association only (no ForgeOps registry record) - never eligible for termination",
        )

    # No positive evidence either way. A process whose command line
    # merely could not be *read* (common for privileged system processes
    # when running unelevated) is not "uncertain" - it's indistinguishable
    # from "unrelated" for reporting purposes, since neither is ever a
    # cleanup candidate regardless (only `managed`, an exact registry
    # match, is). Reserving "uncertain" for genuinely conflicting/partial
    # registry evidence (handled above) keeps process-list's output
    # focused instead of listing every unreadable system process on the
    # machine as a false positive worth reviewing.
    return AssociationResult(
        classification="unrelated",
        confidence="low",
        evidence=[],
        ineligible_reason="no association evidence",
    )
