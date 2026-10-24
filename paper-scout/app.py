# /// script
# requires-python = ">=3.10"
# dependencies = ["elasticsearch>=9", "requests", "python-dotenv", "fastapi", "uvicorn"]
# ///
"""Step 3: the Paper Scout web app.

    uv run app.py        # then open http://localhost:8000
"""

from pathlib import Path

import uvicorn
from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from common import AGENT_ID, INDEX, es, find_reranker, kibana

app = FastAPI()
client = es()
RERANKER = find_reranker(client)
SOURCE = ["title", "abstract", "authors", "published", "primary_category", "url"]


# ---- the three ways to search ---------------------------------------------

def keyword_query(q):
    """Classic BM25: great when you know the exact words."""
    return {"multi_match": {"query": q, "fields": ["title^2", "abstract", "authors"]}}


def semantic_query(q):
    """Jina embeddings via semantic_text: great when you know the idea, not the words."""
    return {"semantic": {"field": "content", "query": q}}


def hybrid_retriever(q):
    """Both, fused with Reciprocal Rank Fusion, then (optionally) reranked by a cross-encoder."""
    rrf = {
        "rrf": {
            "retrievers": [
                {"standard": {"query": keyword_query(q)}},
                {"standard": {"query": semantic_query(q)}},
            ],
            "rank_window_size": 50,
        }
    }
    if not RERANKER:
        return rrf
    return {
        "text_similarity_reranker": {
            "retriever": rrf,
            "field": "abstract",
            "inference_id": RERANKER,
            "inference_text": q,
            "rank_window_size": 20,
        }
    }


def hits(resp):
    return [
        {"id": h["_id"], "score": round(h["_score"] or 0, 3), **h["_source"]}
        for h in resp["hits"]["hits"]
    ]


@app.get("/api/search")
def search(q: str, size: int = 6):
    global RERANKER
    out = {
        "keyword": hits(client.search(index=INDEX, query=keyword_query(q), size=size, source=SOURCE)),
        "semantic": hits(client.search(index=INDEX, query=semantic_query(q), size=size, source=SOURCE)),
    }
    try:
        out["hybrid"] = hits(client.search(index=INDEX, retriever=hybrid_retriever(q), size=size, source=SOURCE))
        out["hybrid_label"] = f"Hybrid (RRF{' + ' + RERANKER + ' rerank' if RERANKER else ''})"
    except Exception as e:  # reranker unavailable etc. -> plain RRF from now on
        print("rerank failed, using plain RRF:", e)
        RERANKER = None
        rrf = hybrid_retriever(q)
        rrf = rrf.get("text_similarity_reranker", {}).get("retriever", rrf)
        out["hybrid"] = hits(client.search(index=INDEX, retriever=rrf, size=size, source=SOURCE))
        out["hybrid_label"] = "Hybrid (RRF)"
    return out


@app.get("/api/related/{paper_id}")
def related(paper_id: str, size: int = 5):
    """'More like this' with zero ML code: embed the paper's abstract and search with it."""
    paper = client.get(index=INDEX, id=paper_id)["_source"]
    resp = client.search(
        index=INDEX,
        size=size,
        source=SOURCE,
        query={"bool": {"must": semantic_query(paper["abstract"]), "must_not": {"ids": {"values": [paper_id]}}}},
    )
    return {"paper": paper["title"], "related": hits(resp)}


@app.get("/api/trend")
def trend(q: str):
    """Analytics on the same index with ES|QL: monthly volume of papers mentioning q."""
    resp = client.esql.query(
        query=(
            f"FROM {INDEX} | WHERE MATCH(abstract, ?q, {{\"operator\": \"AND\"}}) "
            "| EVAL month = DATE_TRUNC(1 month, published) "
            "| STATS papers = COUNT(*) BY month | SORT month ASC"
        ),
        params=[{"q": q}],
    )
    cols = [c["name"] for c in resp["columns"]]
    rows = [dict(zip(cols, r)) for r in resp["values"]]
    return {"rows": [{"month": r["month"][:7], "papers": r["papers"]} for r in rows]}


@app.get("/api/stats")
def stats():
    resp = client.search(
        index=INDEX, size=0, aggs={"cats": {"terms": {"field": "primary_category", "size": 8}}}
    )
    return {
        "count": resp["hits"]["total"]["value"],
        "categories": [b["key"] for b in resp["aggregations"]["cats"]["buckets"]],
        "reranker": RERANKER,
    }


# ---- the agent ---------------------------------------------------------------

class Chat(BaseModel):
    message: str
    conversation_id: str | None = None


@app.post("/api/chat")
def chat(body: Chat):
    payload = {"input": body.message, "agent_id": AGENT_ID}
    if body.conversation_id:
        payload["conversation_id"] = body.conversation_id
    try:
        r = kibana("POST", "/api/agent_builder/converse", json=payload)
    except Exception as e:
        return {"error": str(e)}
    if not r.ok:
        return {"error": f"{r.status_code}: {r.text[:500]}"}
    data = r.json()
    tools = [
        {"tool": s.get("tool_id"), "params": s.get("params")}
        for s in data.get("steps", [])
        if s.get("type") == "tool_call"
    ]
    return {
        "conversation_id": data.get("conversation_id"),
        "message": data.get("response", {}).get("message", ""),
        "tools": tools,
    }


@app.get("/", response_class=HTMLResponse)
def index():
    return (Path(__file__).parent / "static" / "index.html").read_text()


if __name__ == "__main__":
    print(f"Paper Scout on http://localhost:8000  (index={INDEX}, reranker={RERANKER})")
    uvicorn.run(app, host="0.0.0.0", port=8000)
