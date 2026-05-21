// ============================================================
// foundry.bicep — Azure AI Foundry Hub + Project
//
// Creates a single AIServices account that serves as both the
// Foundry hub and the model host. Direct resource declaration
// (not AVM module) so we can set allowProjectManagement: true,
// which is required for Foundry project child resources.
//
// Model deployments are handled by foundry-iq.bicep, which
// references this hub account by name via hubAccountName output.
// ============================================================

@description('Base name prefix for all resources.')
param resourcePrefix string

@description('Azure region for all non-AI resources.')
param location string

@description('Azure region for AI Foundry hub + model deployments. Override when your primary region lacks GlobalStandard quota (e.g. use eastus or swedencentral).')
param aiLocation string = location

@description('Resource tags.')
param tags object = {}

@description('Principal ID of the user-assigned managed identity.')
param managedIdentityPrincipalId string

// Well-known role definition IDs (subscription-scoped)
var roleAiDeveloper = '64702f94-c441-49e6-a78b-ef80e0188fee'
var roleCognitiveServicesOpenAiUser = '5e0bd9bd-7b93-4f28-af87-19fc36ad61bd'
// Azure AI Administrator — only built-in role widely available in all tenants
// that includes the new `Microsoft.CognitiveServices/accounts/AIServices/agents/write`
// data action required by the Foundry V2 Responses-API agents endpoint.
// `Azure AI Developer` does NOT include this action; chat returns 401 without this role.
var roleAiAdministrator = 'b78c5d69-af96-48a3-bf8d-a8b4d589de94'
// Foundry User — grants `Microsoft.CognitiveServices/*` data action. This is the
// role whose dataAction wildcard covers the project-scoped Responses API path
// (`accounts/projects/openai/v1/responses/action`). Azure AI Developer and
// Cognitive Services OpenAI User only cover `accounts/OpenAI/*` and therefore
// return 401 PermissionDenied for MAF FoundryAgent.run() calls on Foundry V2
// projects. This was the actual missing role observed at runtime.
var roleFoundryUser = '53ca6127-db72-4b80-b1b0-d745d6d5456d'

// ── AIServices Hub Account ────────────────────────────────────
// kind: AIServices ensures the Foundry Responses API endpoint
// (https://{name}.services.ai.azure.com) resolves model deployments
// on this same account. allowProjectManagement: true is mandatory
// for Foundry project child resources.
resource aiHub 'Microsoft.CognitiveServices/accounts@2025-04-01-preview' = {
  name: '${resourcePrefix}-hub'
  location: aiLocation
  tags: tags
  kind: 'AIServices'
  sku: {
    name: 'S0'
  }
  identity: {
    type: 'SystemAssigned'
  }
  properties: {
    customSubDomainName: toLower(replace('${resourcePrefix}-hub', '-', ''))
    publicNetworkAccess: 'Enabled'
    restrictOutboundNetworkAccess: false
    disableLocalAuth: true
    allowProjectManagement: true
  }
}

// Azure AI Developer — required by MAF AIProjectClient
resource roleAssignmentAiDeveloper 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(aiHub.id, managedIdentityPrincipalId, roleAiDeveloper)
  scope: aiHub
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roleAiDeveloper)
    principalId: managedIdentityPrincipalId
    principalType: 'ServicePrincipal'
  }
}

// Cognitive Services OpenAI User — required for Responses API calls
resource roleAssignmentOpenAiUser 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(aiHub.id, managedIdentityPrincipalId, roleCognitiveServicesOpenAiUser)
  scope: aiHub
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roleCognitiveServicesOpenAiUser)
    principalId: managedIdentityPrincipalId
    principalType: 'ServicePrincipal'
  }
}

// Azure AI Administrator on the hub account — required so MAF can invoke
// PromptAgents via the V2 Responses API (`agents/write` data action).
resource roleAssignmentAiAdminHub 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(aiHub.id, managedIdentityPrincipalId, roleAiAdministrator)
  scope: aiHub
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roleAiAdministrator)
    principalId: managedIdentityPrincipalId
    principalType: 'ServicePrincipal'
  }
}

// Foundry User on the hub account — the ACTUAL role whose data-action wildcard
// (`Microsoft.CognitiveServices/*`) covers the project-scoped Responses API.
// Without this, MAF FoundryAgent.run() returns 401 PermissionDenied even when
// the MI has Azure AI Developer + Azure AI Administrator + Cognitive Services
// OpenAI User. Verified at runtime against swedencentral.
resource roleAssignmentFoundryUser 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(aiHub.id, managedIdentityPrincipalId, roleFoundryUser)
  scope: aiHub
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roleFoundryUser)
    principalId: managedIdentityPrincipalId
    principalType: 'ServicePrincipal'
  }
}

// ── Foundry Project (sub-resource of hub) ────────────────────
// parent: aiHub provides implicit dependsOn — ARM will not attempt
// to create the project until the hub resource is fully provisioned.
resource foundryProject 'Microsoft.CognitiveServices/accounts/projects@2025-04-01-preview' = {
  parent: aiHub
  name: '${resourcePrefix}-proj'
  location: aiLocation
  tags: tags
  identity: {
    type: 'SystemAssigned'
  }
  properties: {
    description: 'AI Prototype Foundry project'
    displayName: resourcePrefix
  }
}

// Azure AI Administrator on the project sub-resource — V2 data-plane checks
// the project scope, not just the hub. Hub-only assignment is insufficient.
resource roleAssignmentAiAdminProject 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(foundryProject.id, managedIdentityPrincipalId, roleAiAdministrator)
  scope: foundryProject
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roleAiAdministrator)
    principalId: managedIdentityPrincipalId
    principalType: 'ServicePrincipal'
  }
}

// ── Outputs ──────────────────────────────────────────────────
@description('Name of the AIServices hub account.')
output aiHubName string = aiHub.name

@description('Name of the Foundry project.')
output aiProjectName string = foundryProject.name

@description('Custom subdomain name of the AIServices hub (no dashes, used in services.ai.azure.com hostname).')
output aiHubSubdomain string = aiHub.properties.customSubDomainName

@description('Base AIServices endpoint (https://{customSubDomain}.services.ai.azure.com/). Uses customSubDomainName — the resource name with dashes does NOT work for services.ai.azure.com.')
output aiServicesEndpoint string = 'https://${aiHub.properties.customSubDomainName}.services.ai.azure.com/'

@description('Foundry project endpoint for AIProjectClient (Microsoft Agent Framework).')
output aiProjectEndpoint string = 'https://${aiHub.properties.customSubDomainName}.services.ai.azure.com/api/projects/${foundryProject.name}/'
