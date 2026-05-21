"""
ai-prototype-accelerator — Application Entry Point
Static scaffold file — do NOT modify when processing spec.yaml.
"""
import os
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

    yield

    # ── Shutdown ──────────────────────────────────────────────────────────────
    logger.info("Shutting down...")
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
