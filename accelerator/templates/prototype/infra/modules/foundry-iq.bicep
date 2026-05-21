// ============================================================
// foundry-iq.bicep — Model deployments + Azure AI Search
//
// Adds the LLM deployments listed in `modelDeployments` plus a
// fixed `text-embedding-3-large` deployment (required by AI Search)
// as sub-resources of the AIServices hub account created by
// foundry.bicep. No separate OpenAI account is created.
//
// Having deployments on the hub account is required so that
// Microsoft Agent Framework's Responses API calls (which route
// through https://{hub}.services.ai.azure.com) can resolve them.
// ============================================================

@description('Base name prefix for all resources.')
param resourcePrefix string

@description('Azure region for AI Search.')
param location string

@description('Azure region for the AI Search service. Defaults to `location`. Override when the primary region is capacity-exhausted on the Search SKU.')
param searchLocation string = location

@description('Name of the AIServices hub account (output of foundry.bicep).')
param hubAccountName string

@description('Resource tags.')
param tags object = {}

@description('Principal ID of the user-assigned managed identity.')
param managedIdentityPrincipalId string

@description('LLM model deployments. Driven by spec.yaml.model_deployments. Each entry: { deploymentName, model, version, capacity, sku? }.')
param modelDeployments array

@description('Embedding model deployment capacity (TPM in thousands).')
param embeddingCapacity int = 10

@description('Embedding model name (kept fixed because AI Search vector indexing depends on it).')
param embeddingModelName string = 'text-embedding-3-large'

@description('Embedding model version.')
param embeddingModelVersion string = '1'

// ── Reference existing hub (created by foundry.bicep) ────────
resource hubAccount 'Microsoft.CognitiveServices/accounts@2025-04-01-preview' existing = {
  name: hubAccountName
}

// ── LLM deployments (looped from modelDeployments array) ─────
// Bicep schedules these in parallel; Azure serializes deployments
// on the same account internally, so no explicit dependsOn chain
// is required. If you hit throttling, reduce the array size.
@batchSize(1)
resource llmDeployments 'Microsoft.CognitiveServices/accounts/deployments@2025-04-01-preview' = [for m in modelDeployments: {
  parent: hubAccount
  name: m.deploymentName
  sku: {
    name: m.?sku ?? 'GlobalStandard'
    capacity: m.capacity
  }
  properties: {
    model: {
      format: 'OpenAI'
      name: m.model
      version: m.version
    }
    raiPolicyName: 'Microsoft.DefaultV2'
  }
}]

// ── Embedding deployment (always required for AI Search) ─────
resource embeddingDeployment 'Microsoft.CognitiveServices/accounts/deployments@2025-04-01-preview' = {
  parent: hubAccount
  name: embeddingModelName
  sku: {
    name: 'GlobalStandard'
    capacity: embeddingCapacity
  }
  properties: {
    model: {
      format: 'OpenAI'
      name: embeddingModelName
      version: embeddingModelVersion
    }
    raiPolicyName: 'Microsoft.DefaultV2'
  }
  dependsOn: [llmDeployments]
}

// ── Azure AI Search ──────────────────────────────────────────
// SKU 'basic' is the default for prototype workloads — cheaper and more
// likely to have capacity in any region. Bump to 'standard' if you need
// higher replica/partition counts or per-index storage > 2 GB.
resource searchService 'Microsoft.Search/searchServices@2023-11-01' = {
  name: '${resourcePrefix}-search'
  location: searchLocation
  tags: tags
  sku: {
    name: 'basic'
  }
  properties: {
    replicaCount: 1
    partitionCount: 1
    publicNetworkAccess: 'enabled'
    authOptions: {
      aadOrApiKey: {
        aadAuthFailureMode: 'http403'
      }
    }
  }
}

// Search Index Data Contributor → managed identity
resource searchIndexContributorRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(searchService.id, managedIdentityPrincipalId, 'Search Index Data Contributor')
  scope: searchService
  properties: {
    roleDefinitionId: subscriptionResourceId(
      'Microsoft.Authorization/roleDefinitions',
      '8ebe5a00-799e-43f5-93ac-243d3dce84a7'
    )
    principalId: managedIdentityPrincipalId
    principalType: 'ServicePrincipal'
  }
}

// Search Service Contributor → managed identity
resource searchServiceContributorRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(searchService.id, managedIdentityPrincipalId, 'Search Service Contributor')
  scope: searchService
  properties: {
    roleDefinitionId: subscriptionResourceId(
      'Microsoft.Authorization/roleDefinitions',
      '7ca78c08-252a-4471-8644-bb5ff32d4ba0'
    )
    principalId: managedIdentityPrincipalId
    principalType: 'ServicePrincipal'
  }
}

// ── Outputs ──────────────────────────────────────────────────
@description('AIServices hub endpoint (https://{name}.services.ai.azure.com/).')
output hubEndpoint string = 'https://${hubAccountName}.services.ai.azure.com/'

@description('Resource ID of the Azure AI Search service.')
output searchServiceId string = searchService.id

@description('Name of the Azure AI Search service.')
output searchServiceName string = searchService.name

@description('Endpoint URL of the Azure AI Search service.')
output searchEndpoint string = 'https://${searchService.name}.search.windows.net'
