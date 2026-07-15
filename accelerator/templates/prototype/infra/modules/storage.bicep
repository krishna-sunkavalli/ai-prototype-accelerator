// ============================================================
// storage.bicep — Azure Blob Storage (GPv2)
// AVM module: br/public:avm/res/storage/storage-account:0.32.1
// ============================================================

@description('Base name prefix for all resources.')
param resourcePrefix string

@description('Azure region for all resources.')
param location string

@description('Resource tags.')
param tags object = {}

@description('Resource ID of the user-assigned managed identity.')
param managedIdentityPrincipalId string

// ── Storage Account ──────────────────────────────────────────
// Name must be 3-24 lowercase alphanumeric characters only — strip hyphens.
var storageAccountName = take(toLower(replace('${resourcePrefix}st', '-', '')), 24)

module storageAccount 'br/public:avm/res/storage/storage-account:0.32.1' = {
  name: 'deploy-storage'
  params: {
    name: storageAccountName
    location: location
    tags: tags
    skuName: 'Standard_LRS'
    kind: 'StorageV2'
    accessTier: 'Hot'
    allowBlobPublicAccess: false
    supportsHttpsTrafficOnly: true
    minimumTlsVersion: 'TLS1_2'
    allowSharedKeyAccess: false
    blobServices: {
      containers: [
        {
          name: 'demo-data'
          publicAccess: 'None'
        }
        {
          name: 'agent-state'
          publicAccess: 'None'
        }
      ]
    }
    roleAssignments: [
      {
        principalId: managedIdentityPrincipalId
        roleDefinitionIdOrName: 'Storage Blob Data Contributor'
        principalType: 'ServicePrincipal'
      }
    ]
  }
}

// ── Outputs ──────────────────────────────────────────────────
@description('Resource ID of the storage account.')
output storageAccountId string = storageAccount.outputs.resourceId

@description('Name of the storage account.')
output storageAccountName string = storageAccount.outputs.name

@description('Primary blob endpoint of the storage account.')
output storageBlobEndpoint string = storageAccount.outputs.primaryBlobEndpoint
