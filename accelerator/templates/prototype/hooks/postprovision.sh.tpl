#!/bin/bash
# ai-prototype-accelerator -- Post-Provision Script
# {{CUSTOMER_NAME}} — {{USE_CASE_TITLE}}
# Runs after: azd provision (Bicep deployment complete)
# Actions: RBAC assignments, Cosmos seed, doc upload, search index, agent registration
set -euo pipefail

# Headless-driver overlap: build.py runs `azd provision` concurrently with
# the LLM generation steps, so at provision-complete time the seed script /
# agent definitions / knowledge docs may not exist yet. The driver sets
# BUILD_DEFER_HOOKS=true for that provision and re-runs this hook itself
# (azd hooks run postprovision) once generation has finished.
if [ "${BUILD_DEFER_HOOKS:-}" = "true" ]; then
  echo "postprovision: deferred — headless driver will run this after generation completes."
  exit 0
fi

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
assign_arm_role() {
  local oid="$1"
  local role="$2"
  local scope="$3"
  local label="$4"
  local ptype="${5:-ServicePrincipal}"
  [ -z "${oid}" ] || [ -z "${scope}" ] && return
  az role assignment create \
    --assignee-object-id "${oid}" \
    --assignee-principal-type "${ptype}" \
    --role "${role}" \
    --scope "${scope}" \
    --output none 2>&1 | sed "s/^/   /" || true
  echo "   OK: '${role}' -> ${label}"
}

# -- Step 3: Cosmos DB RBAC ---------------------------------------------------
echo "3)  Assigning Cosmos DB Data Contributor role..."
# L3 + L4: assign to BOTH MI and deployer
assign_cosmos_rbac "${MI_OID}" "Managed Identity"
assign_cosmos_rbac "${DEPLOYER_OID}" "Deployer"
echo ""

# -- Step 4: Storage RBAC -----------------------------------------------------
echo "4)  Assigning Storage Blob Data Contributor to MI..."
STORAGE_ID=$(az storage account show \
  --name "${AZURE_STORAGE_ACCOUNT_NAME}" \
  --resource-group "${AZURE_RESOURCE_GROUP}" \
  --query id --output tsv 2>/dev/null || true)
assign_arm_role "${MI_OID}" "Storage Blob Data Contributor" "${STORAGE_ID}" "Managed Identity"
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
assign_arm_role "${DEPLOYER_OID}" "Search Index Data Contributor" "${SEARCH_ID}" "Deployer"
assign_arm_role "${DEPLOYER_OID}" "Search Service Contributor"   "${SEARCH_ID}" "Deployer"
echo ""

# -- Step 5b: AI Hub + Project RBAC (L17) ------------------------------------
# L17: MI must have Azure AI User on BOTH the hub and project to call Agents API at runtime.
echo "5b) Assigning Azure AI User to MI on AI Hub + Project..."
HUB_ID=$(az resource show \
  --name "${AZURE_AI_HUB_NAME}" \
  --resource-group "${AZURE_RESOURCE_GROUP}" \
  --resource-type "Microsoft.CognitiveServices/accounts" \
  --query id --output tsv 2>/dev/null || true)
PROJ_ID="${HUB_ID}/projects/${AZURE_AI_PROJECT_NAME}"
assign_arm_role "${MI_OID}" "Azure AI User" "${HUB_ID}"  "MI -> Hub"
assign_arm_role "${MI_OID}" "Azure AI User" "${PROJ_ID}" "MI -> Project"
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

# -- Step 9: Upload operational documents to Blob Storage ---------------------
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

# L13: upload-batch with --auth-mode login
az storage blob upload-batch \
  --account-name "${AZURE_STORAGE_ACCOUNT_NAME}" \
  --destination "${CONTAINER_NAME}/operational-docs" \
  --source "${DOCS_DIR}" \
  --pattern "*.md" \
  --auth-mode login \
  --overwrite true \
  --output none 2>&1 | sed "s/^/   /" || true
echo "   OK: Operational documents uploaded."
echo ""

# -- Step 10: Create/update AI Search index and index documents ---------------
echo "10) Indexing operational documents in Azure AI Search..."

INDEX_NAME="${AZURE_SEARCH_INDEX_NAME:-{{SEARCH_INDEX_NAME}}}"
# Use admin API key — avoids RBAC propagation race; disableLocalAuth=false so keys are always available
SEARCH_SERVICE=$(echo "$AZURE_SEARCH_ENDPOINT" | sed 's|https://||' | cut -d. -f1)
SEARCH_ADMIN_KEY=$(az search admin-key show \
  --service-name "$SEARCH_SERVICE" \
  --resource-group "$AZURE_RESOURCE_GROUP" \
  --query primaryKey --output tsv 2>/dev/null)
if [ -z "$SEARCH_ADMIN_KEY" ]; then
  echo "   FAILED: Could not retrieve Search admin key."
  FAILED=true
fi

# L11: correct semantic config field names (prioritizedContentFields / prioritizedKeywordsFields)
INDEX_JSON=$(python3 - <<'PYEOF'
import json, os
name = os.environ.get("AZURE_SEARCH_INDEX_NAME", "{{SEARCH_INDEX_NAME}}")
schema = {
  "name": name,
  "fields": [
    {"name": "id", "type": "Edm.String", "key": True, "searchable": False},
    {"name": "content", "type": "Edm.String", "searchable": True, "analyzer": "en.microsoft"},
    {"name": "title", "type": "Edm.String", "searchable": True, "filterable": True},
    {"name": "filename", "type": "Edm.String", "searchable": False, "filterable": True},
    {"name": "category", "type": "Edm.String", "searchable": False, "filterable": True}
  ],
  "semantic": {
    "configurations": [{
      "name": "default",
      "prioritizedFields": {
        "prioritizedContentFields": [{"fieldName": "content"}],
        "prioritizedKeywordsFields": [{"fieldName": "title"}]
      }
    }]
  }
}
print(json.dumps(schema))
PYEOF
)

HTTP_STATUS=$(curl -s -o /tmp/search_index_response.json -w "%{http_code}" \
  -X PUT \
  "${AZURE_SEARCH_ENDPOINT}/indexes/${INDEX_NAME}?api-version=2023-11-01" \
  -H "api-key: ${SEARCH_ADMIN_KEY}" \
  -H "Content-Type: application/json" \
  -d "${INDEX_JSON}")

# L12: Accept 200, 201, 204
if [ "$HTTP_STATUS" -ne 200 ] && [ "$HTTP_STATUS" -ne 201 ] && [ "$HTTP_STATUS" -ne 204 ]; then
  echo "   FAILED: Could not create search index (HTTP ${HTTP_STATUS})."
  cat /tmp/search_index_response.json | sed "s/^/   /" || true
  FAILED=true
else
  echo "   OK: Search index '${INDEX_NAME}' created/updated (HTTP ${HTTP_STATUS})."
fi

for filename in "${DOCS[@]}"; do
  doc_file="${DOCS_DIR}/${filename}"
  if [ ! -f "$doc_file" ]; then
    echo "   NOT FOUND: $filename"
    FAILED=true
    continue
  fi
  doc_id="${filename%.md}"
  title=$(head -5 "$doc_file" | grep "^#" | head -1 | sed "s/^#* //")
  content=$(python3 -c "import json,sys; print(json.dumps(open(sys.argv[1]).read()))" "$doc_file")
  DOCUMENT_JSON=$(python3 -c "
import json, sys
doc_id = sys.argv[1]; title = sys.argv[2]; filename = sys.argv[3]; content_file = sys.argv[4]
content = open(content_file).read()
doc = {'value': [{'@search.action': 'mergeOrUpload', 'id': doc_id, 'title': title, 'filename': filename, 'category': 'operational-docs', 'content': content}]}
print(json.dumps(doc))
" "$doc_id" "$title" "$filename" "$doc_file")
  HTTP_STATUS=$(curl -s -o /tmp/search_upload_response.json -w "%{http_code}" \
    -X POST \
    "${AZURE_SEARCH_ENDPOINT}/indexes/${INDEX_NAME}/docs/index?api-version=2023-11-01" \
    -H "api-key: ${SEARCH_ADMIN_KEY}" \
    -H "Content-Type: application/json" \
    -d "${DOCUMENT_JSON}")
  # L12: Accept 200, 201, 204
  if [ "$HTTP_STATUS" -ne 200 ] && [ "$HTTP_STATUS" -ne 201 ] && [ "$HTTP_STATUS" -ne 204 ]; then
    echo "   FAILED: ${filename} (HTTP ${HTTP_STATUS})"
    FAILED=true
  else
    echo "   OK: ${filename}"
  fi
done
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
  echo "  * RBAC assigned (MI + deployer) -- Cosmos, Storage, Search"
  echo "  * Cosmos DB seeded ({{TABLE_NAMES_STR}})"
  echo "  * Operational documents uploaded to Blob Storage"
  echo "  * Documents indexed in AI Search (${INDEX_NAME})"
  echo "  * Agents registered in Azure AI Foundry"
  echo ""
  echo "  Run: azd deploy"
  echo "=================================================================="
  exit 0
fi
