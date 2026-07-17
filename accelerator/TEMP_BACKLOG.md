# Temp backlog — scratch, not yet triaged into BACKLOG.md/KNOWN_ISSUES.md

## 1. Revisit: confirm telemetry actually lands in Log Analytics / App Insights

**Status:** Infra fixes applied and confirmed at the config level (see
[RESOLVED.md #38](RESOLVED.md)). Live data was NOT yet confirmed in either
pipe by the end of the 2026-07-17 session — last check showed 0 rows in
both `ContainerAppConsoleLogs` and Application Insights `requests`/`traces`,
roughly 30-40 minutes after the `appLogsConfiguration` fix was applied.

**Why this isn't necessarily a bug:** Microsoft's own Container Apps
logging docs state *"If there's an error when running a query, try again
in 10-15 minutes. There may be a delay for Log Analytics to start
receiving logs from the application."* A brand-new logging destination
that's never had data before may need longer than that in practice.

**Update 2026-07-17 (later same session):** found and fixed a real,
confirmed root cause for the console-log half of this — no
`Microsoft.Insights/diagnosticSettings` resource existed at all
(`appLogsConfiguration` alone is not sufficient). Added one in bicep,
scoped to the environment (container-app-level diagnostic settings
reject log categories outright). See RESOLVED.md #38. This should fix
`ContainerAppConsoleLogs`. It does NOT explain the separate Application
Insights `requests`/`traces` gap (SDK-level telemetry via
`configure_azure_monitor()`, an independent pipe) — that part is still
unconfirmed.

**Action for next session:**
1. Re-run: `az monitor log-analytics query --workspace <LAW customerId>
   --analytics-query "ContainerAppConsoleLogs | where TimeGenerated >
   ago(2h) | take 10"` — Alliant build workspace customerId:
   `1ea216ef-ba5b-4b5c-abea-f7a727804015`. Should now actually return
   rows given the diagnostic setting fix; if still empty, that's a new,
   genuine issue (the setting itself is confirmed to exist and is
   correctly configured).
2. Re-run: `az monitor app-insights query --app
   9672a03a-05fb-498a-9e58-cd057c014270 --analytics-query "union
   requests, traces | where timestamp > ago(2h) | take 10"`. If this
   specific one is still empty even after console logs start flowing,
   check `ContainerAppConsoleLogs` for the `"Azure Monitor telemetry
   configured: %s"` startup log line from `main.py` and any "WARNING:
   configure_azure_monitor() failed" print — this will finally reveal
   whether the SDK-side export is silently failing at runtime.
3. Next diagnostic step if still stuck: use the Live Metrics stream in
   the Azure Portal for the `alliant-alliant-appi` Application Insights
   resource, which bypasses ingestion lag entirely and shows real-time
   telemetry if the SDK-side export is working at all.
4. Remember: `%{http_code}` alone from curl is NOT sufficient to confirm
   the real app is responding — Azure Container Apps' own placeholder
   page also returns 200. This bit twice in a row after `azd provision`
   runs that touched the environment resource — always check response
   body content, and always follow an `azd provision` with `azd deploy
   app` as a standing precaution for this accelerator.

**Update 2026-07-17 (later still, same session):** implemented and
deployed the last documented app-level gap — Microsoft Agent Framework's
own GenAI spans (`invoke_agent`/`chat`/`execute_tool`) are gated behind a
separate `enable_instrumentation()` call / `ENABLE_INSTRUMENTATION` env
var, independent of `configure_azure_monitor()`. Added
`from agent_framework.observability import enable_instrumentation;
enable_instrumentation()` to `backend/main.py`. Tests pass (72/72),
deployed via `azd deploy app`, app confirmed healthy, generated fresh
agent traffic via `verify-prototype.py` (4/4 PASS, all 3 specialists
exercised).

**This did NOT fix the visibility gap** — re-checked immediately after
and found something much bigger: **the entire Log Analytics workspace has
zero rows in every table for the prior 24 hours**, including
`ContainerAppSystemLogs` (100% platform-generated, no app code involved)
and a blanket `search * | ago(1d)` across the whole workspace. This proves
the remaining gap is an infrastructure/ingestion-pipeline problem, NOT
anything left to fix in application code. All app-level config was
re-verified correct (connection string present in the running container,
App Insights component correctly linked to the workspace with
`ingestionMode: LogAnalytics`, both public network access flags Enabled,
no VNet integration blocking egress, the `cae-console-logs` diagnostic
setting from RESOLVED #38 still present and correctly scoped). One
related-but-distinct lead surfaced: `az policy state list` shows the
Foundry PROJECT and HUB diagnostic-settings policies as NonCompliant too
(`ProjectsAIFoundry_Diagnostics_Enable`, `CognitiveServices_Diagnostics_Enable`)
— Foundry's own diagnostics were never deployed either, though this is a
separate resource from the Log Analytics workspace itself.

**Action for next session (superseding the actions above, which are now
exhausted):**
1. Treat this as a genuine ingestion-pipeline outage/block, not latency —
   24h with literally zero rows anywhere is well past any normal
   first-time-destination delay (docs say 10-15 min).
2. Check tenant/subscription-level Azure Policy (not just RG-scoped) for
   anything touching `Microsoft.OperationalInsights` or
   `Microsoft.Insights/diagnosticSettings` resource types —
   `MCAPSGovDeployPolicies` is assigned at the Tenant Root Group (per
   RESOLVED #31/#32's Cosmos/Storage findings) and may have an unrelated
   rule affecting Log Analytics ingestion specifically.
3. Check the resource group's Activity Log for failed diagnostic-settings
   delivery events.
4. Try the Live Metrics stream in the Portal for `alliant-alliant-appi` —
   bypasses Log Analytics ingestion entirely, will prove/disprove whether
   the OTel SDK export is working in real time regardless of the
   workspace issue.
5. See full write-up in repo memory
   (`/memories/repo/ai-prototype-accelerator.md`, "enable_instrumentation()
   fix implemented + a DEEPER workspace-wide ingestion bug found").
