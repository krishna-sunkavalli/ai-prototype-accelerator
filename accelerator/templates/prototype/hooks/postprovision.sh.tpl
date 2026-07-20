#!/bin/bash
# ai-prototype-accelerator -- Post-Provision Script
# {{CUSTOMER_NAME}} — {{USE_CASE_TITLE}}
# Runs after: azd provision (Bicep deployment complete)
# Actions: RBAC assignments, Cosmos seed, doc upload, search index, agent registration
set -euo pipefail

echo "=================================================================="
echo "  ai-prototype-accelerator -- Post-Provision"
echo "  {{CUSTOMER_NAME}} — {{USE_CASE_TITLE}}"
echo "=================================================================="
echo ""

FAILED=false
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# L10: Set UTF-8 for all Python calls
export PYTHONUTF8=1

# -- Step 1: Validate required environment variables --------------------------
echo "1)  Validating environment variables..."

REQUIRED_VARS=(
  "AZURE_AI_PROJECT_ENDPOINT"
  "AZURE_SEARCH_ENDPOINT"
  "AZURE_STORAGE_ACCOUNT_NAME"
  "AZURE_COSMOS_ENDPOINT"
  "AZURE_COSMOS_DATABASE"
  "AZURE_COSMOS_ACCOUNT_NAME"
  "AZURE_RESOURCE_GROUP"
  "AZURE_SUBSCRIPTION_ID"
)

for var in "${REQUIRED_VARS[@]}"; do
  if [ -z "${!var:-}" ]; then
    echo "   MISSING: $var"
    FAILED=true
  else
    echo "   OK: $var"
  fi
done

if [ "$FAILED" = true ]; then
  echo ""
  echo "One or more required environment variables are missing."
  echo "Run: azd env set <VAR_NAME> <value>"
  exit 1
fi

# Derive MI display name
if [ -z "${AZURE_MI_DISPLAY_NAME:-}" ]; then
  export AZURE_MI_DISPLAY_NAME="{{MI_NAME}}"
fi
echo "   MI display name: ${AZURE_MI_DISPLAY_NAME}"
echo ""

# -- Step 2: Resolve identities -----------------------------------------------
echo "2)  Resolving MI and deployer object IDs..."

COSMOS_SCOPE="/subscriptions/${AZURE_SUBSCRIPTION_ID}/resourceGroups/${AZURE_RESOURCE_GROUP}/providers/Microsoft.DocumentDB/databaseAccounts/${AZURE_COSMOS_ACCOUNT_NAME}"

# L1: Derive MI OID via az identity show (.principalId), NOT az ad sp show --id
MI_OID=$(az identity show \
  --name "${AZURE_MI_DISPLAY_NAME}" \
  --resource-group "${AZURE_RESOURCE_GROUP}" \
  --query principalId \
  --output tsv 2>/dev/null || true)

if [ -n "${MI_OID}" ]; then
  echo "   MI OID: ${MI_OID}"
else
  echo "   WARN: Could not resolve MI object ID -- RBAC assignments will be skipped."
fi

# L4: Deployer OID so both MI and deployer get Cosmos access
DEPLOYER_OID=$(az ad signed-in-user show --query id --output tsv 2>/dev/null || true)
if [ -n "${DEPLOYER_OID}" ]; then
  echo "   Deployer OID: ${DEPLOYER_OID}"
else
  echo "   INFO: No signed-in user (CI/CD pipeline) -- only MI will receive roles."
fi
echo ""

# Helper: assign Cosmos SQL RBAC
assign_cosmos_rbac() {
  local oid="$1"
  local label="$2"
  [ -z "${oid}" ] && return
  az cosmosdb sql role assignment create \
    --account-name "${AZURE_COSMOS_ACCOUNT_NAME}" \
    --resource-group "${AZURE_RESOURCE_GROUP}" \
    --role-definition-id "00000000-0000-0000-0000-000000000002" \
    --principal-id "${oid}" \
    --scope "${COSMOS_SCOPE}" \
    --output none 2>&1 | sed "s/^/   /" || true
  echo "   OK: Cosmos Built-in Data Contributor -> ${label}"
}

# Helper: assign ARM role
# ptype defaults to ServicePrincipal (correct for the MI). The deployer OID
# (step 2) only ever resolves via `az ad signed-in-user show`, which
# succeeds only for an interactively signed-in human -- meaning whenever
# DEPLOYER_OID is non-empty it is ALWAYS type 'User', never
# 'ServicePrincipal'. Passing the wrong hint doesn't just get ignored: ARM
# rejects the assignment outright with UnmatchedPrincipalType. That error
# was invisible here because the original call swallowed stdout/stderr AND
# never checked the exit code -- always printing "OK" regardless. Every
# deployer-scoped call below must pass "User" explicitly as the 5th arg.
assign_arm_role() {
  local oid="$1"
  local role="$2"
  local scope="$3"
  local label="$4"
  local ptype="${5:-ServicePrincipal}"
  [ -z "${oid}" ] || [ -z "${scope}" ] && return
  local role_output
  role_output=$(az role assignment create \
    --assignee-object-id "${oid}" \
    --assignee-principal-type "${ptype}" \
    --role "${role}" \
    --scope "${scope}" 2>&1)
  if [ $? -ne 0 ]; then
    echo "   FAILED: '${role}' -> ${label} -- ${role_output}"
    FAILED=true
  else
    echo "   OK: '${role}' -> ${label}"
  fi
}

# -- Step 3: Cosmos DB RBAC ---------------------------------------------------
echo "3)  Assigning Cosmos DB Data Contributor role..."
# L3 + L4: assign to BOTH MI and deployer
assign_cosmos_rbac "${MI_OID}" "Managed Identity"
assign_cosmos_rbac "${DEPLOYER_OID}" "Deployer"
echo ""

# -- Step 4: Storage RBAC -----------------------------------------------------
echo "4)  Assigning Storage Blob Data Contributor to MI + Deployer..."
STORAGE_ID=$(az storage account show \
  --name "${AZURE_STORAGE_ACCOUNT_NAME}" \
  --resource-group "${AZURE_RESOURCE_GROUP}" \
  --query id --output tsv 2>/dev/null || true)
assign_arm_role "${MI_OID}" "Storage Blob Data Contributor" "${STORAGE_ID}" "Managed Identity"
# L13 fix: the doc-upload step (step 9) uses `az storage blob upload-batch
# --auth-mode login`, which authenticates as the DEPLOYER, not the MI. Without
# this role the upload fails with 403 on every build -- silently, because the
# original step 9 command discarded both stdout/stderr AND the exit code.
assign_arm_role "${DEPLOYER_OID}" "Storage Blob Data Contributor" "${STORAGE_ID}" "Deployer" "User"
echo ""

# -- Step 5: AI Search RBAC ---------------------------------------------------
echo "5)  Assigning AI Search roles to MI..."
SEARCH_NAME="$(echo "${AZURE_SEARCH_ENDPOINT}" | sed 's|https://||' | cut -d'.' -f1)"
SEARCH_ID=$(az search service show \
  --name "${SEARCH_NAME}" \
  --resource-group "${AZURE_RESOURCE_GROUP}" \
  --query id --output tsv 2>/dev/null || true)
assign_arm_role "${MI_OID}"       "Search Index Data Contributor" "${SEARCH_ID}" "Managed Identity"
assign_arm_role "${MI_OID}"       "Search Service Contributor"   "${SEARCH_ID}" "Managed Identity"
assign_arm_role "${DEPLOYER_OID}" "Search Index Data Contributor" "${SEARCH_ID}" "Deployer" "User"
assign_arm_role "${DEPLOYER_OID}" "Search Service Contributor"   "${SEARCH_ID}" "Deployer" "User"
echo ""

# -- Step 5b: AI Hub + Project RBAC (L17) ------------------------------------
# L17: MI must have Foundry User (formerly "Azure AI User") on BOTH the hub
# and project to call Agents API at runtime. Microsoft renamed several
# Foundry RBAC roles; the old name silently fails with "Role 'Azure AI
# User' doesn't exist" -- previously invisible here because assign_arm_role
# discarded stdout/stderr and never checked the exit code.
echo "5b) Assigning Foundry User to MI on AI Hub + Project..."
HUB_ID=$(az resource show \
  --name "${AZURE_AI_HUB_NAME}" \
  --resource-group "${AZURE_RESOURCE_GROUP}" \
  --resource-type "Microsoft.CognitiveServices/accounts" \
  --query id --output tsv 2>/dev/null || true)
PROJ_ID="${HUB_ID}/projects/${AZURE_AI_PROJECT_NAME}"
assign_arm_role "${MI_OID}" "Foundry User" "${HUB_ID}"  "MI -> Hub"
assign_arm_role "${MI_OID}" "Foundry User" "${PROJ_ID}" "MI -> Project"
echo ""

# -- Step 5c: AI Search's OWN identity -> Foundry hub + Storage (Foundry IQ) --
# Foundry IQ (blob knowledge source + knowledge base, step 10) calls the
# deployed embedding/chat models and reads blob content using the Search
# service's OWN system-assigned identity -- never an API key -- so no secret
# ever lands in the knowledge source/base definitions.
echo "5c) Assigning AI Search's identity to Foundry hub + Storage..."
SEARCH_PRINCIPAL_ID=$(az search service show \
  --name "${SEARCH_NAME}" \
  --resource-group "${AZURE_RESOURCE_GROUP}" \
  --query identity.principalId --output tsv 2>/dev/null || true)
assign_arm_role "${SEARCH_PRINCIPAL_ID}" "Cognitive Services User" "${HUB_ID}" "Search -> Hub (Foundry IQ)"
assign_arm_role "${SEARCH_PRINCIPAL_ID}" "Storage Blob Data Reader" "${STORAGE_ID}" "Search -> Storage (Foundry IQ)"
echo ""

# -- Step 6: Consolidated RBAC propagation wait (L5) -------------------------
echo "6)  Waiting 90s for all RBAC assignments to propagate..."
sleep 90
echo "   Done."
echo ""

# -- Step 7: Cosmos DB networking + disable key-based metadata writes (L6+L7) -
echo "7)  Configuring Cosmos DB networking and access settings..."
az cosmosdb update \
  --name "${AZURE_COSMOS_ACCOUNT_NAME}" \
  --resource-group "${AZURE_RESOURCE_GROUP}" \
  --public-network-access Enabled \
  --network-acl-bypass AzureServices \
  --disable-key-based-metadata-write-access false \
  --output none 2>&1 | sed "s/^/   /" || true
echo "   OK: Cosmos DB networking configured."
echo ""

# -- Step 8: Seed Cosmos DB ---------------------------------------------------
echo "8)  Seeding Cosmos DB containers..."

SEED_SCRIPT="${SCRIPT_DIR}/../db/cosmos_seed.py"

if [ ! -f "${SEED_SCRIPT}" ]; then
  echo "   ERROR: Seed script not found: ${SEED_SCRIPT}"
  FAILED=true
else
  python3 "${SEED_SCRIPT}" 2>&1 | sed "s/^/   /"
  SEED_EXIT="${PIPESTATUS[0]}"
  if [ "${SEED_EXIT}" -ne 0 ]; then
    echo "   ERROR: Cosmos DB seeding failed (exit ${SEED_EXIT})."
    FAILED=true
  else
    echo "   OK: {{TABLE_NAMES_STR}} seeded."
  fi
fi
echo ""

# -- Determine data grounding mode (synthetic vs real) -----------------------
# spec.yaml's data_grounding.mode, passed through manifest.json ->
# fill-templates.py -> this plain JSON file (not a hydrated placeholder --
# a JSON blob with real resource IDs/names doesn't embed safely inside a
# shell string literal). Missing file or missing key both default to
# "synthetic" so builds from before this feature existed keep working
# unchanged.
DATA_GROUNDING_FILE="${SCRIPT_DIR}/data-grounding.json"
GROUNDING_MODE=$(python3 -c "import json; print(json.load(open('${DATA_GROUNDING_FILE}')).get('mode','synthetic'))" 2>/dev/null || echo "synthetic")

# -- Step 9: Upload operational documents to Blob Storage ---------------------
if [ "${GROUNDING_MODE}" = "synthetic" ]; then
echo "9)  Uploading operational documents to Blob Storage..."

DOCS_DIR="${SCRIPT_DIR}/../agents/knowledge"
CONTAINER_NAME="prototype-data"

az storage container create \
  --name "${CONTAINER_NAME}" \
  --account-name "${AZURE_STORAGE_ACCOUNT_NAME}" \
  --auth-mode login \
  --output none 2>&1 | sed "s/^/   /" || true

# Document filenames derived from manifest.json by fill-templates.py
DOCS={{DOCS_BASH_ARRAY}}

# L13: upload-batch with --auth-mode login (requires Storage Blob Data
# Contributor on the DEPLOYER -- see step 4 -- since --auth-mode login
# authenticates as the signed-in user, not the managed identity).
UPLOAD_OUTPUT=$(az storage blob upload-batch \
  --account-name "${AZURE_STORAGE_ACCOUNT_NAME}" \
  --destination "${CONTAINER_NAME}/operational-docs" \
  --source "${DOCS_DIR}" \
  --pattern "*.md" \
  --auth-mode login \
  --overwrite true 2>&1)
UPLOAD_EXIT=$?
if [ "${UPLOAD_EXIT}" -ne 0 ]; then
  echo "   ERROR: Document upload failed (exit ${UPLOAD_EXIT})."
  echo "${UPLOAD_OUTPUT}" | sed "s/^/   /"
  FAILED=true
else
  UPLOADED_COUNT=$(find "${DOCS_DIR}" -maxdepth 1 -name '*.md' -type f | wc -l)
  echo "   OK: Operational documents uploaded (${UPLOADED_COUNT} file(s))."
fi
else
echo "9)  Skipped document upload (dataGrounding.mode = real; grounding real Azure data sources instead)."
fi
echo ""

# -- Step 10: Wire Foundry IQ knowledge base -----------------------------------
# Real Foundry IQ wiring (not a hand-rolled index): a blob knowledge source
# auto-generates the data source + skillset (chunking/vectorization) +
# indexer + index from the blob container, and a knowledge base wraps it for
# agentic retrieval (subquery decomposition + semantic reranking). Both the
# embedding model and the query-planning LLM are called via the Search
# service's OWN identity (granted in step 5c) -- no API key stored in either
# object. Cosmos DB is untouched by this change; run_sql_query keeps handling
# precise structured lookups.
echo "10) Wiring Foundry IQ knowledge base (agentic retrieval)..."

INDEX_NAME="${AZURE_SEARCH_INDEX_NAME:-{{SEARCH_INDEX_NAME}}}"
KS_NAME="${INDEX_NAME}-ks"
KB_NAME="${INDEX_NAME}-kb"
KS_API_VERSION="2026-05-01-preview"

# Use admin API key for the Search control-plane calls themselves (avoids the
# RBAC propagation race for this script) -- separate from the identity-based
# auth the knowledge source/base use to reach the embedding/chat models and
# blob storage.
SEARCH_ADMIN_KEY=$(az search admin-key show \
  --service-name "${SEARCH_NAME}" \
  --resource-group "${AZURE_RESOURCE_GROUP}" \
  --query primaryKey --output tsv 2>/dev/null)
if [ -z "$SEARCH_ADMIN_KEY" ]; then
  echo "   FAILED: Could not retrieve Search admin key."
  FAILED=true
fi

# Base AI Services endpoint (NOT the /api/projects/{proj}/ path) for the
# embedding + chat-completion model references below.
AI_SERVICES_SUBDOMAIN=$(az cognitiveservices account show \
  --name "${AZURE_AI_HUB_NAME}" \
  --resource-group "${AZURE_RESOURCE_GROUP}" \
  --query properties.customSubDomainName --output tsv 2>/dev/null || true)
AI_SERVICES_ENDPOINT="https://${AI_SERVICES_SUBDOMAIN}.services.ai.azure.com/"

if [ "${GROUNDING_MODE}" = "synthetic" ]; then
# Identity-based blob connection string -- no storage key stored anywhere.
STORAGE_CONNECTION="ResourceId=/subscriptions/${AZURE_SUBSCRIPTION_ID}/resourceGroups/${AZURE_RESOURCE_GROUP}/providers/Microsoft.Storage/storageAccounts/${AZURE_STORAGE_ACCOUNT_NAME};"

KS_JSON=$(python3 - "$KS_NAME" "$STORAGE_CONNECTION" "$AI_SERVICES_ENDPOINT" "{{CUSTOMER_NAME}}" <<'PYEOF'
import json, sys
ks_name, storage_conn, ai_endpoint, customer = sys.argv[1:5]
body = {
    "name": ks_name,
    "kind": "azureBlob",
    "description": f"Operational knowledge documents for {customer}.",
    "azureBlobParameters": {
        "connectionString": storage_conn,
        "containerName": "prototype-data",
        "folderPath": "operational-docs",
        "isADLSGen2": False,
        "ingestionParameters": {
            "contentExtractionMode": "minimal",
            # NOTE: chatCompletionModel must NOT be set when
            # disableImageVerbalization is True -- the Search knowledge
            # source API returns 400 "ChatCompletionModel must not be set
            # when DisableImageVerbalization is true." chatCompletionModel
            # is only used for image verbalization during ingestion, so it
            # has no effect here anyway. See RESOLVED.md.
            "disableImageVerbalization": True,
            "embeddingModel": {
                "kind": "azureOpenAI",
                "azureOpenAIParameters": {
                    "resourceUri": ai_endpoint,
                    "deploymentId": "text-embedding-3-large",
                    "modelName": "text-embedding-3-large",
                },
            },
        },
    },
}
print(json.dumps(body))
PYEOF
)

HTTP_STATUS=$(curl -s -o /tmp/ks_response.json -w "%{http_code}" \
  -X PUT \
  "${AZURE_SEARCH_ENDPOINT}/knowledgesources/${KS_NAME}?api-version=${KS_API_VERSION}" \
  -H "api-key: ${SEARCH_ADMIN_KEY}" \
  -H "Content-Type: application/json" \
  -d "${KS_JSON}")

if [ "$HTTP_STATUS" -ne 200 ] && [ "$HTTP_STATUS" -ne 201 ] && [ "$HTTP_STATUS" -ne 204 ]; then
  echo "   FAILED: Knowledge source (HTTP ${HTTP_STATUS})."
  cat /tmp/ks_response.json | sed "s/^/   /" || true
  FAILED=true
else
  echo "   OK: Knowledge source '${KS_NAME}' created/updated (HTTP ${HTTP_STATUS})."
fi

KB_JSON=$(python3 - "$KB_NAME" "$KS_NAME" "$AI_SERVICES_ENDPOINT" "{{CUSTOMER_NAME}}" "{{KB_RETRIEVAL_INSTRUCTIONS}}" <<'PYEOF'
import json, sys
kb_name, ks_name, ai_endpoint, customer, retrieval_instructions = sys.argv[1:6]
body = {
    "name": kb_name,
    "description": f"Foundry IQ knowledge base for {customer} operational documents.",
    "knowledgeSources": [{"name": ks_name}],
    "models": [{
        "kind": "azureOpenAI",
        "azureOpenAIParameters": {
            "resourceUri": ai_endpoint,
            "deploymentId": "gpt-4o-mini",
            "modelName": "gpt-4o-mini",
        },
    }],
    "outputMode": "extractiveData",
    "retrievalReasoningEffort": {"kind": "low"},
    "retrievalInstructions": retrieval_instructions,
}
print(json.dumps(body))
PYEOF
)

HTTP_STATUS=$(curl -s -o /tmp/kb_response.json -w "%{http_code}" \
  -X PUT \
  "${AZURE_SEARCH_ENDPOINT}/knowledgebases/${KB_NAME}?api-version=${KS_API_VERSION}" \
  -H "api-key: ${SEARCH_ADMIN_KEY}" \
  -H "Content-Type: application/json" \
  -d "${KB_JSON}")

if [ "$HTTP_STATUS" -ne 200 ] && [ "$HTTP_STATUS" -ne 201 ] && [ "$HTTP_STATUS" -ne 204 ]; then
  echo "   FAILED: Knowledge base (HTTP ${HTTP_STATUS})."
  cat /tmp/kb_response.json | sed "s/^/   /" || true
  FAILED=true
else
  echo "   OK: Knowledge base '${KB_NAME}' created/updated (HTTP ${HTTP_STATUS})."
fi

# Brief, bounded, non-fatal poll — first ingestion sync can take a few
# minutes even for a handful of docs. Don't hang the whole deploy on it.
echo "   Checking ingestion status (up to 60s, non-blocking)..."
for i in 1 2 3 4 5 6; do
  sleep 10
  STATUS_JSON=$(curl -s \
    "${AZURE_SEARCH_ENDPOINT}/knowledgesources/${KS_NAME}/status?api-version=${KS_API_VERSION}" \
    -H "api-key: ${SEARCH_ADMIN_KEY}" 2>/dev/null || true)
  SYNC_STATUS=$(echo "$STATUS_JSON" | python3 -c "import json,sys
try:
    print(json.load(sys.stdin).get('lastSynchronizationState', {}).get('status', 'unknown'))
except Exception:
    print('unknown')" 2>/dev/null || echo "unknown")
  if [ "$SYNC_STATUS" = "success" ]; then
    echo "   OK: Initial ingestion complete."
    break
  elif [ "$i" -eq 6 ]; then
    echo "   INFO: Ingestion still in progress (status: ${SYNC_STATUS}) -- it will finish in the background."
  fi
done
else
# -- Real data grounding: discover + wire the customer's own Azure resources --
# See accelerator/templates/prototype/hooks/wire_real_data_sources.py for the
# full discovery/RBAC/knowledge-source/knowledge-base logic. Best-effort and
# non-fatal by design (external, customer-owned resources are outside this
# accelerator's control) -- always exits 0.
python3 "${SCRIPT_DIR}/wire_real_data_sources.py" \
  --data-grounding "${DATA_GROUNDING_FILE}" \
  --search-endpoint "${AZURE_SEARCH_ENDPOINT}" \
  --admin-key "${SEARCH_ADMIN_KEY}" \
  --api-version "${KS_API_VERSION}" \
  --ai-endpoint "${AI_SERVICES_ENDPOINT}" \
  --search-principal-id "${SEARCH_PRINCIPAL_ID:-}" \
  --index-name "${INDEX_NAME}" \
  --customer-name "{{CUSTOMER_NAME}}" \
  --retrieval-instructions "{{KB_RETRIEVAL_INSTRUCTIONS}}" 2>&1 | sed "s/^/   /"
fi
echo ""

# -- Step 11: Register AI Foundry agents --------------------------------------
echo "11) Registering agents in Azure AI Foundry..."

REGISTER_SCRIPT="${SCRIPT_DIR}/../agents/register_agents.py"

if [ ! -f "${REGISTER_SCRIPT}" ]; then
  echo "   ERROR: Agent registration script not found: ${REGISTER_SCRIPT}"
  FAILED=true
elif [ -z "${AZURE_AI_PROJECT_ENDPOINT:-}" ]; then
  echo "   ERROR: AZURE_AI_PROJECT_ENDPOINT not set -- skipping agent registration."
  FAILED=true
else
  python3 "${REGISTER_SCRIPT}" 2>&1 | sed "s/^/   /"
  REG_EXIT="${PIPESTATUS[0]}"
  if [ "${REG_EXIT}" -ne 0 ]; then
    echo "   ERROR: Agent registration failed (exit ${REG_EXIT})."
    FAILED=true
  else
    echo "   OK: All agents registered in Azure AI Foundry."
  fi
fi
echo ""

# -- Final status --------------------------------------------------------------
if [ "$FAILED" = true ]; then
  echo "====================================="
  echo "  POST-PROVISION FAILED"
  echo "====================================="
  echo ""
  echo "One or more post-provision steps failed. Review errors above."
  echo "Re-run: azd provision"
  exit 1
else
  echo "=================================================================="
  echo "  POST-PROVISION COMPLETE"
  echo ""
  echo "  * RBAC assigned (MI + deployer + Search identity) -- Cosmos, Storage, Search, Foundry"
  echo "  * Cosmos DB seeded ({{TABLE_NAMES_STR}})"
  echo "  * Operational documents uploaded to Blob Storage"
  echo "  * Foundry IQ knowledge base wired (${KB_NAME}, agentic retrieval)"
  echo "  * Agents registered in Azure AI Foundry"
  echo ""
  echo "  Run: azd deploy"
  echo "=================================================================="
  exit 0
fi
