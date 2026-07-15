// ============================================================
// main.bicepparam — ai-prototype-accelerator
//
// Update customerName, location, and demoTheme for each customer demo.
// All other parameters have sensible defaults.
// ============================================================

using './main.bicep'

// ── Required: customize per customer ─────────────────────────

// Short customer name used as resource prefix (e.g. 'contoso', 'fabrikam').
// Allowed: lowercase letters and hyphens, 2-20 characters.
// Derived from customer.slug in spec.yaml; auto-shortened when slug is too long.
param customerName = '{{CUSTOMER_SHORT_BICEP}}'
param environmentName = '{{ENVIRONMENT_NAME}}'

// Azure region for all resources.
// Defaults to the region declared in spec.yaml, but can be overridden per
// deployment with `azd env set AZURE_LOCATION <region>` — useful when the
// spec-declared region hits capacity issues for AI Search or Foundry.
param location = readEnvironmentVariable('AZURE_LOCATION', '{{AZURE_REGION}}')

// Short demo theme slug (e.g. 'loan-approval-ai', 'inventory-agent').
// Allowed: lowercase letters, numbers, and hyphens, 2-30 characters.
param demoTheme = '{{DEMO_THEME_BICEP}}'

// ── Optional: change defaults only when needed ────────────────

// Placeholder image — replaced automatically by azd deploy (builds from Dockerfile, pushes to ACR).
param containerImage = 'mcr.microsoft.com/azuredocs/containerapps-helloworld:latest'

// Name of the initial Cosmos DB database.
param databaseName = '{{COSMOS_DATABASE_NAME_BICEP}}'

// Name of the Azure AI Search index.
param searchIndexName = '{{SEARCH_INDEX_NAME_BICEP}}'

// LLM model deployments (driven by spec.yaml.model_deployments).
// Each entry: { deploymentName, model, version, capacity, sku? }.
param modelDeployments = {{MODEL_DEPLOYMENTS_BICEP}}

// Azure OpenAI embedding throughput (TPM in thousands).
param embeddingCapacity = 10

// Azure region for AI Foundry hub + model deployments.
// Override per-deployment with `azd env set AZURE_AI_LOCATION <region>` when
// your primary region lacks GlobalStandard quota.
// Common choices: 'eastus', 'swedencentral', 'australiaeast'.
param aiLocation = readEnvironmentVariable('AZURE_AI_LOCATION', '{{AI_REGION}}')

// Azure region for Azure AI Search service. Override per-deployment with
// `azd env set AZURE_SEARCH_LOCATION <region>` when the primary region is
// capacity-exhausted (e.g. 'eastus2' standard SKU outage). Falls back to
// `location` when no override is set.
param searchLocation = readEnvironmentVariable('AZURE_SEARCH_LOCATION', '{{AZURE_REGION}}')

// AI Search SKU. `basic` is the default; bump to `standard` (or higher)
// with `azd env set AZURE_SEARCH_SKU standard` when Basic is capacity-
// exhausted region-wide. Never use `free` — its 3-index cap conflicts
// with the demo's knowledge index.
param searchSku = readEnvironmentVariable('AZURE_SEARCH_SKU', 'basic')

// ── Branding (Step 2b: populated from spec.yaml by Copilot) ──

// Customer display name shown in the UI header.
param customerDisplayName = '{{CUSTOMER_NAME_BICEP}}'

// Agent name shown in the UI.
param agentName = '{{BRANDING_AGENT_NAME_BICEP}}'

// Primary brand color (hex).
param primaryColor = '{{BRANDING_PRIMARY_COLOR_BICEP}}'

// Accent brand color (hex).
param accentColor = '{{BRANDING_ACCENT_COLOR_BICEP}}'

// Font family for the UI.
param fontFamily = '{{BRANDING_FONT_FAMILY_BICEP}}'

// URL to the customer logo image.
// Uses a relative path served by the app container — no external URL dependency.
param logoUrl = '{{BRANDING_LOGO_URL_BICEP}}'

// Welcome message shown in the chat UI.
param welcomeMessage = '{{BRANDING_WELCOME_MESSAGE_BICEP}}'

// Use case title shown as agent subtitle.
param useCaseTitle = '{{BRANDING_USE_CASE_TITLE_BICEP}}'

// Starter questions shown as chips in the chat UI (JSON array).
// Leading space before [ prevents ARM from interpreting as expression.
param starterQuestions = '{{STARTER_QUESTIONS_BICEP}}'

// Demo persona shown in the user profile (drive from spec.yaml personas).
param personaName = '{{BRANDING_PERSONA_NAME_BICEP}}'
param personaRole = '{{BRANDING_PERSONA_ROLE_BICEP}}'
