// ============================================================
// container-app.bicep — Azure Container Apps Environment + App
// AVM modules:
//   br/public:avm/res/app/managed-environment:0.13.3
//   br/public:avm/res/app/container-app:0.23.0
// ============================================================

@description('Base name prefix for all resources.')
param resourcePrefix string

@description('Azure region for all resources.')
param location string

@description('Resource tags.')
param tags object = {}

@description('Application Insights connection string (used for Container App Environment logging).')
param appInsightsConnectionString string

@description('Resource ID of the Log Analytics workspace to forward console/system logs to. Empty string omits appLogsConfiguration (platform default, which does not forward logs anywhere queryable).')
param logAnalyticsWorkspaceResourceId string = ''

@description('Resource ID of the user-assigned managed identity.')
param managedIdentityResourceId string

@description('Client ID of the user-assigned managed identity (for workload identity).')
param managedIdentityClientId string

@description('Container image to deploy (e.g. ghcr.io/your-org/your-agent:latest).')
param containerImage string = 'ghcr.io/PLACEHOLDER_ORG/PLACEHOLDER_REPO:latest'

@description('Azure Cosmos DB endpoint URI.')
param cosmosEndpoint string

@description('Cosmos DB account name.')
param cosmosAccountName string

@description('Name of the Cosmos DB database.')
param cosmosDatabaseName string

@description('Azure AI Search endpoint URL.')
param searchEndpoint string

@description('Azure AI Search index name.')
param searchIndexName string = 'prototype-index'

@description('ACR login server (e.g. myregistry.azurecr.io). Used to configure MI-based pull auth.')
param acrLoginServer string = ''

@description('Azure Storage account name (not the blob endpoint).')
param storageAccountName string

@description('Display name of the user-assigned managed identity.')
param miDisplayName string

// ── Branding params (populated from spec.yaml at Step 2b) ────
@description('Customer display name shown in the UI header.')
param customerDisplayName string = 'AI Prototype'

@description('Agent name shown in the UI (e.g. Watts, Ada, Max).')
param agentName string = 'AI Assistant'

@description('Primary brand color (hex, e.g. #0072CE).')
param primaryColor string = '#0078D4'

@description('Accent brand color (hex, e.g. #78BE20).')
param accentColor string = '#50E6FF'

@description('Font family for the UI.')
param fontFamily string = 'Arial, sans-serif'

@description('URL to the customer logo image.')
param logoUrl string = ''

@description('Welcome message shown in the chat UI.')
param welcomeMessage string = 'Hello! How can I help you today?'

@description('Use case title shown as agent subtitle (e.g. Energy Theft Investigation).')
param useCaseTitle string = ''

@description('Starter question chips shown in the UI (JSON array string).')
param starterQuestions string = '[]'

@description('Demo persona display name shown in the user profile (e.g. Sarah Chen).')
param personaName string = 'User'

@description('Demo persona role shown under the profile name (e.g. Chief Financial Officer).')
param personaRole string = 'Viewer'

@description('AI Foundry project endpoint for Microsoft Agent Framework (AIProjectClient).')
param aiProjectEndpoint string = ''

@description('Minimum number of replicas (0 = scale to zero).')
param minReplicas int = 0

@description('Maximum number of replicas.')
param maxReplicas int = 5

// ── Container Apps Environment ───────────────────────────────
// Console/system log routing has TWO mutually exclusive mechanisms, and
// mixing them silently breaks the pipe (Alliant build, 2026-07-17):
//   1. destination 'log-analytics' (+ workspace key) writes directly to
//      the CUSTOM tables `ContainerAppConsoleLogs_CL` /
//      `ContainerAppSystemLogs_CL`; diagnostic settings are IGNORED in
//      this mode.
//   2. destination 'azure-monitor' emits through Azure Monitor, and a
//      diagnostic-settings resource on the environment routes the
//      STANDARD tables `ContainerAppConsoleLogs`/`ContainerAppSystemLogs`
//      to a workspace. Microsoft: "If you selected Azure Monitor as your
//      logs destination, you must also configure the diagnostic settings."
// This template previously set destination 'log-analytics' AND created
// diagnostic settings — so the diagnostic setting never emitted, and the
// standard tables stayed at 0 rows forever. Use 'azure-monitor' so the
// diagnostic setting below is the single, real routing mechanism and
// logs land in the standard tables everyone queries.
module environment 'br/public:avm/res/app/managed-environment:0.13.3' = {
  name: 'deploy-cae'
  params: {
    name: '${resourcePrefix}-cae'
    location: location
    tags: tags
    appInsightsConnectionString: appInsightsConnectionString
    appLogsConfiguration: empty(logAnalyticsWorkspaceResourceId) ? null : {
      destination: 'azure-monitor'
    }
    zoneRedundant: false
    publicNetworkAccess: 'Enabled'
  }
}

// The diagnostic-settings resource that actually routes
// `ContainerAppConsoleLogs`/`ContainerAppSystemLogs` to the workspace
// when destination is 'azure-monitor' (see block comment above).
// Diagnostic settings for log categories can only be created at the
// ENVIRONMENT level -- the container-app-level diagnostic-settings API
// only accepts a metrics category (`AllMetrics`), it rejects
// `ContainerAppConsoleLogs`/`ContainerAppSystemLogs` with a 400. See
// RESOLVED.md.
resource caeExisting 'Microsoft.App/managedEnvironments@2025-01-01' existing = if (!empty(logAnalyticsWorkspaceResourceId)) {
  name: '${resourcePrefix}-cae'
  dependsOn: [
    environment
  ]
}

resource caeDiagnostics 'Microsoft.Insights/diagnosticSettings@2021-05-01-preview' = if (!empty(logAnalyticsWorkspaceResourceId)) {
  name: 'cae-console-logs'
  scope: caeExisting
  properties: {
    workspaceId: logAnalyticsWorkspaceResourceId
    logs: [
      { category: 'ContainerAppConsoleLogs', enabled: true }
      { category: 'ContainerAppSystemLogs', enabled: true }
    ]
  }
}

// ── Container App ────────────────────────────────────────────
module containerApp 'br/public:avm/res/app/container-app:0.23.0' = {
  name: 'deploy-ca'
  params: {
    name: '${take(resourcePrefix, 29)}-ca'
    location: location
    tags: union(tags, {
      'azd-service-name': 'app'
    })
    environmentResourceId: environment.outputs.resourceId
    managedIdentities: {
      userAssignedResourceIds: [
        managedIdentityResourceId
      ]
    }
    ingressExternal: true
    ingressTargetPort: 80
    ingressTransport: 'http'
    registries: empty(acrLoginServer) ? [] : [
      {
        server: acrLoginServer
        identity: managedIdentityResourceId
      }
    ]
    scaleSettings: {
      minReplicas: minReplicas
      maxReplicas: maxReplicas
    }
    containers: [
      {
        name: 'agent'
        image: containerImage
        resources: {
          cpu: json('0.5')
          memory: '1Gi'
        }
        env: [
          {
            name: 'AZURE_CLIENT_ID'
            value: managedIdentityClientId
          }
          {
            name: 'AZURE_COSMOS_ENDPOINT'
            value: cosmosEndpoint
          }
          {
            name: 'AZURE_COSMOS_DATABASE'
            value: cosmosDatabaseName
          }
          {
            name: 'AZURE_COSMOS_ACCOUNT_NAME'
            value: cosmosAccountName
          }
          {
            name: 'AZURE_STORAGE_ACCOUNT_NAME'
            value: storageAccountName
          }
          {
            name: 'AZURE_MI_DISPLAY_NAME'
            value: miDisplayName
          }
          {
            name: 'AZURE_SEARCH_ENDPOINT'
            value: searchEndpoint
          }
          {
            name: 'AZURE_SEARCH_INDEX'
            value: searchIndexName
          }
          {
            name: 'APPLICATIONINSIGHTS_CONNECTION_STRING'
            value: appInsightsConnectionString
          }
          {
            name: 'CUSTOMER_NAME'
            value: customerDisplayName
          }
          {
            name: 'AGENT_NAME'
            value: agentName
          }
          {
            name: 'PRIMARY_COLOR'
            value: primaryColor
          }
          {
            name: 'ACCENT_COLOR'
            value: accentColor
          }
          {
            name: 'FONT_FAMILY'
            value: fontFamily
          }
          {
            name: 'LOGO_URL'
            value: logoUrl
          }
          {
            name: 'WELCOME_MESSAGE'
            value: welcomeMessage
          }
          {
            name: 'USE_CASE_TITLE'
            value: useCaseTitle
          }
          {
            name: 'STARTER_QUESTIONS'
            value: starterQuestions
          }
          {
            name: 'PERSONA_NAME'
            value: personaName
          }
          {
            name: 'PERSONA_ROLE'
            value: personaRole
          }
          {
            name: 'AZURE_AI_PROJECT_ENDPOINT'
            value: aiProjectEndpoint
          }
        ]
      }
    ]
  }
}

// ── Outputs ──────────────────────────────────────────────────
@description('Resource ID of the Container Apps Environment.')
output environmentId string = environment.outputs.resourceId

@description('Name of the Container Apps Environment.')
output environmentName string = environment.outputs.name

@description('Resource ID of the Container App.')
output containerAppId string = containerApp.outputs.resourceId

@description('Name of the Container App.')
output containerAppName string = containerApp.outputs.name

@description('FQDN of the Container App ingress.')
output containerAppFqdn string = containerApp.outputs.fqdn
