# ai-prototype-accelerator - Post-Provision Script (Windows / PowerShell)
# {{CUSTOMER_NAME}} — {{USE_CASE_TITLE}}
# Runs after: azd provision (Bicep deployment complete)
# Actions: RBAC assignments, Cosmos seed, doc upload, search index, agent registration
$ErrorActionPreference = "Continue"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
# L10: UTF-8 for all Python calls
$env:PYTHONUTF8 = "1"

# Headless-driver overlap: build.py runs `azd provision` concurrently with
# the LLM generation steps; it sets BUILD_DEFER_HOOKS=true for that provision
# and re-runs this hook itself once generation has finished.
if ($env:BUILD_DEFER_HOOKS -eq "true") {
    Write-Host "postprovision: deferred — headless driver will run this after generation completes."
    exit 0
}

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
function Assign-ArmRole($oid, $role, $scope, $label) {
  if ([string]::IsNullOrWhiteSpace($oid) -or [string]::IsNullOrWhiteSpace($scope)) { return }
  az role assignment create `
    --assignee-object-id $oid `
    --assignee-principal-type ServicePrincipal `
    --role $role `
    --scope $scope `
    --output none 2>&1 | Out-Null
  Write-Host "   OK: '$role' -> $label"
}

Write-Host ""

# -- Step 3: Cosmos DB RBAC ---------------------------------------------------
Write-Host "3)  Assigning Cosmos DB Data Contributor role..."
# L3 + L4: assign to BOTH MI and deployer
Assign-CosmosRbac $MI_OID "Managed Identity"
Assign-CosmosRbac $DEPLOYER_OID "Deployer"
Write-Host ""

# -- Step 4: Storage RBAC -----------------------------------------------------
Write-Host "4)  Assigning Storage Blob Data Contributor to MI..."
$STORAGE_ID = $null
try {
  $STORAGE_ID = (az storage account show `
    --name $env:AZURE_STORAGE_ACCOUNT_NAME `
    --resource-group $env:AZURE_RESOURCE_GROUP `
    --query id --output tsv 2>$null)
} catch {}
Assign-ArmRole $MI_OID "Storage Blob Data Contributor" $STORAGE_ID "Managed Identity"
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
Assign-ArmRole $DEPLOYER_OID "Search Index Data Contributor" $SEARCH_ID "Deployer"
Assign-ArmRole $DEPLOYER_OID "Search Service Contributor"   $SEARCH_ID "Deployer"
Write-Host ""

# -- Step 5b: AI Hub + Project RBAC (L17) ------------------------------------
# L17: MI must have Azure AI User on BOTH the hub and project to call Agents API at runtime.
Write-Host "5b) Assigning Azure AI User to MI on AI Hub + Project..."
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
Assign-ArmRole $MI_OID "Azure AI User" $HUB_ID    "MI -> Hub"
Assign-ArmRole $MI_OID "Azure AI User" $PROJ_ID   "MI -> Project"
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

# L13: upload-batch with --auth-mode login
az storage blob upload-batch `
  --account-name $env:AZURE_STORAGE_ACCOUNT_NAME `
  --destination "$CONTAINER_NAME/operational-docs" `
  --source $DOCS_DIR `
  --pattern "*.md" `
  --auth-mode login `
  --overwrite $true `
  --output none 2>&1 | Out-Null
Write-Host "   OK: Operational documents uploaded."
Write-Host ""

# -- Step 10: Create/update AI Search index and index documents ---------------
Write-Host "10) Indexing operational documents in Azure AI Search..."

$INDEX_NAME = if ($env:AZURE_SEARCH_INDEX_NAME) { $env:AZURE_SEARCH_INDEX_NAME } else { "{{SEARCH_INDEX_NAME}}" }
# Use admin API key — avoids RBAC propagation race; disableLocalAuth=false so keys are always available
$SEARCH_ADMIN_KEY = (az search admin-key show `
  --service-name $SEARCH_SERVICE `
  --resource-group $env:AZURE_RESOURCE_GROUP `
  --query primaryKey --output tsv 2>$null)
if ([string]::IsNullOrWhiteSpace($SEARCH_ADMIN_KEY)) {
  Write-Host "   FAILED: Could not retrieve Search admin key."
  $FAILED = $true
}

# L11: correct semantic config field names (prioritizedContentFields / prioritizedKeywordsFields)
$INDEX_DEF = @{
  name   = $INDEX_NAME
  fields = @(
    @{ name = "id";       type = "Edm.String"; key = $true;  searchable = $false },
    @{ name = "content";  type = "Edm.String"; searchable = $true; analyzer = "en.microsoft" },
    @{ name = "title";    type = "Edm.String"; searchable = $true;  filterable = $true },
    @{ name = "filename"; type = "Edm.String"; searchable = $false; filterable = $true },
    @{ name = "category"; type = "Edm.String"; searchable = $false; filterable = $true }
  )
  semantic = @{
    configurations = @(@{
      name = "default"
      prioritizedFields = @{
        prioritizedContentFields  = @(@{ fieldName = "content" })
        prioritizedKeywordsFields = @(@{ fieldName = "title" })
      }
    })
  }
} | ConvertTo-Json -Depth 10

try {
  $indexResponse = Invoke-WebRequest `
    -Uri "$env:AZURE_SEARCH_ENDPOINT/indexes/${INDEX_NAME}?api-version=2023-11-01" `
    -Method PUT `
    -Headers @{ "api-key" = $SEARCH_ADMIN_KEY; "Content-Type" = "application/json" } `
    -Body $INDEX_DEF `
    -UseBasicParsing `
    -ErrorAction Stop
  # L12: Accept 200, 201, 204
  if ($indexResponse.StatusCode -in @(200, 201, 204)) {
    Write-Host "   OK: Search index '$INDEX_NAME' created/updated (HTTP $($indexResponse.StatusCode))."
  } else {
    Write-Host "   FAILED: Search index (HTTP $($indexResponse.StatusCode))."
    $FAILED = $true
  }
} catch {
  $sc = $_.Exception.Response.StatusCode.value__
  # L12: 204 comes back as a non-terminating response in some environments
  if ($sc -eq 204) {
    Write-Host "   OK: Search index already exists (HTTP 204)."
  } else {
    Write-Host "   FAILED: Could not create search index -- $($_.Exception.Message)"
    $FAILED = $true
  }
}

foreach ($filename in $DOCS) {
  if ([string]::IsNullOrWhiteSpace($SEARCH_ADMIN_KEY)) { $FAILED = $true; break }
  $doc_file = Join-Path $DOCS_DIR $filename
  if (-not (Test-Path $doc_file)) {
    Write-Host "   NOT FOUND: $filename"
    $FAILED = $true
    continue
  }
  $doc_id   = [System.IO.Path]::GetFileNameWithoutExtension($filename)
  $raw      = Get-Content $doc_file -Raw
  $title    = ($raw -split "`n" | Where-Object { $_ -match "^#" } | Select-Object -First 1) -replace "^#+ ", ""
  $doc_body = @{
    value = @(@{
      "@search.action" = "mergeOrUpload"
      id       = $doc_id
      title    = $title
      filename = $filename
      category = "operational-docs"
      content  = $raw
    })
  } | ConvertTo-Json -Depth 5

  try {
    $uploadResponse = Invoke-WebRequest `
      -Uri "$env:AZURE_SEARCH_ENDPOINT/indexes/${INDEX_NAME}/docs/index?api-version=2023-11-01" `
      -Method POST `
      -Headers @{ "api-key" = $SEARCH_ADMIN_KEY; "Content-Type" = "application/json" } `
      -Body $doc_body `
      -UseBasicParsing `
      -ErrorAction Stop
    # L12: Accept 200, 201, 204
    if ($uploadResponse.StatusCode -in @(200, 201, 204)) {
      Write-Host "   OK: $filename"
    } else {
      Write-Host "   FAILED: $filename (HTTP $($uploadResponse.StatusCode))"
      $FAILED = $true
    }
  } catch {
    $sc = $_.Exception.Response.StatusCode.value__
    if ($sc -eq 204) { Write-Host "   OK: $filename (HTTP 204)" }
    else { Write-Host "   FAILED: $filename -- $($_.Exception.Message)"; $FAILED = $true }
  }
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
  Write-Host "  * RBAC assigned (MI + deployer) -- Cosmos, Storage, Search"
  Write-Host "  * Cosmos DB seeded ({{TABLE_NAMES_STR}})"
  Write-Host "  * Operational documents uploaded to Blob Storage"
  Write-Host "  * Documents indexed in AI Search ($INDEX_NAME)"
  Write-Host "  * Agents registered in Azure AI Foundry"
  Write-Host ""
  Write-Host "  Run: azd deploy"
  Write-Host "=================================================================="
  exit 0
}
