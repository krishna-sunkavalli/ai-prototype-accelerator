// ============================================================
// search.bicep — Azure AI Search service + managed-identity roles
//
// Deliberately its own module: Search has no dependency on the AI
// Foundry hub, so provisioning it here lets ARM create it in parallel
// with the Foundry account instead of serialized behind it (it was
// previously bundled into foundry-iq, whose hubAccountName parameter
// forced the whole module to wait ~2-5 minutes for Foundry).
//
// SKU is controlled by the `searchSku` param (default 'basic'). Bump to
// 'standard' when Basic is capacity-exhausted region-wide — e.g. via
// `azd env set AZURE_SEARCH_SKU standard` before rerunning `azd up`.
// Higher SKUs give more replica/partition counts and per-index storage,
// at proportionally higher cost.
// ============================================================

@description('Base name prefix for all resources.')
param resourcePrefix string

@description('Azure region for the AI Search service.')
param searchLocation string

@description('AI Search SKU. `basic` is the default for prototype workloads (cheap, adequate for one index). Never use `free` — its 3-index cap conflicts with the demo\'s knowledge index + potential future indexes.')
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

resource searchService 'Microsoft.Search/searchServices@2025-05-01' = {
  name: '${resourcePrefix}-search'
  location: searchLocation
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
