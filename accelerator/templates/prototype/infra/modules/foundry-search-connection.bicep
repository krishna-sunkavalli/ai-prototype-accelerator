// ── Foundry project → Azure AI Search / Foundry IQ connections ────
// Two distinct connections, for two distinct purposes:
//
// 1. `searchConnection` (category: CognitiveSearch, authType: AAD) —
//    registers the Search resource on the Foundry project so the
//    portal's Knowledge (Foundry IQ) blade can discover/manage the
//    knowledge base created by postprovision. Portal-visibility only.
//
// 2. `mcpConnection` (category: RemoteTool, authType: ProjectManagedIdentity) —
//    the wiring agents actually need to call the knowledge base's
//    `knowledge_base_retrieve` MCP tool natively (register_agents.py
//    attaches an MCPTool referencing this connection's name). This is
//    the mechanism documented at
//    https://learn.microsoft.com/azure/foundry/agents/how-to/foundry-iq-connect
//    and is what the portal's "Connect to Foundry IQ" button creates
//    when done by hand. `ProjectManagedIdentity`/`RemoteTool` are not
//    yet reflected in the published Bicep type schema for this API
//    version (as of 2026-07), but ARM accepts them — verified against
//    a live deployment.
//
// Both connections need the project's system-assigned identity to
// have `Search Index Data Reader` on the Search service (granted here)
// so the MCP tool's retrieval calls succeed at runtime.

param aiHubName string
param aiProjectName string
param searchServiceName string
param searchEndpoint string
param searchIndexName string

var knowledgeBaseName = '${searchIndexName}-kb'
var mcpApiVersion = '2026-05-01-preview'

resource aiHub 'Microsoft.CognitiveServices/accounts@2026-03-01' existing = {
  name: aiHubName
}

resource foundryProject 'Microsoft.CognitiveServices/accounts/projects@2026-03-01' existing = {
  parent: aiHub
  name: aiProjectName
}

resource searchService 'Microsoft.Search/searchServices@2025-05-01' existing = {
  name: searchServiceName
}

resource searchConnection 'Microsoft.CognitiveServices/accounts/projects/connections@2025-10-01-preview' = {
  parent: foundryProject
  name: searchServiceName
  properties: {
    category: 'CognitiveSearch'
    target: searchEndpoint
    authType: 'AAD'
    isSharedToAll: true
    metadata: {
      ApiType: 'Azure'
      ResourceId: resourceId('Microsoft.Search/searchServices', searchServiceName)
    }
  }
}

resource mcpConnection 'Microsoft.CognitiveServices/accounts/projects/connections@2025-10-01-preview' = {
  parent: foundryProject
  name: '${searchIndexName}-mcp'
  // `any()` bypasses Bicep's compile-time authType/category union check.
  // `RemoteTool` / `ProjectManagedIdentity` are accepted by ARM for this
  // resource type but not yet published in the Bicep type schema (verified
  // against a live deployment on 2026-07-16).
  properties: any({
    category: 'RemoteTool'
    authType: 'ProjectManagedIdentity'
    target: '${searchEndpoint}/knowledgebases/${knowledgeBaseName}/mcp?api-version=${mcpApiVersion}'
    isSharedToAll: true
    audience: 'https://search.azure.com/'
    metadata: {
      ApiType: 'Azure'
    }
  })
}

// The MCP tool calls the knowledge base using the PROJECT's own
// managed identity (authType: ProjectManagedIdentity above) — grant it
// read access to the search service's indexes.
resource roleAssignmentSearchIndexReader 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(searchService.id, foundryProject.id, 'Search Index Data Reader')
  scope: searchService
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '1407120a-92aa-4202-b7e9-c0e197c71c8f')
    principalId: foundryProject.identity.principalId
    principalType: 'ServicePrincipal'
  }
}

output mcpConnectionName string = mcpConnection.name
output knowledgeBaseName string = knowledgeBaseName
