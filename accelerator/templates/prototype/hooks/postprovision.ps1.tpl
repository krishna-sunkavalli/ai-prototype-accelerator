# ai-prototype-accelerator - Post-Provision Script (Windows / PowerShell)
# {{CUSTOMER_NAME}} — {{USE_CASE_TITLE}}
# Runs after: azd provision (Bicep deployment complete)
# Actions: RBAC assignments, Cosmos seed, doc upload, search index, agent registration
$ErrorActionPreference = "Continue"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
# L10: UTF-8 for all Python calls
$env:PYTHONUTF8 = "1"

Write-Host "=================================================================="
Write-Host "  ai-prototype-accelerator - Post-Provision"
Write-Host "  {{CUSTOMER_NAME}} — {{USE_CASE_TITLE}}"
Write-Host "=================================================================="
Write-Host ""

$FAILED = $false
$SCRIPT_DIR = Split-Path -Parent $MyInvocation.MyCommand.Path

# -- Step 1: Validate required environment variables --------------------------
Write-Host "1)  Validating environment variables..."

$REQUIRED_VARS = @(
  "AZURE_AI_PROJECT_ENDPOINT",
  "AZURE_SEARCH_ENDPOINT",
  "AZURE_STORAGE_ACCOUNT_NAME",
  "AZURE_COSMOS_ENDPOINT",
  "AZURE_COSMOS_DATABASE",
  "AZURE_COSMOS_ACCOUNT_NAME",
  "AZURE_RESOURCE_GROUP",
  "AZURE_SUBSCRIPTION_ID"
)

foreach ($var in $REQUIRED_VARS) {
  if ([string]::IsNullOrEmpty([System.Environment]::GetEnvironmentVariable($var))) {
    Write-Host "   MISSING: $var"
    $FAILED = $true
  } else {
    Write-Host "   OK: $var"
  }
}

if ($FAILED) {
  Write-Host ""
  Write-Host "One or more required environment variables are missing."
  Write-Host "Run: azd env set <VAR_NAME> <value>"
  exit 1
}

# Derive MI display name
if ([string]::IsNullOrEmpty($env:AZURE_MI_DISPLAY_NAME)) {
  $env:AZURE_MI_DISPLAY_NAME = "{{MI_NAME}}"
}
Write-Host "   MI display name: $env:AZURE_MI_DISPLAY_NAME"
Write-Host ""

# -- Step 2: Resolve MI and deployer object IDs --------------------------------
Write-Host "2)  Resolving MI and deployer object IDs..."

# L1: Derive MI OID via az identity show (.principalId), NOT az ad sp show --id
$MI_OID = $null
try {
  $MI_OID = (az identity show `
    --name $env:AZURE_MI_DISPLAY_NAME `
    --resource-group $env:AZURE_RESOURCE_GROUP `
    --query principalId --output tsv 2>$null)
} catch {}

if (-not [string]::IsNullOrWhiteSpace($MI_OID)) {
  Write-Host "   MI OID: $MI_OID"
} else {
  Write-Host "   WARN: Could not resolve MI OID -- RBAC steps will be skipped."
  $MI_OID = $null
}

# L4: Deployer OID so both MI and deployer receive Cosmos access
$DEPLOYER_OID = $null
try {
  $DEPLOYER_OID = (az ad signed-in-user show --query id --output tsv 2>$null)
} catch {}
if (-not [string]::IsNullOrWhiteSpace($DEPLOYER_OID)) {
  Write-Host "   Deployer OID: $DEPLOYER_OID"
} else {
  Write-Host "   INFO: No signed-in user (CI/CD pipeline) -- only MI will receive roles."
  $DEPLOYER_OID = $null
}

$SUBSCRIPTION_ID = $env:AZURE_SUBSCRIPTION_ID
$COSMOS_SCOPE = "/subscriptions/$SUBSCRIPTION_ID/resourceGroups/$env:AZURE_RESOURCE_GROUP/providers/Microsoft.DocumentDB/databaseAccounts/$env:AZURE_COSMOS_ACCOUNT_NAME"

# Helper: Assign Cosmos SQL RBAC
function Assign-CosmosRbac($oid, $label) {
  if ([string]::IsNullOrWhiteSpace($oid)) { return }
  az cosmosdb sql role assignment create `
    --account-name $env:AZURE_COSMOS_ACCOUNT_NAME `
    --resource-group $env:AZURE_RESOURCE_GROUP `
    --role-definition-id "00000000-0000-0000-0000-000000000002" `
    --principal-id $oid `
    --scope $COSMOS_SCOPE `
    --output none 2>&1 | Out-Null
  Write-Host "   OK: Cosmos Built-in Data Contributor -> $label"
}

# Helper: Assign ARM role
# principalType defaults to ServicePrincipal (correct for the MI). The
# deployer OID (step 2) only ever resolves via `az ad signed-in-user show`,
# which succeeds only for an interactively signed-in human -- meaning
# whenever $DEPLOYER_OID is non-empty it is ALWAYS type 'User', never
# 'ServicePrincipal'. Passing the wrong hint doesn't just get ignored: ARM
# rejects the assignment outright with UnmatchedPrincipalType. That error
# was invisible here because the original call swallowed stdout/stderr AND
# never checked the exit code -- always printing "OK" regardless. Every
# deployer-scoped call below must pass -PrincipalType 'User' explicitly.
function Assign-ArmRole($oid, $role, $scope, $label, $principalType = "ServicePrincipal") {
  if ([string]::IsNullOrWhiteSpace($oid) -or [string]::IsNullOrWhiteSpace($scope)) { return }
  $roleOutput = az role assignment create `
    --assignee-object-id $oid `
    --assignee-principal-type $principalType `
    --role $role `
    --scope $scope 2>&1
  if ($LASTEXITCODE -ne 0) {
    Write-Host "   FAILED: '$role' -> $label -- $roleOutput"
    # $script: scope required -- a bare `$FAILED = $true` here only sets a
    # function-local variable in PowerShell and silently never reaches the
    # final `if ($FAILED)` gate at the bottom of the script.
    $script:FAILED = $true
  } else {
    Write-Host "   OK: '$role' -> $label"
  }
}

Write-Host ""

# -- Step 3: Cosmos DB RBAC ---------------------------------------------------
Write-Host "3)  Assigning Cosmos DB Data Contributor role..."
# L3 + L4: assign to BOTH MI and deployer
Assign-CosmosRbac $MI_OID "Managed Identity"
Assign-CosmosRbac $DEPLOYER_OID "Deployer"
Write-Host ""

# -- Step 4: Storage RBAC -----------------------------------------------------
Write-Host "4)  Assigning Storage Blob Data Contributor to MI + Deployer..."
$STORAGE_ID = $null
try {
  $STORAGE_ID = (az storage account show `
    --name $env:AZURE_STORAGE_ACCOUNT_NAME `
    --resource-group $env:AZURE_RESOURCE_GROUP `
    --query id --output tsv 2>$null)
} catch {}
Assign-ArmRole $MI_OID       "Storage Blob Data Contributor" $STORAGE_ID "Managed Identity"
# L13 fix: the doc-upload step (step 9) uses `az storage blob upload-batch
# --auth-mode login`, which authenticates as the DEPLOYER, not the MI. Without
# this role the upload fails with 403 on every build -- silently, because the
# original step 9 command discarded both stdout/stderr AND the exit code.
Assign-ArmRole $DEPLOYER_OID "Storage Blob Data Contributor" $STORAGE_ID "Deployer" "User"
Write-Host ""

# -- Step 5: AI Search RBAC ---------------------------------------------------
Write-Host "5)  Assigning AI Search roles to MI..."
$SEARCH_SERVICE = ($env:AZURE_SEARCH_ENDPOINT -replace "https://", "") -replace "\..*", ""
$SEARCH_ID = $null
try {
  $SEARCH_ID = (az search service show `
    --name $SEARCH_SERVICE `
    --resource-group $env:AZURE_RESOURCE_GROUP `
    --query id --output tsv 2>$null)
} catch {}
Assign-ArmRole $MI_OID      "Search Index Data Contributor" $SEARCH_ID "Managed Identity"
Assign-ArmRole $MI_OID      "Search Service Contributor"   $SEARCH_ID "Managed Identity"
Assign-ArmRole $DEPLOYER_OID "Search Index Data Contributor" $SEARCH_ID "Deployer" "User"
Assign-ArmRole $DEPLOYER_OID "Search Service Contributor"   $SEARCH_ID "Deployer" "User"
Write-Host ""

# -- Step 5b: AI Hub + Project RBAC (L17) ------------------------------------
# L17: MI must have Foundry User (formerly "Azure AI User") on BOTH the hub
# and project to call Agents API at runtime. Microsoft renamed several
# Foundry RBAC roles; the old name silently fails with "Role 'Azure AI
# User' doesn't exist" -- previously invisible here because Assign-ArmRole
# discarded stdout/stderr and never checked the exit code.
Write-Host "5b) Assigning Foundry User to MI on AI Hub + Project..."
$AI_HUB_NAME = $env:AZURE_AI_HUB_NAME
$AI_PROJECT_NAME = $env:AZURE_AI_PROJECT_NAME
$HUB_ID = $null
$PROJ_ID = $null
try {
  $HUB_ID = (az resource show `
    --name $AI_HUB_NAME `
    --resource-group $env:AZURE_RESOURCE_GROUP `
    --resource-type "Microsoft.CognitiveServices/accounts" `
    --query id --output tsv 2>$null)
  if ($HUB_ID) { $PROJ_ID = "$HUB_ID/projects/$AI_PROJECT_NAME" }
} catch {}
Assign-ArmRole $MI_OID "Foundry User" $HUB_ID    "MI -> Hub"
Assign-ArmRole $MI_OID "Foundry User" $PROJ_ID   "MI -> Project"
Write-Host ""

# -- Step 5c: AI Search's OWN identity -> Foundry hub + Storage (Foundry IQ) --
# Foundry IQ (blob knowledge source + knowledge base, step 10) calls the
# deployed embedding/chat models and reads blob content using the Search
# service's OWN system-assigned identity -- never an API key -- so no secret
# ever lands in the knowledge source/base definitions.
Write-Host "5c) Assigning AI Search's identity to Foundry hub + Storage..."
$SEARCH_PRINCIPAL_ID = $null
try {
  $SEARCH_PRINCIPAL_ID = (az search service show `
    --name $SEARCH_SERVICE `
    --resource-group $env:AZURE_RESOURCE_GROUP `
    --query identity.principalId --output tsv 2>$null)
} catch {}
Assign-ArmRole $SEARCH_PRINCIPAL_ID "Cognitive Services User" $HUB_ID "Search -> Hub (Foundry IQ)"
Assign-ArmRole $SEARCH_PRINCIPAL_ID "Storage Blob Data Reader" $STORAGE_ID "Search -> Storage (Foundry IQ)"
Write-Host ""

# -- Step 6: Consolidated RBAC propagation wait (L5) -------------------------
Write-Host "6)  Waiting 90s for all RBAC assignments to propagate..."
Start-Sleep -Seconds 90
Write-Host "   Done."
Write-Host ""

# -- Step 7: Cosmos DB networking + disable key-based metadata writes (L6+L7) -
Write-Host "7)  Configuring Cosmos DB networking and access settings..."
az cosmosdb update `
  --name $env:AZURE_COSMOS_ACCOUNT_NAME `
  --resource-group $env:AZURE_RESOURCE_GROUP `
  --public-network-access Enabled `
  --network-acl-bypass AzureServices `
  --disable-key-based-metadata-write-access $false `
  --output none 2>&1 | Out-Null
Write-Host "   OK: Cosmos DB networking configured."
Write-Host ""

# -- Step 8: Seed Cosmos DB ---------------------------------------------------
Write-Host "8)  Seeding Cosmos DB containers..."

$SEED_SCRIPT = Join-Path $SCRIPT_DIR "..\db\cosmos_seed.py"

if (-not (Test-Path $SEED_SCRIPT)) {
  Write-Host "   ERROR: Seed script not found: $SEED_SCRIPT"
  $FAILED = $true
} else {
  $result = python $SEED_SCRIPT 2>&1
  $result | ForEach-Object { Write-Host "   $_" }
  if ($LASTEXITCODE -ne 0) {
    Write-Host "   ERROR: Cosmos DB seeding failed (exit $LASTEXITCODE)."
    $FAILED = $true
  } else {
    Write-Host "   OK: {{TABLE_NAMES_STR}} seeded."
  }
}
Write-Host ""

# -- Step 9: Upload operational documents to Blob Storage ---------------------
Write-Host "9)  Uploading operational documents to Blob Storage..."

$DOCS_DIR = Join-Path $SCRIPT_DIR "..\agents\knowledge"
$CONTAINER_NAME = "prototype-data"

az storage container create `
  --name $CONTAINER_NAME `
  --account-name $env:AZURE_STORAGE_ACCOUNT_NAME `
  --auth-mode login `
  --output none 2>&1 | Out-Null

# Document filenames derived from manifest.json by fill-templates.py
$DOCS={{DOCS_PS_ARRAY}}

# L13: upload-batch with --auth-mode login (requires Storage Blob Data
# Contributor on the DEPLOYER -- see step 4 -- since --auth-mode login
# authenticates as the signed-in user, not the managed identity).
$uploadOutput = az storage blob upload-batch `
  --account-name $env:AZURE_STORAGE_ACCOUNT_NAME `
  --destination "$CONTAINER_NAME/operational-docs" `
  --source $DOCS_DIR `
  --pattern "*.md" `
  --auth-mode login `
  --overwrite $true 2>&1
if ($LASTEXITCODE -ne 0) {
  Write-Host "   ERROR: Document upload failed (exit $LASTEXITCODE)."
  $uploadOutput | ForEach-Object { Write-Host "   $_" }
  $FAILED = $true
} else {
  $uploadedCount = (Get-ChildItem -Path $DOCS_DIR -Filter *.md -File).Count
  Write-Host "   OK: Operational documents uploaded ($uploadedCount file(s))."
}
Write-Host ""

# -- Step 10: Wire operational documents into a Foundry IQ knowledge base ----
# Real Foundry IQ wiring (not a hand-rolled index): a blob knowledge source
# auto-generates the data source + skillset (chunking/vectorization) +
# indexer + index from the blob container, and a knowledge base wraps it for
# agentic retrieval (subquery decomposition + semantic reranking). Both the
# embedding model and the query-planning LLM are called via the Search
# service's OWN identity (granted in step 5c) -- no API key stored in either
# object. Cosmos DB is untouched by this change; run_sql_query keeps handling
# precise structured lookups.
Write-Host "10) Wiring operational documents into Foundry IQ (agentic retrieval)..."

$INDEX_NAME = if ($env:AZURE_SEARCH_INDEX_NAME) { $env:AZURE_SEARCH_INDEX_NAME } else { "{{SEARCH_INDEX_NAME}}" }
$KS_NAME = "$INDEX_NAME-ks"
$KB_NAME = "$INDEX_NAME-kb"
$KS_API_VERSION = "2026-05-01-preview"

# Use admin API key for the Search control-plane calls themselves (avoids the
# RBAC propagation race for this script, same as before) -- this is separate
# from the identity-based auth the knowledge source/base use to reach the
# embedding/chat models and blob storage.
$SEARCH_ADMIN_KEY = (az search admin-key show `
  --service-name $SEARCH_SERVICE `
  --resource-group $env:AZURE_RESOURCE_GROUP `
  --query primaryKey --output tsv 2>$null)
if ([string]::IsNullOrWhiteSpace($SEARCH_ADMIN_KEY)) {
  Write-Host "   FAILED: Could not retrieve Search admin key."
  $FAILED = $true
}

# Base AI Services endpoint (NOT the /api/projects/{proj}/ path) for the
# embedding + chat-completion model references below.
$AI_SERVICES_SUBDOMAIN = $null
try {
  $AI_SERVICES_SUBDOMAIN = (az cognitiveservices account show `
    --name $AI_HUB_NAME `
    --resource-group $env:AZURE_RESOURCE_GROUP `
    --query properties.customSubDomainName --output tsv 2>$null)
} catch {}
$AI_SERVICES_ENDPOINT = "https://$AI_SERVICES_SUBDOMAIN.services.ai.azure.com/"

# Identity-based blob connection string -- no storage key stored anywhere.
$STORAGE_CONNECTION = "ResourceId=/subscriptions/$SUBSCRIPTION_ID/resourceGroups/$env:AZURE_RESOURCE_GROUP/providers/Microsoft.Storage/storageAccounts/$env:AZURE_STORAGE_ACCOUNT_NAME;"

$KS_BODY = @{
  name = $KS_NAME
  kind = "azureBlob"
  description = "Operational knowledge documents for {{CUSTOMER_NAME}}."
  azureBlobParameters = @{
    connectionString = $STORAGE_CONNECTION
    containerName = "prototype-data"
    folderPath = "operational-docs"
    isADLSGen2 = $false
    ingestionParameters = @{
      contentExtractionMode = "minimal"
      # NOTE: chatCompletionModel must NOT be set when
      # disableImageVerbalization is $true -- the Search knowledge source
      # API returns 400 "ChatCompletionModel must not be set when
      # DisableImageVerbalization is true." chatCompletionModel is only
      # used for image verbalization during ingestion, so it has no effect
      # here anyway. See RESOLVED.md.
      disableImageVerbalization = $true
      embeddingModel = @{
        kind = "azureOpenAI"
        azureOpenAIParameters = @{
          resourceUri = $AI_SERVICES_ENDPOINT
          deploymentId = "text-embedding-3-large"
          modelName = "text-embedding-3-large"
        }
      }
    }
  }
} | ConvertTo-Json -Depth 10

try {
  $ksResponse = Invoke-WebRequest `
    -Uri "$env:AZURE_SEARCH_ENDPOINT/knowledgesources/${KS_NAME}?api-version=$KS_API_VERSION" `
    -Method PUT `
    -Headers @{ "api-key" = $SEARCH_ADMIN_KEY; "Content-Type" = "application/json" } `
    -Body $KS_BODY `
    -UseBasicParsing `
    -ErrorAction Stop
  if ($ksResponse.StatusCode -in @(200, 201, 204)) {
    Write-Host "   OK: Knowledge source '$KS_NAME' created/updated (HTTP $($ksResponse.StatusCode))."
  } else {
    Write-Host "   FAILED: Knowledge source (HTTP $($ksResponse.StatusCode))."
    $FAILED = $true
  }
} catch {
  $sc = $_.Exception.Response.StatusCode.value__
  if ($sc -eq 204) { Write-Host "   OK: Knowledge source already exists (HTTP 204)." }
  else { Write-Host "   FAILED: Could not create knowledge source -- $($_.Exception.Message)"; $FAILED = $true }
}

$KB_BODY = @{
  name = $KB_NAME
  description = "Foundry IQ knowledge base for {{CUSTOMER_NAME}} operational documents."
  knowledgeSources = @(@{ name = $KS_NAME })
  models = @(@{
    kind = "azureOpenAI"
    azureOpenAIParameters = @{
      resourceUri = $AI_SERVICES_ENDPOINT
      deploymentId = "gpt-4o-mini"
      modelName = "gpt-4o-mini"
    }
  })
  outputMode = "extractiveData"
  retrievalReasoningEffort = @{ kind = "low" }
  retrievalInstructions = "{{KB_RETRIEVAL_INSTRUCTIONS}}"
} | ConvertTo-Json -Depth 10

try {
  $kbResponse = Invoke-WebRequest `
    -Uri "$env:AZURE_SEARCH_ENDPOINT/knowledgebases/${KB_NAME}?api-version=$KS_API_VERSION" `
    -Method PUT `
    -Headers @{ "api-key" = $SEARCH_ADMIN_KEY; "Content-Type" = "application/json" } `
    -Body $KB_BODY `
    -UseBasicParsing `
    -ErrorAction Stop
  if ($kbResponse.StatusCode -in @(200, 201, 204)) {
    Write-Host "   OK: Knowledge base '$KB_NAME' created/updated (HTTP $($kbResponse.StatusCode))."
  } else {
    Write-Host "   FAILED: Knowledge base (HTTP $($kbResponse.StatusCode))."
    $FAILED = $true
  }
} catch {
  $sc = $_.Exception.Response.StatusCode.value__
  if ($sc -eq 204) { Write-Host "   OK: Knowledge base already exists (HTTP 204)." }
  else { Write-Host "   FAILED: Could not create knowledge base -- $($_.Exception.Message)"; $FAILED = $true }
}

# Brief, bounded, non-fatal poll — first ingestion sync can take a few
# minutes even for a handful of docs. A prototype shouldn't hang the whole
# deploy on it; log where it stands and move on.
Write-Host "   Checking ingestion status (up to 60s, non-blocking)..."
for ($i = 0; $i -lt 6; $i++) {
  Start-Sleep -Seconds 10
  try {
    $statusResponse = Invoke-WebRequest `
      -Uri "$env:AZURE_SEARCH_ENDPOINT/knowledgesources/${KS_NAME}/status?api-version=$KS_API_VERSION" `
      -Method GET `
      -Headers @{ "api-key" = $SEARCH_ADMIN_KEY } `
      -UseBasicParsing `
      -ErrorAction Stop
    $status = ($statusResponse.Content | ConvertFrom-Json).lastSynchronizationState.status
    if ($status -eq "success") {
      Write-Host "   OK: Initial ingestion complete."
      break
    } elseif ($i -eq 5) {
      Write-Host "   INFO: Ingestion still in progress (status: $status) -- it will finish in the background."
    }
  } catch { break }
}
Write-Host ""

# -- Step 11: Register AI Foundry agents --------------------------------------
Write-Host "11) Registering agents in Azure AI Foundry..."

$REGISTER_SCRIPT = Join-Path $SCRIPT_DIR "..\agents\register_agents.py"

if (-not (Test-Path $REGISTER_SCRIPT)) {
  Write-Host "   ERROR: Agent registration script not found: $REGISTER_SCRIPT"
  $FAILED = $true
} elseif ([string]::IsNullOrEmpty($env:AZURE_AI_PROJECT_ENDPOINT)) {
  Write-Host "   ERROR: AZURE_AI_PROJECT_ENDPOINT not set -- skipping agent registration."
  $FAILED = $true
} else {
  $result = python $REGISTER_SCRIPT 2>&1
  $result | ForEach-Object { Write-Host "   $_" }
  if ($LASTEXITCODE -ne 0) {
    Write-Host "   ERROR: Agent registration failed (exit $LASTEXITCODE)."
    $FAILED = $true
  } else {
    Write-Host "   OK: All agents registered in Azure AI Foundry."
  }
}
Write-Host ""

# -- Final status --------------------------------------------------------------
if ($FAILED) {
  Write-Host "====================================="
  Write-Host "  POST-PROVISION FAILED"
  Write-Host "====================================="
  Write-Host ""
  Write-Host "One or more post-provision steps failed. Review errors above."
  Write-Host "Re-run: azd provision"
  exit 1
} else {
  Write-Host "=================================================================="
  Write-Host "  POST-PROVISION COMPLETE"
  Write-Host ""
  Write-Host "  * RBAC assigned (MI + deployer + Search identity) -- Cosmos, Storage, Search, Foundry"
  Write-Host "  * Cosmos DB seeded ({{TABLE_NAMES_STR}})"
  Write-Host "  * Operational documents uploaded to Blob Storage"
  Write-Host "  * Foundry IQ knowledge base wired ($KB_NAME, agentic retrieval)"
  Write-Host "  * Agents registered in Azure AI Foundry"
  Write-Host ""
  Write-Host "  Run: azd deploy"
  Write-Host "=================================================================="
  exit 0
}
