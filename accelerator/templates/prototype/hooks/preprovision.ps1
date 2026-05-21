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

# ── Step 2: Quota check — runs BEFORE Bicep what-if ──────────────────────────
# Models injected by Copilot from spec.yaml Section 5b
# Format: "model-name:required-capacity-in-k-tpm"
Write-Host "2️⃣  Checking model quota in $AI_REGION..."
$MODELS = @(
    "gpt-4o:10",
    "gpt-4o-mini:10",
    "gpt-4-1:10"
)
$QUOTA_FAILED = $false

foreach ($entry in $MODELS) {
    $parts    = $entry -split ':'
    $MODEL    = $parts[0]
    $REQUIRED = [int]$parts[1]

    Write-Host "   Checking $MODEL (required: ${REQUIRED}k TPM)..."

    $AVAILABLE = az cognitiveservices usage list `
        --location $AI_REGION `
        --query "[?contains(name.value, '$MODEL')].limit" `
        --output tsv 2>$null | Select-Object -First 1

    $CURRENT = az cognitiveservices usage list `
        --location $AI_REGION `
        --query "[?contains(name.value, '$MODEL')].currentValue" `
        --output tsv 2>$null | Select-Object -First 1

    if ([string]::IsNullOrEmpty($AVAILABLE)) {
        Write-Host "   ⚠️  $MODEL — quota data unavailable, proceeding"
    } else {
        $REMAINING = [int]$AVAILABLE - [int]$CURRENT
        if ($REMAINING -lt $REQUIRED) {
            Write-Host "   ❌ $MODEL — insufficient quota"
            Write-Host "      Available: ${AVAILABLE}k, Used: ${CURRENT}k, Remaining: ${REMAINING}k, Required: ${REQUIRED}k"
            $QUOTA_FAILED = $true
        } else {
            Write-Host "   ✅ $MODEL — quota OK (${REMAINING}k remaining)"
        }
    }
}

if ($QUOTA_FAILED) {
    Write-Host ""
    Write-Host "❌ Quota check failed. Options:"
    Write-Host "   1. Change azure_region in spec.yaml"
    Write-Host "   2. Request quota: aka.ms/oai-quota"
    Write-Host "   3. Reduce model capacity in spec.yaml Section 5b"
    exit 1
}
Write-Host "✅ Model quota check complete"
Write-Host ""

# ── Step 3: Regional service availability ─────────────────────────────────────
Write-Host "3️⃣  Checking service availability in $REGION..."

$CA_CHECK = az provider show `
    --namespace Microsoft.App `
    --query "registrationState" `
    --output tsv 2>$null

if ($CA_CHECK -ne "Registered") {
    Write-Host "   ⚠  Microsoft.App not registered — registering..."
    az provider register --namespace Microsoft.App --wait --output none
}
Write-Host "   ✅ Services available in $REGION"
Write-Host ""

# ── Step 4: Create or confirm resource group ──────────────────────────────────
Write-Host "4️⃣  Ensuring resource group exists..."
az group create `
    --name $RESOURCE_GROUP `
    --location $REGION `
    --output none 2>$null
Write-Host "   ✅ Resource group ready: $RESOURCE_GROUP"
Write-Host ""

# ── Step 5: Bicep what-if validation ──────────────────────────────────────────
Write-Host "5️⃣  Running Bicep what-if validation..."
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

# ── Done ──────────────────────────────────────────────────────────────────────
Write-Host "✅ All pre-provision checks passed."
Write-Host "   Proceeding with deployment..."
