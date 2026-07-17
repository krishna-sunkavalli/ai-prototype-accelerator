"""
ai-prototype-accelerator — Application Entry Point
Static scaffold file — do NOT modify when processing spec.yaml.
"""
import os

# Configure Azure Monitor OpenTelemetry BEFORE importing FastAPI (and before
# any other instrumented library) so the distro's auto-instrumentation can
# correctly patch it -- import order matters for this to work. No-op when
# APPLICATIONINSIGHTS_CONNECTION_STRING isn't set (e.g. local dev without
# Application Insights provisioned). connection_string is read from that env
# var automatically; main.bicep already wires it into the Container App.
#
# Container Apps can scale to zero (minReplicas: 0 on this template) and can
# terminate a replica shortly after its last request. The OTel SDK's default
# BatchSpanProcessor only exports every few seconds, so a replica killed
# between requests can lose whatever hasn't been flushed yet. Two defenses:
# (1) shrink the batch schedule delay so spans/logs export almost
# immediately instead of waiting out the default window, (2) force-flush the
# global providers in the shutdown lifespan below so anything still
# buffered goes out before the process exits.
_TELEMETRY_CONFIGURED = False
if os.environ.get("APPLICATIONINSIGHTS_CONNECTION_STRING"):
    os.environ.setdefault("OTEL_BSP_SCHEDULE_DELAY", "1000")  # ms, default is 5000
    os.environ.setdefault("OTEL_BLRP_SCHEDULE_DELAY", "1000")  # same, for the log record processor
    try:
        from azure.monitor.opentelemetry import configure_azure_monitor
        configure_azure_monitor()
        # Microsoft Agent Framework's own GenAI spans (invoke_agent/chat/
        # execute_tool -- the actual agent/model/tool-call telemetry, not
        # just generic HTTP request spans) are gated behind a SEPARATE
        # instrumentation switch. configure_azure_monitor() only sets up
        # the OTel export pipeline; it does NOT turn on agent_framework's
        # instrumentation code paths. Without this call, orchestrator.py's
        # FoundryAgent invocations never emit any telemetry at all, no
        # matter how correctly the exporter is configured. See
        # https://learn.microsoft.com/agent-framework/agents/observability
        # ("Third party setup" pattern) and RESOLVED.md.
        from agent_framework.observability import enable_instrumentation
        enable_instrumentation()
        _TELEMETRY_CONFIGURED = True
    except Exception as exc:  # pragma: no cover - defensive; must never crash startup
        # print(), not logger -- logging isn't configured yet at this point in
        # module load, and this failure must be visible even if logging setup
        # itself is what's broken.
        print(f"WARNING: configure_azure_monitor() failed, continuing without "
              f"tracing: {exc!r}")

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from azure.identity import DefaultAzureCredential

from backend.api.routes import router
from backend.api.mock_api_router import mock_router
from agents import orchestrator

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)
logger.info("Azure Monitor telemetry configured: %s", _TELEMETRY_CONFIGURED)


def _flush_telemetry() -> None:
    """Force-export any buffered spans/log records right now. Container Apps
    can terminate a scaled-to-zero replica without much warning, so relying
    solely on the batch processors' periodic timer risks losing whatever
    hasn't been exported yet. Safe to call even if telemetry isn't
    configured or a provider doesn't support force_flush()."""
    if not _TELEMETRY_CONFIGURED:
        return
    try:
        from opentelemetry.trace import get_tracer_provider
        get_tracer_provider().force_flush()
    except Exception as exc:  # pragma: no cover - best-effort, never fatal
        logger.warning("Tracer provider flush failed: %r", exc)
    try:
        from opentelemetry._logs import get_logger_provider
        get_logger_provider().force_flush()
    except Exception as exc:  # pragma: no cover - best-effort, never fatal
        logger.warning("Logger provider flush failed: %r", exc)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # ── Startup ──────────────────────────────────────────────────────────────
    logger.info("Starting ai-prototype-accelerator...")

    # Print env var names present (not values) so SE can confirm config
    present = [k for k in os.environ if k.startswith(
        ("AZURE_", "CUSTOMER_", "AGENT_", "PRIMARY_", "ACCENT_",
         "FONT_", "LOGO_", "WELCOME_", "PROTOTYPE_", "APPLICATIONINSIGHTS_",
         "MOCK_API_")
    )]
    logger.info("Environment variables present: %s", sorted(present))

    # Build Azure credential (singleton — reused everywhere)
    # AZURE_CLIENT_ID is required in production (user-assigned MI).
    # For local dev it may be absent; pass it only when present to avoid SDK error.
    client_id = os.environ.get("AZURE_CLIENT_ID", "")
    if client_id:
        credential = DefaultAzureCredential(managed_identity_client_id=client_id)
    else:
        logger.warning("AZURE_CLIENT_ID not set — falling back to ambient credential for local dev")
        credential = DefaultAzureCredential(managed_identity_client_id=None)

    # Store on app state so routes can access
    app.state.credential = credential

    # Warm-up: build one MAF FoundryAgent per pre-registered Foundry agent
    logger.info("Warming up agents...")
    orchestrator.initialize(credential)
    await orchestrator.warm_up()
    logger.info("Agent warm-up complete.")

    # Periodically force-flush telemetry rather than only on clean shutdown --
    # a scale-to-zero replica can be torn down without running the shutdown
    # block below at all.
    flush_task = None
    if _TELEMETRY_CONFIGURED:
        async def _periodic_flush() -> None:
            while True:
                await asyncio.sleep(10)
                _flush_telemetry()
        flush_task = asyncio.create_task(_periodic_flush())

    yield

    # ── Shutdown ──────────────────────────────────────────────────────────────
    logger.info("Shutting down...")
    if flush_task is not None:
        flush_task.cancel()
    _flush_telemetry()
    await orchestrator.close_all_connections()
    logger.info("Shutdown complete.")


app = FastAPI(
    title="ai-prototype-accelerator",
    version="1.0.0",
    lifespan=lifespan
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,  # incompatible with allow_origins=["*"]; SPA is same-origin
    allow_methods=["*"],
    allow_headers=["*"],
)

# API routes (must be registered BEFORE static mount)
app.include_router(router)
app.include_router(mock_router)

# Frontend mounts. `frontend/public` holds the index.html + assets that the
# browser loads directly; `frontend/src` holds source-level JS/CSS (kept
# separate so a build step can later be added without restructuring).
app.mount("/static", StaticFiles(directory="frontend/src"), name="frontend-src")
app.mount("/", StaticFiles(directory="frontend/public", html=True), name="frontend")
