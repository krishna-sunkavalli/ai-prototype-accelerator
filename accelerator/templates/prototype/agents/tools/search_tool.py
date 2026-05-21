"""
ai-prototype-accelerator — AI Search Tool
Static scaffold file — do NOT modify when processing spec.yaml.

Hybrid search: semantic + keyword combined.
Always uses semantic ranker. Returns top 5 results.
"""
import os
import logging

from azure.search.documents import SearchClient
from azure.search.documents.models import (
    VectorizedQuery,
    QueryType,
    QueryAnswerType,
    QueryCaptionType,
)
from azure.identity import DefaultAzureCredential

logger = logging.getLogger(__name__)


def search_knowledge_base(query: str) -> str:
    """
    Hybrid semantic + keyword search against Azure AI Search index.
    Returns formatted context string for agent.
    """
    credential = DefaultAzureCredential(
        managed_identity_client_id=os.environ["AZURE_CLIENT_ID"]
    )
    client = SearchClient(
        endpoint=os.environ["AZURE_SEARCH_ENDPOINT"],
        index_name=os.environ["AZURE_SEARCH_INDEX"],
        credential=credential
    )

    try:
        results = client.search(
            search_text=query,
            query_type=QueryType.SEMANTIC,
            semantic_configuration_name="default",
            query_caption=QueryCaptionType.EXTRACTIVE,
            query_answer=QueryAnswerType.EXTRACTIVE,
            top=5,
            select=["content", "title", "filename", "category"]
        )

        formatted_results = []
        for i, result in enumerate(results, 1):
            content = result.get("content", "")
            title = result.get("title", "Untitled")
            source = result.get("filename") or result.get("category") or "Unknown"
            score = result.get("@search.score", 0.0)

            formatted_results.append(
                f"[{i}] **{title}** (source: {source}, score: {score:.3f})\n{content}"
            )

        if not formatted_results:
            return "No relevant information found in the knowledge base."

        context = "\n\n".join(formatted_results)
        _log_search(query, len(formatted_results))
        return context

    except Exception as e:
        logger.error("search_knowledge_base failed: %s", e)
        return f"Search unavailable: {str(e)}"


def _log_search(query: str, result_count: int) -> None:
    try:
        logger.info(
            "search_tool | query_preview=%s | results=%d",
            query[:100],
            result_count
        )
    except Exception:
        pass
