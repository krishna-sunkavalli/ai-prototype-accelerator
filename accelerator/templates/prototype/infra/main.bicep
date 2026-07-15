// ============================================================
// main.bicep — ai-prototype-accelerator
//
// Orchestrates:
//   1. User-Assigned Managed Identity
//   2. Monitoring (Log Analytics + App Insights)
//   3. Storage (Blob)
//   4. Cosmos DB (NoSQL, Serverless)
//   5. Container Registry (ACR)
//   6. AI Foundry Hub + Project + Key Vault
//   7. Azure OpenAI (GPT-4o + embeddings)
//   8. Container Apps Environment + Agent App
// ============================================================

@description('Short customer name used as resource prefix (e.g. "contoso").')
@minLength(2)
@maxLength(20)
param customerName string

@description('Azure region for all resources.')
param location string = resourceGroup().location

@description('Short demo theme slug (e.g. "loan-approval-ai").')
@minLength(2)
@maxLength(30)
param demoTheme string
@description('Environment name used in MI naming convention (e.g. "prototype", "dev", "prod").')
param environmentName string = 'prototype'
@description('Container image the Container App boots with on FIRST provision. Kept at the ACA hello-world so the initial Bicep deployment can create the resource before `azd deploy` pushes the real image. Preflight (`check_container_image_not_placeholder`) fails the build if the deployed Container App is still running this image after step 9, which is the signal that `azd deploy` never ran or failed silently.')
param containerImage string = 'mcr.microsoft.com/azuredocs/containerapps-helloworld:latest'

@description('Name of the initial Cosmos DB database.')
param databaseName string = 'prototype-db'

@description('Name of the Azure AI Search index.')
param searchIndexName string = 'prototype-index'

@description('LLM model deployments. Driven by spec.yaml.model_deployments. Each entry: { deploymentName, model, version, capacity, sku? }.')
param modelDeployments array = [
  {
    deploymentName: 'gpt-4o'
    model: 'gpt-4o'
    version: '2024-11-20'
    capacity: 10
  }
  {
    deploymentName: 'gpt-4o-mini'
    model: 'gpt-4o-mini'
    version: '2024-07-18'
    capacity: 10
  }
]

@description('Embedding model deployment capacity (TPM in thousands).')
param embeddingCapacity int = 10

@description('Azure region for the AI Foundry hub and model deployments. Defaults to location. Override when your primary region lacks GlobalStandard quota (e.g. eastus or swedencentral).')
param aiLocation string = location

@description('Azure region for the AI Search service. Defaults to location. Override when the primary region is capacity-exhausted on the standard/basic Search SKU.')
param searchLocation string = location

@description('AI Search SKU. `basic` is the default for prototype workloads; bump to `standard` (or higher) when Basic is capacity-exhausted region-wide. `azd env set AZURE_SEARCH_SKU standard` is the intended override path.')
@allowed([
  'basic'
  'standard'
  'standard2'
  'standard3'
])
param searchSku string = 'basic'

// ── Branding (populated from spec.yaml at Step 2b) ───────────────────
@description('Customer display name shown in the UI header.')
param customerDisplayName string = 'AI Prototype'

@description('Agent name shown in the UI.')
param agentName string = 'AI Assistant'

@description('Primary brand color (hex).')
param primaryColor string = '#0078D4'

@description('Accent brand color (hex).')
param accentColor string = '#50E6FF'

@description('Font family for the UI.')
param fontFamily string = 'Arial, sans-serif'

@description('URL to the customer logo image.')
param logoUrl string = ''

@description('Welcome message shown in the chat UI.')
param welcomeMessage string = 'Hello! How can I help you today?'

@description('Use case title shown as agent subtitle.')
param useCaseTitle string = ''
@description('Starter question chips shown in the UI (JSON array string, e.g. [\'Q1\',\'Q2\']).')
param starterQuestions string = '[]'

@description('Demo persona display name shown in the user profile.')
param personaName string = 'User'

@description('Demo persona role shown under the profile name.')
param personaRole string = 'Viewer'
// ── Derived naming ───────────────────────────────────────────
var resourcePrefix = '${customerName}-${demoTheme}'

var commonTags = {
  customer: customerName
  demoTheme: demoTheme
  managedBy: 'azd'
}

// ── User-Assigned Managed Identity ───────────────────────────
// Name follows convention: {customerName}-{environmentName}-id
module identity 'br/public:avm/res/managed-identity/user-assigned-identity:0.6.0' = {
  name: 'deploy-identity'
  params: {
    name: '${customerName}-${environmentName}-id'
    location: location
    tags: commonTags
  }
}

// ── Monitoring ────────────────────────────────────────────────
module monitoring './modules/monitoring.bicep' = {
  name: 'deploy-monitoring'
  params: {
    resourcePrefix: resourcePrefix
    location: location
    tags: commonTags
  }
}

// ── Storage ───────────────────────────────────────────────────
module storage './modules/storage.bicep' = {
  name: 'deploy-storage-mod'
  params: {
    resourcePrefix: resourcePrefix
    location: location
    tags: commonTags
    managedIdentityPrincipalId: identity.outputs.principalId
  }
}

// ── Cosmos DB ─────────────────────────────────────────────────
module cosmos './modules/cosmos.bicep' = {
  name: 'deploy-cosmos'
  params: {
    resourcePrefix: resourcePrefix
    location: location
    tags: commonTags
    databaseName: databaseName
  }
}

// ── Container Registry ────────────────────────────────────────
module acr './modules/container-registry.bicep' = {
  name: 'deploy-acr-mod'
  params: {
    resourcePrefix: resourcePrefix
    location: location
    tags: commonTags
    managedIdentityPrincipalId: identity.outputs.principalId
  }
}

// ── AI Foundry (Hub + Project) ────────────────────────────────
// Creates a single AIServices account that acts as both the hub
// and the model host. Model deployments are wired in foundry-iq.
module foundry './modules/foundry.bicep' = {
  name: 'deploy-foundry'
  params: {
    resourcePrefix: resourcePrefix
    location: location
    aiLocation: aiLocation
    tags: commonTags
    managedIdentityPrincipalId: identity.outputs.principalId
  }
}

// ── AI Models (foundry-iq) ───────────────────────────────────
// Adds model deployments to the hub account. No separate OpenAI
// account is created.
module openAi './modules/foundry-iq.bicep' = {
  name: 'deploy-foundry-iq'
  params: {
    hubAccountName: foundry.outputs.aiHubName
    modelDeployments: modelDeployments
    embeddingCapacity: embeddingCapacity
  }
}

// ── Azure AI Search ───────────────────────────────────────────
// No dependency on the foundry module — ARM provisions this in
// parallel with the Foundry hub instead of serializing behind it
// (foundry account creation includes subdomain-reservation checks
// that are often the slowest single resource in the deployment).
module search './modules/search.bicep' = {
  name: 'deploy-search'
  params: {
    resourcePrefix: resourcePrefix
    location: searchLocation
    searchSku: searchSku
    tags: commonTags
    managedIdentityPrincipalId: identity.outputs.principalId
  }
}

// ── Container App ─────────────────────────────────────────────
module app './modules/container-app.bicep' = {
  name: 'deploy-app'
  params: {
    resourcePrefix: resourcePrefix
    location: location
    tags: commonTags
    appInsightsConnectionString: monitoring.outputs.appInsightsConnectionString
    managedIdentityResourceId: identity.outputs.resourceId
    managedIdentityClientId: identity.outputs.clientId
    containerImage: containerImage
    cosmosEndpoint: cosmos.outputs.cosmosEndpoint
    cosmosDatabaseName: cosmos.outputs.cosmosDatabaseName
    cosmosAccountName: cosmos.outputs.cosmosAccountName
    searchEndpoint: search.outputs.searchEndpoint
    searchIndexName: searchIndexName
    acrLoginServer: acr.outputs.loginServer
    storageAccountName: storage.outputs.storageAccountName
    miDisplayName: '${customerName}-${environmentName}-id'
    customerDisplayName: customerDisplayName
    agentName: agentName
    primaryColor: primaryColor
    accentColor: accentColor
    fontFamily: fontFamily
    logoUrl: logoUrl
    welcomeMessage: welcomeMessage
    useCaseTitle: useCaseTitle
    starterQuestions: starterQuestions
    personaName: personaName
    personaRole: personaRole
    aiProjectEndpoint: foundry.outputs.aiProjectEndpoint
  }
}

// ── Outputs ───────────────────────────────────────────────────
@description('Resource ID of the managed identity.')
output identityId string = identity.outputs.resourceId

@description('Principal ID of the managed identity.')
output identityPrincipalId string = identity.outputs.principalId

@description('Name of the Log Analytics workspace.')
output logAnalyticsWorkspaceName string = monitoring.outputs.logAnalyticsWorkspaceName

@description('Name of Application Insights.')
output appInsightsName string = monitoring.outputs.appInsightsName

@description('Name of the storage account.')
output storageAccountName string = storage.outputs.storageAccountName

@description('Cosmos DB endpoint URI.')
output cosmosEndpoint string = cosmos.outputs.cosmosEndpoint

@description('Cosmos DB database name.')
output cosmosDatabaseName string = cosmos.outputs.cosmosDatabaseName

@description('Cosmos DB account name.')
output cosmosAccountName string = cosmos.outputs.cosmosAccountName

@description('Login server of the container registry.')
output acrLoginServer string = acr.outputs.loginServer

@description('Name of the AI Foundry Hub.')
output aiHubName string = foundry.outputs.aiHubName

@description('Name of the AI Foundry Project.')
output aiProjectName string = foundry.outputs.aiProjectName

@description('Name of the Container App.')
output containerAppName string = app.outputs.containerAppName

@description('Public FQDN of the Container App.')
output containerAppUrl string = 'https://${app.outputs.containerAppFqdn}'

// ── azd env vars (SCREAMING_SNAKE_CASE for azd .env export, consumed by hooks) ──
@description('Storage account name — used as AZURE_STORAGE_ACCOUNT_NAME by hooks.')
output AZURE_STORAGE_ACCOUNT_NAME string = storage.outputs.storageAccountName

@description('Cosmos DB endpoint — used as AZURE_COSMOS_ENDPOINT by hooks.')
output AZURE_COSMOS_ENDPOINT string = cosmos.outputs.cosmosEndpoint

@description('Container Registry login server — used as AZURE_CONTAINER_REGISTRY_ENDPOINT by azd.')
output AZURE_CONTAINER_REGISTRY_ENDPOINT string = acr.outputs.loginServer

@description('MI display name — used as AZURE_MI_DISPLAY_NAME by hooks.')
output AZURE_MI_DISPLAY_NAME string = '${customerName}-${environmentName}-id'

@description('Cosmos DB database name — used as AZURE_COSMOS_DATABASE by tests.')
output AZURE_COSMOS_DATABASE string = cosmos.outputs.cosmosDatabaseName

@description('Cosmos DB account name — used as AZURE_COSMOS_ACCOUNT_NAME by hooks.')
output AZURE_COSMOS_ACCOUNT_NAME string = cosmos.outputs.cosmosAccountName

@description('AI Search index name — used as AZURE_SEARCH_INDEX by container app.')
output AZURE_SEARCH_INDEX string = searchIndexName

@description('Container App public URL — used as AZURE_CONTAINER_APP_URL by tests.')
output AZURE_CONTAINER_APP_URL string = 'https://${app.outputs.containerAppFqdn}'

@description('Managed identity client ID — used as AZURE_CLIENT_ID by tests.')
output AZURE_CLIENT_ID string = identity.outputs.clientId

@description('AI Search endpoint — used as AZURE_SEARCH_ENDPOINT by tests.')
output AZURE_SEARCH_ENDPOINT string = search.outputs.searchEndpoint

@description('AI Hub name — used as AZURE_AI_HUB_NAME by tests.')
output AZURE_AI_HUB_NAME string = foundry.outputs.aiHubName

@description('AI Project name — used as AZURE_AI_PROJECT_NAME by tests.')
output AZURE_AI_PROJECT_NAME string = foundry.outputs.aiProjectName

@description('AI Foundry project endpoint — used as AZURE_AI_PROJECT_ENDPOINT by agent runtime.')
output AZURE_AI_PROJECT_ENDPOINT string = foundry.outputs.aiProjectEndpoint
