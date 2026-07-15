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
module environment 'br/public:avm/res/app/managed-environment:0.13.3' = {
  name: 'deploy-cae'
  params: {
    name: '${resourcePrefix}-cae'
    location: location
    tags: tags
    appInsightsConnectionString: appInsightsConnectionString
    zoneRedundant: false
    publicNetworkAccess: 'Enabled'
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
