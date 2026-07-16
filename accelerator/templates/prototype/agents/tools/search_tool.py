"""
ai-prototype-accelerator — AI Search Tool
Static scaffold file — do NOT modify when processing spec.yaml.

Calls a Foundry IQ knowledge base's `retrieve` action (agentic retrieval):
the knowledge base decomposes the query, runs it against the blob knowledge
source (chunked + optionally vectorized by the auto-generated indexer
pipeline created in postprovision), semantically reranks, and returns
grounding data. This replaces a hand-rolled single-shot semantic query
against a manually-built index.
"""
import json
import logging
import os

import httpx
from azure.identity import DefaultAzureCredential

from agents.tools import activity

logger = logging.getLogger(__name__)

_SEARCH_API_VERSION = "2026-05-01-preview"

# DefaultAzureCredential caches tokens internally; a module-level singleton
# avoids re-creating the credential (and its token cache) on every call.
_CREDENTIAL: DefaultAzureCredential | None = None


def _get_credential() -> DefaultAzureCredential:
    global _CREDENTIAL
    if _CREDENTIAL is None:
        _CREDENTIAL = DefaultAzureCredential(
            managed_identity_client_id=os.environ["AZURE_CLIENT_ID"]
        )
    return _CREDENTIAL


def _knowledge_base_name() -> str:
    # Derived, not a separate env var — matches the "{index}-kb" name the
    # postprovision hook creates the knowledge base under.
    return f"{os.environ['AZURE_SEARCH_INDEX']}-kb"


def search_knowledge_base(query: str) -> str:
    """
    Agentic retrieval against the Foundry IQ knowledge base.
    Returns a formatted context string for the agent.
    """
    preview = query if len(query) <= 60 else query[:57] + "..."
    activity.notify(f'Searching knowledge base: "{preview}"')

    endpoint = os.environ["AZURE_SEARCH_ENDPOINT"].rstrip("/")
    kb_name = _knowledge_base_name()
    url = f"{endpoint}/knowledgebases/{kb_name}/retrieve"

    body = {
        "messages": [
            {
                "role": "assistant",
                "content": [
                    {
                        "type": "text",
                        "text": (
                            "You retrieve grounding data for a business chat agent. "
                            "Sources are JSON objects with a ref_id, title, and content."
                        ),
                    }
                ],
            },
            {
                "role": "user",
                "content": [{"type": "text", "text": query}],
            },
        ]
    }

    try:
        token = _get_credential().get_token("https://search.azure.com/.default").token
        response = httpx.post(
            url,
            params={"api-version": _SEARCH_API_VERSION},
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
            json=body,
            timeout=30.0,
        )
        response.raise_for_status()
        payload = response.json()

        raw_text = payload["response"][0]["content"][0]["text"]
        chunks = json.loads(raw_text) if raw_text else []

        if not chunks:
            return "No relevant information found in the knowledge base."

        formatted_results = []
        for i, chunk in enumerate(chunks, 1):
            title = chunk.get("title", "Untitled")
            content = chunk.get("content", "")
            formatted_results.append(f"[{i}] **{title}**\n{content}")

        context = "\n\n".join(formatted_results)
        _log_search(query, len(formatted_results))
        return context

    except Exception as e:
        logger.error("search_knowledge_base failed: %s", e)
        return "Search unavailable right now."


def _log_search(query: str, result_count: int) -> None:
    try:
        logger.info(
            "search_tool | query_preview=%s | results=%d",
            query[:100],
            result_count
        )
    except Exception:
        pass

