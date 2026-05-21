# ai-prototype-accelerator — Generated Prototype Reset
# Accelerator-maintained utility script.

param(
    [switch]$Force
)

Write-Host "╔══════════════════════════════════════╗"
Write-Host "║   ai-prototype-accelerator          ║"
Write-Host "║   Generated Prototype Reset         ║"
Write-Host "╚══════════════════════════════════════╝"
Write-Host ""
Write-Host "This will remove all emitted prototype files from"
Write-Host "the previous accelerator build."
Write-Host ""
Write-Host "The following will be PRESERVED:"
Write-Host "  accelerator/"
Write-Host "  .github/"
Write-Host "  docs and root metadata"
Write-Host "  README.md"
Write-Host "  spec.yaml (your use case spec)"
Write-Host ""
Write-Host "The following will be REMOVED:"
Write-Host "  generated/prototype/*"
Write-Host "  generated/build-state/*"
Write-Host ""

if (-not $Force) {
    $confirm = Read-Host "Are you sure? Type 'yes' to confirm"
    if ($confirm -ne 'yes') {
        Write-Host ""
        Write-Host "Cancelled. No files removed."
        exit 0
    }
}

Write-Host ""
Write-Host "Removing generated files..."

if (Test-Path "generated/prototype") {
    Get-ChildItem "generated/prototype" -Force | Remove-Item -Recurse -Force
}
if (Test-Path "generated/build-state") {
    Get-ChildItem "generated/build-state" -Force | Remove-Item -Recurse -Force
}

New-Item -ItemType Directory -Force "generated/prototype/agents/specialists" | Out-Null
New-Item -ItemType Directory -Force "generated/prototype/agents/knowledge" | Out-Null
New-Item -ItemType Directory -Force "generated/build-state" | Out-Null
New-Item -ItemType File -Force "generated/prototype/agents/specialists/.gitkeep" | Out-Null
New-Item -ItemType File -Force "generated/prototype/agents/knowledge/.gitkeep" | Out-Null
New-Item -ItemType File -Force "generated/build-state/.gitkeep" | Out-Null

Write-Host "   Generated files removed"
Write-Host ""
Write-Host "Reset complete."
Write-Host ""
Write-Host "Next steps:"
Write-Host "  1. Update spec.yaml for the new idea"
Write-Host "  2. Open GitHub Copilot Chat"
Write-Host "  3. Run: @devlead build"
Write-Host "  4. Deploy from generated/prototype"
