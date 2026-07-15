// ============================================================
// monitoring.bicep — Log Analytics Workspace + Application Insights
// AVM modules:
//   br/public:avm/res/operational-insights/workspace:0.15.1
//   br/public:avm/res/insights/component:0.7.2
// ============================================================

@description('Base name prefix for all resources (customerName-demoTheme).')
param resourcePrefix string

@description('Azure region for all resources.')
param location string

@description('Resource tags to apply to all resources.')
param tags object = {}

// ── Log Analytics Workspace ──────────────────────────────────
module logAnalytics 'br/public:avm/res/operational-insights/workspace:0.15.1' = {
  name: 'deploy-log-analytics'
  params: {
    name: '${resourcePrefix}-log'
    location: location
    tags: tags
    skuName: 'PerGB2018'
    dataRetention: 30
  }
}

// ── Application Insights ─────────────────────────────────────
module appInsights 'br/public:avm/res/insights/component:0.7.2' = {
  name: 'deploy-app-insights'
  params: {
    name: '${resourcePrefix}-appi'
    location: location
    tags: tags
    workspaceResourceId: logAnalytics.outputs.resourceId
    applicationType: 'web'
    kind: 'web'
  }
}

// ── Outputs ──────────────────────────────────────────────────
@description('Resource ID of the Log Analytics workspace.')
output logAnalyticsWorkspaceId string = logAnalytics.outputs.resourceId

@description('Name of the Log Analytics workspace.')
output logAnalyticsWorkspaceName string = logAnalytics.outputs.name

@description('Resource ID of Application Insights.')
output appInsightsId string = appInsights.outputs.resourceId

@description('Name of Application Insights.')
output appInsightsName string = appInsights.outputs.name

@description('Instrumentation key of Application Insights.')
output appInsightsInstrumentationKey string = appInsights.outputs.instrumentationKey

@description('Connection string of Application Insights.')
output appInsightsConnectionString string = appInsights.outputs.connectionString
