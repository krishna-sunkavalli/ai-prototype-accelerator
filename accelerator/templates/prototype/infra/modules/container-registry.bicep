// ============================================================
// container-registry.bicep — Azure Container Registry
// AVM module: br/public:avm/res/container-registry/registry:0.12.1
// ============================================================

@description('Base name prefix for all resources.')
@minLength(3)
param resourcePrefix string

@description('Azure region for all resources.')
param location string

@description('Resource tags.')
param tags object = {}

@description('Principal ID of the user-assigned managed identity (to grant AcrPull).')
param managedIdentityPrincipalId string

// ── Container Registry ───────────────────────────────────────
// ACR names must be globally unique, 5-50 lowercase alphanumeric only.
// Name is computed here; caller must ensure resourcePrefix has ≥ 3 chars.
// 'acr' suffix added so minimum possible length = len(resourcePrefix)+3 ≥ 6.
var acrName = toLower(replace(take('${resourcePrefix}acr', 50), '-', ''))

resource registry 'Microsoft.ContainerRegistry/registries@2023-07-01' = {
  name: acrName
  location: location
  tags: tags
  sku: {
    name: 'Basic'
  }
  properties: {
    adminUserEnabled: false
    publicNetworkAccess: 'Enabled'
  }
}

var acrPullRoleId = '7f951dda-4ed3-4680-a7ca-43fe172d538d'

resource acrPull 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(registry.id, managedIdentityPrincipalId, acrPullRoleId)
  scope: registry
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', acrPullRoleId)
    principalId: managedIdentityPrincipalId
    principalType: 'ServicePrincipal'
  }
}

// ── Outputs ──────────────────────────────────────────────────
@description('Resource ID of the container registry.')
output registryId string = registry.id

@description('Name of the container registry.')
output registryName string = registry.name

@description('Login server URL of the container registry.')
output loginServer string = registry.properties.loginServer
