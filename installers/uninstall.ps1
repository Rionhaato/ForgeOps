#Requires -Version 5.1
<#
Placeholder for the ForgeOps Claude/Codex plugin uninstaller.
Full implementation (restore-from-backup, remove only what install.ps1
added) lands in Phase 12.
#>
param(
    [switch]$DryRun,
    [string]$TargetPath
)

Write-Error "forgeops uninstaller: not yet implemented (Phase 12 of the ForgeOps build). See .agent/HANDOFF.md for current progress."
exit 1
