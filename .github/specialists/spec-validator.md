# Spec Validator — Validates spec.yaml and writes generated/build-state/manifest.json

## Architecture guardrail

If behavior or file ownership is unclear, check `.github/architecture-reference.md` first.
Then verify against `generated/build-state/manifest.json` and generated artifacts.
Do not assume services, files, or runtime behavior that are not documented.

## Purpose
Gate step. Nothing else proceeds until this completes successfully.
Read spec.yaml, validate it, derive all resource names, write manifest.json.

## Input
Read: spec.yaml (root of repo)

## Outputs
Write: generated/build-state/manifest.json
Write: generated/build-state/01-spec-validator.done (hash-aware JSON sentinel produced by `sentinels.write()` inside `spec-validator.py`)

---

## How to run this step

Run the deterministic validator script — no LLM authoring required:

```
py -3 accelerator/generators/spec-validator.py
```

The script enforces every rule listed below, derives all resource names, computes the
spec checksum, generates the 4-char suffix (seeded from the checksum so it is stable
across reruns of the same spec), and writes both `manifest.json` and the
`01-spec-validator.done` sentinel.

On success it prints the summary block shown at the bottom of this document.
On failure it lists every validation error and exits non-zero. Do not hand-author
`manifest.json` — the script is the source of truth for the manifest schema that
`fill-templates.py` and downstream specialists consume.

---

## Validation Rules (enforced by the script) — hard stop and report ALL errors before proceeding

1. Required top-level keys present: `customer`, `branding`, `use_case`, `deployment`,
   `agents`, `tables`, `documents`, `starter_questions`, `demo_persona`,
   `foundry_iq`, `model_deployments`
2. `customer.slug` is lowercase, hyphens only (no spaces, no underscores)
3. `deployment.azure_region` is a valid Azure region string (e.g. "westus2", "eastus")
4. `agents[].routing_keywords` must not overlap across any two agents — list conflicts if found
5. Every `agents[].model` must reference a value in `model_deployments[].deployment_name`
6. At least one agent defined, at least one table defined, at least one document defined
7. `tables[].partition_key` starts with `/` (Cosmos partition key format)

If any rule fails: list every failure, then stop. Do not write manifest.json.

---

## manifest.json — write EXACTLY this structure

Derive all resource names from spec.yaml. Never hard-code values.

```json
{
  "specChecksum": "<first 8 chars of SHA-256 of spec.yaml content>",
  "buildTimestamp": "<ISO 8601 UTC>",
  "customer": {
    "slug": "<spec.yaml customer.slug>",
    "name": "<spec.yaml customer.name>",
    "industry": "<spec.yaml customer.industry>"
  },
  "deployment": {
    "environmentName": "<spec.yaml deployment.environment_name>",
    "resourceGroup": "<spec.yaml deployment.resource_group>",
    "location": "<spec.yaml deployment.azure_region>",
    "suffix": "<generate: 4-char random lowercase alphanumeric>"
  },
  "resources": {
    "cosmosAccountName": "<slug>-cosmos-<suffix>",
    "cosmosDatabaseName": "<spec.yaml deployment.database_name>",
    "searchIndexName": "<spec.yaml foundry_iq.index_name>",
    "storageAccount": "<slug with hyphens removed, max 20 chars>st<suffix>",
    "miName": "<slug>-<environmentName>-id",
    "acrName": "<slug with hyphens removed, max 20 chars>acr<suffix>"
  },
  "branding": {
    "agentName": "<spec.yaml branding.agent_name>",
    "primaryColor": "<spec.yaml branding.primary_color>",
    "accentColor": "<spec.yaml branding.accent_color>",
    "fontFamily": "<spec.yaml branding.font_family>",
    "logoUrl": "<spec.yaml branding.logo_url>",
    "welcomeMessage": "<spec.yaml branding.welcome_message>",
    "useCaseTitle": "<spec.yaml use_case.title>",
    "starterQuestions": ["<spec.yaml starter_questions — up to 6>"],
    "personaName": "<spec.yaml demo_persona.name>",
    "personaRole": "<spec.yaml demo_persona.role>"
  },
  "tables": [
    {
      "name": "<spec.yaml tables[].name>",
      "partitionKey": "<spec.yaml tables[].partition_key>",
      "seedCount": "<spec.yaml tables[].seed_count>",
      "seedScenario": "<spec.yaml tables[].seed_scenario>"
    }
  ],
  "agents": [
    {
      "name": "<spec.yaml agents[].name>",
      "role": "<spec.yaml agents[].role>",
      "model": "<spec.yaml agents[].model>",
      "routingKeywords": ["<spec.yaml agents[].routing_keywords>"],
      "tools": ["<spec.yaml agents[].tools>"],
      "skills": ["<spec.yaml agents[].skills>"]
    }
  ],
  "modelDeployments": [
    {
      "deploymentName": "<spec.yaml model_deployments[].deployment_name>",
      "model": "<spec.yaml model_deployments[].model>",
      "capacity": "<spec.yaml model_deployments[].capacity>"
    }
  ],
  "documents": ["<spec.yaml documents[].title>"],
  "foundryIq": {
    "indexName": "<spec.yaml foundry_iq.index_name>",
    "chunkSize": "<spec.yaml foundry_iq.chunking.chunk_size>",
    "overlap": "<spec.yaml foundry_iq.chunking.overlap>"
  },
  "mockApiEnabled": "<spec.yaml mock_api.enabled | false>"
}
```

---

## Suffix generation
Generate a 4-character random alphanumeric string (lowercase only) for `deployment.suffix`.
This suffix is appended to all resource names for uniqueness.
Example: "hud8", "fmc4", "ce7x"

## After success
Print a summary:
```
[Step 1/7] Spec validation passed.
  Customer:  <name>
  Agents:    <count> (<list names>)
  Tables:    <count> (<list names>)
  Models:    <count> (<list deployment_names>)
  Docs:      <count>
  Suffix:    <suffix>
  Manifest written to generated/build-state/manifest.json
```
