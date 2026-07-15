// ============================================================
// search.bicep — Azure AI Search
//
// Provisions the Azure AI Search service used for the knowledge
// index, plus the RBAC role assignments the managed identity needs
// to manage indexes and query data.
//
// Deliberately has no dependency on the Foundry hub account (the
// vector-index-to-embedding-model wiring happens later, in Python,
// when postprovision creates the index) so ARM can provision Search
// in parallel with foundry.bicep / foundry-iq.bicep instead of
// serializing behind them.
// ============================================================

@description('Base name prefix for all resources.')
param resourcePrefix string

@description('Azure region for the AI Search service. Defaults to `location`. Override when the primary region is capacity-exhausted on the Search SKU.')
param location string

@description('AI Search SKU. `basic` is the default for prototype workloads (cheap, adequate for one index). Bump to `standard` when Basic is capacity-exhausted region-wide or you need multiple partitions / replicas. Never use `free` — its 3-index cap conflicts with the demo\'s knowledge index + potential future indexes.')
@allowed([
  'basic'
  'standard'
  'standard2'
  'standard3'
])
param searchSku string = 'basic'

@description('Resource tags.')
param tags object = {}

@description('Principal ID of the user-assigned managed identity.')
param managedIdentityPrincipalId string

// ── Azure AI Search ──────────────────────────────────────────
// SKU is controlled by the `searchSku` param (default 'basic'). Bump to
// 'standard' when Basic is capacity-exhausted region-wide — e.g. via
// `azd env set AZURE_SEARCH_SKU standard` before rerunning `azd up`.
// Higher SKUs give more replica/partition counts and per-index storage,
// at proportionally higher cost.
resource searchService 'Microsoft.Search/searchServices@2025-05-01' = {
  name: '${resourcePrefix}-search'
  location: location
  tags: tags
  sku: {
    name: searchSku
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
@description('Resource ID of the Azure AI Search service.')
output searchServiceId string = searchService.id

@description('Name of the Azure AI Search service.')
output searchServiceName string = searchService.name

@description('Endpoint URL of the Azure AI Search service.')
output searchEndpoint string = 'https://${searchService.name}.search.windows.net'
