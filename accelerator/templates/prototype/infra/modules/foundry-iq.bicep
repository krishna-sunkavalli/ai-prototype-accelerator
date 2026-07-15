// ============================================================
// foundry-iq.bicep — Model deployments
//
// Adds the LLM deployments listed in `modelDeployments` plus a
// fixed `text-embedding-3-large` deployment (required by AI Search's
// vector index, wired up later in Python by postprovision) as
// sub-resources of the AIServices hub account created by
// foundry.bicep. No separate OpenAI account is created.
//
// Having deployments on the hub account is required so that
// Microsoft Agent Framework's Responses API calls (which route
// through https://{hub}.services.ai.azure.com) can resolve them.
//
// Azure AI Search itself lives in search.bicep — kept out of this
// module so ARM can provision it in parallel with the hub account
// instead of serializing behind it.
// ============================================================

@description('Name of the AIServices hub account (output of foundry.bicep).')
param hubAccountName string

@description('LLM model deployments. Driven by spec.yaml.model_deployments. Each entry: { deploymentName, model, version, capacity, sku? }.')
param modelDeployments array

@description('Embedding model deployment capacity (TPM in thousands).')
param embeddingCapacity int = 10

@description('Embedding model name (kept fixed because AI Search vector indexing depends on it).')
param embeddingModelName string = 'text-embedding-3-large'

@description('Embedding model version.')
param embeddingModelVersion string = '1'

// ── Reference existing hub (created by foundry.bicep) ────────
resource hubAccount 'Microsoft.CognitiveServices/accounts@2026-03-01' existing = {
  name: hubAccountName
}

// ── LLM deployments (looped from modelDeployments array) ─────
// Bicep schedules these in parallel; Azure serializes deployments
// on the same account internally, so no explicit dependsOn chain
// is required. If you hit throttling, reduce the array size.
//
// Filter out any entry whose deploymentName matches the embedding model —
// that deployment is owned by the fixed `embeddingDeployment` resource
// below (AI Search vector indexing depends on it). Without this filter,
// specs that declare `text-embedding-3-large` in model_deployments (which
// is the canonical spec.yaml template default) trigger InvalidTemplate:
// "resource ... deployments/text-embedding-3-large is defined multiple
// times in a template".
var llmOnlyModelDeployments = filter(modelDeployments, m => m.deploymentName != embeddingModelName)

@batchSize(1)
resource llmDeployments 'Microsoft.CognitiveServices/accounts/deployments@2026-03-01' = [for m in llmOnlyModelDeployments: {
  parent: hubAccount
  name: m.deploymentName
  sku: {
    name: m.?sku ?? 'GlobalStandard'
    capacity: m.capacity
  }
  properties: {
    model: {
      format: 'OpenAI'
      name: m.model
      version: m.version
    }
    raiPolicyName: 'Microsoft.DefaultV2'
  }
}]

// ── Embedding deployment (always required for AI Search) ─────
// `dependsOn: llmDeployments` serializes embedding after all LLM
// deployments finish. Without this, empirically (Hudson Advisors build,
// 2026-07-13) the embedding create races with the last LLM create and
// Azure returns `RequestConflict: Another operation is being performed
// on the parent resource ...`. That failure surfaces as an unrecoverable
// azd provision error even though the resource eventually creates
// successfully — leaving azd unable to emit deployment outputs. The
// couple of seconds we lose to serializing is worth the reliability.
resource embeddingDeployment 'Microsoft.CognitiveServices/accounts/deployments@2026-03-01' = {
  parent: hubAccount
  name: embeddingModelName
  sku: {
    name: 'GlobalStandard'
    capacity: embeddingCapacity
  }
  properties: {
    model: {
      format: 'OpenAI'
      name: embeddingModelName
      version: embeddingModelVersion
    }
    raiPolicyName: 'Microsoft.DefaultV2'
  }
  dependsOn: [
    llmDeployments
  ]
}

// ── Outputs ──────────────────────────────────────────────────
@description('AIServices hub endpoint (https://{name}.services.ai.azure.com/).')
output hubEndpoint string = 'https://${hubAccountName}.services.ai.azure.com/'
