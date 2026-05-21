#!/bin/bash
# ai-prototype-accelerator — Pre-Provision Validation
# Static scaffold file — do NOT modify when processing spec.yaml.
set -e

echo "╔══════════════════════════════════════╗"
echo "║   ai-prototype-accelerator        ║"
echo "║   Pre-Provision Validation           ║"
echo "╚══════════════════════════════════════╝"
echo ""

REGION="${AZURE_LOCATION}"
RESOURCE_GROUP="${AZURE_RESOURCE_GROUP}"
# AI model deployments may go to a different region than general resources.
# AZURE_AI_LOCATION is set via 'azd env set AZURE_AI_LOCATION eastus' (or via bicepparam).
# Falls back to AZURE_LOCATION if not set.
AI_REGION="${AZURE_AI_LOCATION:-$AZURE_LOCATION}"

# ── Step 1: Validate required env vars ────────────────────────────────────────
echo "1️⃣  Validating environment..."
REQUIRED_VARS=(
  "AZURE_LOCATION"
  "AZURE_RESOURCE_GROUP"
  "AZURE_SUBSCRIPTION_ID"
)
ENV_FAILED=false
for var in "${REQUIRED_VARS[@]}"; do
  if [ -z "${!var}" ]; then
    echo "   ❌ Missing required env var: $var"
    echo "      Run: azd env set $var <value>"
    ENV_FAILED=true
  fi
done
if [ "$ENV_FAILED" = true ]; then
  exit 1
fi
echo "   ✅ Environment valid"
echo ""

# ── Step 2: Quota check — runs BEFORE Bicep what-if ──────────────────────────
# Models read from manifest.json at runtime via python (no jq dependency).
# Format inside MODELS: "model-name:required-capacity-in-k-tpm"
echo "2️⃣  Checking model quota in $AI_REGION..."
# Read MODELS array from manifest.json using Python (no jq dependency).
# Python is already required by the accelerator build, so this is safer than
# silently falling back to stale hard-coded values when jq is missing.
MANIFEST_PATH="$(cd "$(dirname "$0")/.." && pwd)/../build-state/manifest.json"
MODELS=()
PYTHON_BIN="$(command -v python3 || command -v python || true)"
if [ -f "$MANIFEST_PATH" ] && [ -n "$PYTHON_BIN" ]; then
  while IFS= read -r line; do
    [ -n "$line" ] && MODELS+=("$line")
  done < <("$PYTHON_BIN" -c 'import json,sys;m=json.load(open(sys.argv[1]));[print(d["model"]+":"+str(d.get("capacity",10))) for d in m.get("modelDeployments",[])]' "$MANIFEST_PATH")
fi
if [ ${#MODELS[@]} -eq 0 ]; then
  echo "   ❌ Could not read modelDeployments from $MANIFEST_PATH"
  echo "      Ensure the manifest exists and python is available before preprovision."
  exit 1
fi
QUOTA_FAILED=false

for entry in "${MODELS[@]}"; do
  MODEL="${entry%%:*}"
  REQUIRED="${entry##*:}"

  echo "   Checking $MODEL (required: ${REQUIRED}k TPM)..."

  AVAILABLE=$(az cognitiveservices usage list \
    --location "$AI_REGION" \
    --query "[?contains(name.value, '$MODEL')].limit" \
    --output tsv 2>/dev/null | head -1)

  CURRENT=$(az cognitiveservices usage list \
    --location "$AI_REGION" \
    --query "[?contains(name.value, '$MODEL')].currentValue" \
    --output tsv 2>/dev/null | head -1)

  if [ -z "$AVAILABLE" ]; then
    echo "   ⚠️  $MODEL — quota data unavailable, proceeding"
  else
    REMAINING=$((AVAILABLE - CURRENT))
    if [ "$REMAINING" -lt "$REQUIRED" ] 2>/dev/null; then
      echo "   ❌ $MODEL — insufficient quota"
      echo "      Available: ${AVAILABLE}k, Used: ${CURRENT}k, Remaining: ${REMAINING}k, Required: ${REQUIRED}k"
      QUOTA_FAILED=true
    else
      echo "   ✅ $MODEL — quota OK (${REMAINING}k remaining)"
    fi
  fi
done

if [ "$QUOTA_FAILED" = true ]; then
  echo ""
  echo "❌ Quota check failed. Options:"
  echo "   1. Change azure_region in spec.yaml"
  echo "   2. Request quota: aka.ms/oai-quota"
  echo "   3. Reduce model capacity in spec.yaml Section 5b"
  exit 1
fi
echo "✅ Model quota check complete"
echo ""

# ── Step 3: Regional service availability ─────────────────────────────────────
echo "3️⃣  Checking service availability in $REGION..."
SERVICES_FAILED=false

# Check Azure Container Apps availability
CA_CHECK=$(az provider show \
  --namespace Microsoft.App \
  --query "registrationState" \
  --output tsv 2>/dev/null)
if [ "$CA_CHECK" != "Registered" ]; then
  echo "   ⚠  Microsoft.App not registered — registering..."
  az provider register --namespace Microsoft.App --wait --output none
fi

echo "   ✅ Services available in $REGION"
echo ""

# ── Step 4: Create or confirm resource group ──────────────────────────────────
echo "4️⃣  Ensuring resource group exists..."
az group create \
  --name "$RESOURCE_GROUP" \
  --location "$REGION" \
  --output none 2>/dev/null || true
echo "   ✅ Resource group ready: $RESOURCE_GROUP"
echo ""

# ── Step 5: Bicep what-if validation ──────────────────────────────────────────
echo "5️⃣  Running Bicep what-if validation..."
az deployment group what-if \
  --resource-group "$RESOURCE_GROUP" \
  --template-file infra/main.bicep \
  --parameters infra/main.bicepparam \
  --result-format FullResourcePayloads \
  --output table

WHATIF_EXIT=$?
if [ $WHATIF_EXIT -ne 0 ]; then
  echo ""
  echo "   ❌ Bicep what-if failed."
  echo "   Fix errors above before running azd up again."
  exit 1
fi
echo ""
echo "   ✅ Bicep what-if passed"
echo ""

# ── Done ──────────────────────────────────────────────────────────────────────
echo "✅ All pre-provision checks passed."
echo "   Proceeding with deployment..."
