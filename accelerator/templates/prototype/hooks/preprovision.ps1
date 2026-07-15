# ai-prototype-accelerator — Pre-Provision Validation (Windows/PowerShell)
# PowerShell equivalent of preprovision.sh for Windows environments without WSL.
# Static scaffold file — do NOT modify when processing spec.yaml.
$ErrorActionPreference = 'Stop'

Write-Host "╔══════════════════════════════════════╗"
Write-Host "║   ai-prototype-accelerator        ║"
Write-Host "║   Pre-Provision Validation           ║"
Write-Host "╚══════════════════════════════════════╝"
Write-Host ""

$REGION         = $env:AZURE_LOCATION
$RESOURCE_GROUP = $env:AZURE_RESOURCE_GROUP
# AI model deployments may go to a different region than general resources.
# AZURE_AI_LOCATION is set via 'azd env set AZURE_AI_LOCATION eastus' (or via bicepparam).
# Falls back to AZURE_LOCATION if not set.
$AI_REGION      = if ($env:AZURE_AI_LOCATION) { $env:AZURE_AI_LOCATION } else { $REGION }

# ── Step 1: Validate required env vars ────────────────────────────────────────
Write-Host "1️⃣  Validating environment..."
$REQUIRED_VARS = @("AZURE_LOCATION", "AZURE_RESOURCE_GROUP", "AZURE_SUBSCRIPTION_ID")
$ENV_FAILED = $false
foreach ($var in $REQUIRED_VARS) {
    $val = [System.Environment]::GetEnvironmentVariable($var)
    if ([string]::IsNullOrEmpty($val)) {
        Write-Host "   ❌ Missing required env var: $var"
        Write-Host "      Run: azd env set $var <value>"
        $ENV_FAILED = $true
    }
}
if ($ENV_FAILED) { exit 1 }
Write-Host "   ✅ Environment valid"
Write-Host ""

# ── Step 2: Ensure Microsoft.App provider is registered ──────────────────────
# Container Apps is the runtime target; azd fails silently later if the
# provider is not registered on this subscription. Model TPM quota, AI
# Search regional capacity, and Cosmos regional AZ support are now checked
# in accelerator/generators/preflight.py before this hook ever runs; we
# leave provider registration here as a self-healing safety net for shells
# where preflight was skipped.
Write-Host "2️⃣  Ensuring Microsoft.App provider is registered..."
$CA_CHECK = az provider show `
    --namespace Microsoft.App `
    --query "registrationState" `
    --output tsv 2>$null

if ($CA_CHECK -ne "Registered") {
    Write-Host "   ⚠  Microsoft.App not registered — registering..."
    az provider register --namespace Microsoft.App --wait --output none
}
Write-Host "   ✅ Microsoft.App registered"
Write-Host ""

# ── Step 3: Create or confirm resource group ──────────────────────────────────
Write-Host "3️⃣  Ensuring resource group exists..."
az group create `
    --name $RESOURCE_GROUP `
    --location $REGION `
    --output none 2>$null
Write-Host "   ✅ Resource group ready: $RESOURCE_GROUP"
Write-Host ""

# ── Step 4: Bicep what-if validation ──────────────────────────────────────────
# Devlead sets SKIP_PREPROV_WHATIF=true via `azd env set` because Phase 2
# preflight (accelerator/generators/preflight.py) already ran what-if before
# `azd up` fired. When the user invokes `azd up` outside devlead, the var is
# unset and this belt-and-braces check still runs.
$SKIP_WHATIF = $env:SKIP_PREPROV_WHATIF
if ($SKIP_WHATIF -eq "true") {
    Write-Host "4️⃣  Skipping Bicep what-if (preflight already validated)"
    Write-Host ""
} else {
    Write-Host "4️⃣  Running Bicep what-if validation..."
    $whatIfResult = az deployment group what-if `
        --resource-group $RESOURCE_GROUP `
        --template-file infra/main.bicep `
        --parameters infra/main.bicepparam `
        --result-format FullResourcePayloads `
        --output table

    $WHATIF_EXIT = $LASTEXITCODE
    Write-Host $whatIfResult

    if ($WHATIF_EXIT -ne 0) {
        Write-Host ""
        Write-Host "   ❌ Bicep what-if failed."
        Write-Host "   Fix errors above before running azd up again."
        exit 1
    }
    Write-Host ""
    Write-Host "   ✅ Bicep what-if passed"
    Write-Host ""
}

# ── Done ──────────────────────────────────────────────────────────────────────
Write-Host "✅ All pre-provision checks passed."
Write-Host "   Proceeding with deployment..."
