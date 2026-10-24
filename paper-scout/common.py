"""Shared config + clients for Paper Scout. Reads settings from .env."""

import os
from pathlib import Path

import requests
from dotenv import load_dotenv
from elasticsearch import Elasticsearch

load_dotenv(Path(__file__).parent / ".env")

ES_URL = os.environ.get("ELASTICSEARCH_URL", "http://localhost:9200")
# On Elastic Cloud the Kibana URL is the Elasticsearch URL with ".es." swapped for ".kb.".
KIBANA_URL = (os.environ.get("KIBANA_URL") or ES_URL.replace(".es.", ".kb.", 1)).rstrip("/")
if KIBANA_URL == ES_URL.rstrip("/"):
    KIBANA_URL = ""  # not an Elastic Cloud URL, so we can't guess
API_KEY = os.environ.get("ELASTIC_API_KEY", "")
INDEX = os.environ.get("PAPERS_INDEX", "papers")
# Jina v5 is the default semantic_text model on Serverless; we name it explicitly so it's visible.
EMBEDDING_ID = os.environ.get("EMBEDDING_INFERENCE_ID", ".jina-embeddings-v5-text-small")
AGENT_ID = os.environ.get("AGENT_ID", "paper-scout")


def es() -> Elasticsearch:
    if API_KEY:
        return Elasticsearch(ES_URL, api_key=API_KEY, request_timeout=120)
    return Elasticsearch(ES_URL, request_timeout=120)


def kibana(method: str, path: str, **kwargs) -> requests.Response:
    """Call a Kibana API (Agent Builder, Workflows) with the same API key."""
    if not KIBANA_URL:
        raise RuntimeError("KIBANA_URL is not set in .env")
    headers = {
        "Authorization": f"ApiKey {API_KEY}",
        "kbn-xsrf": "true",
        "Content-Type": "application/json",
        "x-elastic-internal-origin": "Kibana",
    }
    return requests.request(method, f"{KIBANA_URL}{path}", headers=headers, timeout=300, **kwargs)


def find_reranker(client: Elasticsearch) -> str | None:
    """Pick a rerank endpoint if the project has one (prefers Jina)."""
    if os.environ.get("RERANK_INFERENCE_ID"):
        return os.environ["RERANK_INFERENCE_ID"]
    try:
        endpoints = client.inference.get(task_type="rerank").get("endpoints", [])
    except Exception:
        return None
    ids = [e["inference_id"] for e in endpoints]
    # newest Jina text reranker first (m0 is the multimodal one)
    jina = sorted((i for i in ids if "jina" in i and "m0" not in i), reverse=True)
    return (jina or ids or [None])[0]
