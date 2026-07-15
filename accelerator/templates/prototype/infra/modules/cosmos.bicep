// ============================================================
// cosmos.bicep — Azure Cosmos DB (NoSQL API)
// AVM module:
//   br/public:avm/res/document-db/database-account:0.19.0
//
// Auth: RBAC only (key-based auth disabled)
// The managed identity is granted Cosmos DB Built-in Data Contributor.
// ============================================================

@description('Base name prefix for all resources.')
param resourcePrefix string

@description('Azure region for all resources.')
param location string

@description('Resource tags.')
param tags object = {}

@description('Name of the initial Cosmos DB database.')
param databaseName string = 'prototype-db'

// ── Cosmos DB Account ─────────────────────────────────────────
module cosmosAccount 'br/public:avm/res/document-db/database-account:0.19.0' = {
  name: 'deploy-cosmos-account'
  params: {
    name: '${resourcePrefix}-cosmos'
    location: location
    tags: tags
    // NoSQL API (default)
    databaseAccountOfferType: 'Standard'
    // Keep local auth enabled so postprovision hook can seed via CLI credential
    disableLocalAuthentication: false
    // Serverless — no throughput cost for demos
    capabilitiesToAdd: [
      'EnableServerless'
    ]
    // Single-region — sufficient for demos
    failoverLocations: [
      {
        locationName: location
        failoverPriority: 0
        isZoneRedundant: false
      }
    ]
    // SQL databases
    sqlDatabases: [
      {
        name: databaseName
        containers: []  // Containers are created by postprovision hook (spec-driven)
      }
    ]
    // Allow metadata writes (create/delete containers) through data-plane SDK.
    // Without this, container creation via Python SDK is blocked even with RBAC.
    disableKeyBasedMetadataWriteAccess: false
    // Keep network open so postprovision hook can seed data without DNS propagation delays.
    // The AVM default is publicNetworkAccess: 'Disabled' which resets on every provision
    // and causes regional endpoint DNS failures in the seed script.
    // networkAclBypass: 'AzureServices' allows Container Apps + other Azure services to connect
    // even if IP-based firewall rules would otherwise block them.
    networkRestrictions: {
      publicNetworkAccess: 'Enabled'
      networkAclBypass: 'AzureServices'
    }
    // NOTE: Cosmos DB data-plane RBAC is assigned imperatively in postprovision.ps1
    // using 'az cosmosdb sql role assignment create' because the AVM module passes
    // the short GUID directly to the API without constructing the full resource path.
  }
}

// ── Outputs ──────────────────────────────────────────────────
@description('Cosmos DB account name.')
output cosmosAccountName string = cosmosAccount.outputs.name

@description('Cosmos DB endpoint URI.')
output cosmosEndpoint string = cosmosAccount.outputs.endpoint

@description('Cosmos DB account resource ID (needed for data-plane RBAC scope).')
output cosmosAccountId string = cosmosAccount.outputs.resourceId

@description('Cosmos DB database name.')
output cosmosDatabaseName string = databaseName
