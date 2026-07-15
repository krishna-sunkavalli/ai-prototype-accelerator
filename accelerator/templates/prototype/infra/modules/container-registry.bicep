// ============================================================
// container-registry.bicep — Azure Container Registry
// AVM module: br/public:avm/res/container-registry/registry:0.12.1
//
// Basic SKU (prototype default) implies public network access is
// always Enabled — the AVM `publicNetworkAccess` and network-rules
// parameters require Premium SKU and are intentionally omitted.
// Bumping to Premium is a spec-level decision, not a template one.
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

// ACR names must be globally unique, 5-50 lowercase alphanumeric only.
// Name is computed here; caller must ensure resourcePrefix has ≥ 3 chars.
// 'acr' suffix added so minimum possible length = len(resourcePrefix)+3 ≥ 6.
var acrName = toLower(replace(take('${resourcePrefix}acr', 50), '-', ''))

module registry 'br/public:avm/res/container-registry/registry:0.12.1' = {
  name: 'deploy-acr'
  params: {
    name: acrName
    location: location
    tags: tags
    acrSku: 'Basic'
    acrAdminUserEnabled: false
    // AVM grants AcrPull to the managed identity via its own role-assignment
    // block; the built-in role name is resolved internally so we don't need
    // to pass the role definition GUID.
    roleAssignments: [
      {
        principalId: managedIdentityPrincipalId
        principalType: 'ServicePrincipal'
        roleDefinitionIdOrName: 'AcrPull'
      }
    ]
  }
}

// ── Outputs ──────────────────────────────────────────────────
@description('Resource ID of the container registry.')
output registryId string = registry.outputs.resourceId

@description('Name of the container registry.')
output registryName string = registry.outputs.name

@description('Login server URL of the container registry.')
output loginServer string = registry.outputs.loginServer
