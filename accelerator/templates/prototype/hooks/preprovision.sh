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

# ── Step 2: Ensure Microsoft.App provider is registered ──────────────────────
# Container Apps is the runtime target; azd fails silently later if the
# provider is not registered on this subscription. Model TPM quota, AI
# Search regional capacity, and Cosmos regional AZ support are now checked
# in accelerator/generators/preflight.py before this hook ever runs; we
# leave provider registration here as a self-healing safety net for shells
# where preflight was skipped.
echo "2️⃣  Ensuring Microsoft.App provider is registered..."
CA_CHECK=$(az provider show \
  --namespace Microsoft.App \
  --query "registrationState" \
  --output tsv 2>/dev/null)
if [ "$CA_CHECK" != "Registered" ]; then
  echo "   ⚠  Microsoft.App not registered — registering..."
  az provider register --namespace Microsoft.App --wait --output none
fi
echo "   ✅ Microsoft.App registered"
echo ""

# ── Step 3: Create or confirm resource group ──────────────────────────────────
echo "3️⃣  Ensuring resource group exists..."
az group create \
  --name "$RESOURCE_GROUP" \
  --location "$REGION" \
  --output none 2>/dev/null || true
echo "   ✅ Resource group ready: $RESOURCE_GROUP"
echo ""

# ── Step 4: Bicep what-if validation ──────────────────────────────────────────
# Devlead sets SKIP_PREPROV_WHATIF=true via `azd env set` because Phase 2
# preflight (accelerator/generators/preflight.py) already ran what-if before
# `azd up` fired. When the user invokes `azd up` outside devlead, the var is
# unset and this belt-and-braces check still runs.
if [ "${SKIP_PREPROV_WHATIF:-false}" = "true" ]; then
  echo "4️⃣  Skipping Bicep what-if (preflight already validated)"
  echo ""
else
  echo "4️⃣  Running Bicep what-if validation..."
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
fi

# ── Done ──────────────────────────────────────────────────────────────────────
echo "✅ All pre-provision checks passed."
echo "   Proceeding with deployment..."
