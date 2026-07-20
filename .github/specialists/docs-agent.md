# Docs Agent — Generates generated/prototype/agents/knowledge/*.md

## Architecture guardrail

If behavior or file ownership is unclear, check `.github/architecture-reference.md` first.
Then verify against `generated/build-state/manifest.json` and generated artifacts.
Do not assume services, files, or runtime behavior that are not documented.

## Purpose
Generate realistic, domain-specific operational documents.
These are uploaded to Azure Blob Storage and indexed in Azure AI Search.
Quality matters — more content and higher specificity = better semantic search retrieval.

## Prerequisite
Read generated/build-state/manifest.json first.
Read spec.yaml documents[] section for titles and key_topics.
If manifest.json missing, stop.
Record the step clock: `py -3 accelerator/scripts/build-metrics.py step-start 05-docs-agent`.

**Check `manifest.dataGrounding.mode` first.** When it is `"real"` (the
customer chose to ground Foundry IQ in their own Azure Blob/SQL resources
instead of synthetic documents), **do not generate any documents**. Write
zero files to `generated/prototype/agents/knowledge/`, still write the
`.done` sentinel (the build graph must not stall), and print:
```
[Step 5/7] Skipped synthetic document generation (dataGrounding.mode = real).
  Knowledge base will be grounded in <N> real data source(s) instead — wired
  in during postprovision (hook-agent step 7 / 10).
```
When `mode` is `"synthetic"` or the `dataGrounding` key is absent (specs
written before this feature existed), proceed with the rest of this
specialist exactly as documented below.

## Inputs
Read: generated/build-state/manifest.json
Read: spec.yaml (documents[] section only)

## Outputs
Write: generated/prototype/agents/knowledge/{kebab-case-title}.md for each spec.yaml documents[] entry
Write: generated/build-state/05-docs-agent.done — via:

```
py -3 accelerator/generators/sentinels.py write \
  --sentinel generated/build-state/05-docs-agent.done \
  --manifest generated/build-state/manifest.json \
  --output generated/prototype/agents/knowledge
```

Never write the sentinel as a plain timestamp.

---

## Filename convention
Title → kebab-case + .md
Example: "Auto Loan Collections Best Practices Guide" → "auto-loan-collections-best-practices-guide.md"

---

## Content rules — all mandatory

### R1 — Word count: 600-900 words per document
These documents are chunked and indexed for semantic search.
Documents shorter than 600 words produce poor retrieval results during agent
demos (thin chunks, weak semantic anchors). Documents longer than 900 words
waste generation time and slow the Search index-and-embed step without
improving retrieval quality — the top-k chunks are already well-formed by
900 words for the demo's question surface.

Target the 700-800 word band; write to the density of the domain, not to
pad the word count.

### R2 — Real content, not placeholder text
Write as if this is an actual enterprise document used by the customer.
Include:
- Numbered sections with headings
- Specific KPIs, thresholds, and benchmarks (realistic for the industry)
- Process steps with conditions and decision points
- Regulatory references where applicable to the domain
- Tables or lists where appropriate

### R3 — Domain and industry alignment
All content must be realistic for:
- manifest.customer.industry
- manifest.customer.name
- manifest.branding.useCaseTitle

### R4 — Cover all key_topics
Every key_topic listed in spec.yaml documents[].key_topics must be addressed
as a section or subsection. Do not skip any.

### R5 — Document header format
```markdown
# {Document Title}

**Organization:** {manifest.customer.name}
**Industry:** {manifest.customer.industry}
**Last Updated:** {current date}
**Classification:** Internal Use

---

## Overview
{2-3 paragraph executive summary}

## {Section from key_topics}
{detailed content}

## {Section from key_topics}
{detailed content}

...
```

---

## After success
Print:
```
[Step 5/7] Operational documents generated.
  <N> documents written to generated/prototype/agents/knowledge/
  <list filenames>
```
