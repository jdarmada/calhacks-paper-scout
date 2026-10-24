# /// script
# requires-python = ">=3.10"
# dependencies = ["elasticsearch>=9", "requests", "python-dotenv"]
# ///
"""Step 2: create the Paper Scout agent in Elastic Agent Builder.

Creates 4 tools (index search, 2 ES|QL, 1 workflow), uploads the workflow,
and wires them into one agent. Safe to re-run: it updates what already exists.

    uv run setup_agent.py
"""

from pathlib import Path

from common import AGENT_ID, INDEX, KIBANA_URL, es, kibana

WORKFLOW_YAML = (Path(__file__).parent / "workflows" / "paper-digest.yaml").read_text()

TOOLS = [
    {
        "id": "papers_search",
        "type": "index_search",
        "description": (
            "Search recent arXiv research papers (title, abstract, authors, categories, dates). "
            "Use for any question about what research exists on a topic, finding specific papers, "
            "or comparing approaches. Results include the arXiv URL to cite."
        ),
        "configuration": {"pattern": INDEX},
    },
    {
        "id": "papers_topic_trend",
        "type": "esql",
        "description": (
            "Count papers per month that mention a keyword or phrase, broken down by arXiv category. "
            "Use for 'is X trending', 'how much research is there on X', or 'which fields work on X'. "
            "The index holds the same number of papers per category per month, so monthly counts are directly comparable."
        ),
        "configuration": {
            "query": (
                f"FROM {INDEX} | WHERE MATCH(abstract, ?topic, {{\"operator\": \"AND\"}}) "
                "| EVAL month = DATE_TRUNC(1 month, published) "
                "| STATS papers = COUNT(*) BY month, primary_category "
                "| SORT month DESC, papers DESC | LIMIT 60"
            ),
            "params": {"topic": {"type": "string", "description": "Keyword or phrase, e.g. 'diffusion'"}},
        },
    },
    {
        "id": "papers_top_authors",
        "type": "esql",
        "description": (
            "Find the most prolific authors on a topic. Use for 'who works on X', "
            "'who should I follow / reach out to about X'."
        ),
        "configuration": {
            "query": (
                f"FROM {INDEX} | WHERE MATCH(abstract, ?topic, {{\"operator\": \"AND\"}}) "
                "| STATS papers = COUNT(*), latest = MAX(published) BY authors "
                "| SORT papers DESC, latest DESC | LIMIT 15"
            ),
            "params": {"topic": {"type": "string", "description": "Keyword or phrase, e.g. 'robot grasping'"}},
        },
    },
    {
        "id": "papers_write_digest",
        "type": "workflow",
        "description": (
            "Generate and SAVE a research digest for a topic (runs the paper-digest workflow). "
            "Use only when the user asks for a digest, briefing, or report to be created or saved."
        ),
        "configuration": {"workflow_id": None},  # filled in after the workflow is uploaded
    },
]

AGENT = {
    "id": AGENT_ID,
    "name": "Paper Scout",
    "description": "Research assistant over thousands of recent arXiv papers.",
    "labels": ["calhacks", "research"],
    "avatar_color": "#48EFCF",
    "avatar_symbol": "PS",
    "configuration": {
        "instructions": (
            "You are Paper Scout, a research assistant for hackathon builders.\n"
            "- Always ground answers in the papers index using your tools. Never invent papers.\n"
            "- Only state facts that come from tool results (no affiliations or claims from memory).\n"
            "- Cite every paper you mention as [Title](arXiv URL).\n"
            "- Prefer recent work and say when evidence is thin.\n"
            "- When asked for project ideas, connect 2-3 specific papers to a concrete build that fits a weekend.\n"
            "- For trend questions use papers_topic_trend; for people use papers_top_authors.\n"
            "- Keep answers tight: bullets over paragraphs."
        ),
        "tools": [{"tool_ids": [t["id"] for t in TOOLS]}],
    },
}


def create_digest_index():
    """paper-digests: where the workflow saves briefings. A pipeline stamps @timestamp on write."""
    client = es()
    client.ingest.put_pipeline(
        id="paper-digests-timestamp",
        processors=[{"set": {"field": "@timestamp", "value": "{{_ingest.timestamp}}"}}],
    )
    if not client.indices.exists(index="paper-digests"):
        client.indices.create(
            index="paper-digests",
            settings={"index.default_pipeline": "paper-digests-timestamp"},
            mappings={"properties": {
                "@timestamp": {"type": "date"},
                "topic": {"type": "keyword"},
                "digest": {"type": "text"},
            }},
        )
    print("  index paper-digests: ready")


def upsert_workflow() -> str | None:
    existing = kibana("GET", "/api/workflows", params={"query": "paper-digest", "size": 100})
    if existing.ok:
        for wf in existing.json().get("results", []):
            if wf.get("name") == "paper-digest":
                r = kibana("PUT", f"/api/workflows/workflow/{wf['id']}", json={"yaml": WORKFLOW_YAML})
                print(f"  workflow paper-digest: updated ({r.status_code})")
                return wf["id"]
    # Newer Kibana: /api/workflows/workflow, older: /api/workflows
    for path in ("/api/workflows/workflow", "/api/workflows"):
        r = kibana("POST", path, json={"yaml": WORKFLOW_YAML})
        if r.ok:
            print("  workflow paper-digest: created")
            return r.json().get("id")
    print(f"  workflow paper-digest: FAILED {r.status_code} {r.text[:300]}")
    print("  (Workflows may need enabling: Stack Management > Advanced Settings > workflows:ui:enabled)")
    return None


def upsert(kind: str, body: dict):
    r = kibana("GET", f"/api/agent_builder/{kind}/{body['id']}")
    if r.ok:
        update = {k: v for k, v in body.items() if k not in ("id", "type", "name")}
        r = kibana("PUT", f"/api/agent_builder/{kind}/{body['id']}", json=update)
        verb = "updated"
    else:
        r = kibana("POST", f"/api/agent_builder/{kind}", json=body)
        verb = "created"
    print(f"  {kind[:-1]} {body['id']}: {verb if r.ok else 'FAILED'}" + ("" if r.ok else f" {r.status_code} {r.text[:300]}"))
    return r.ok


def main():
    print("Preparing digest index...")
    create_digest_index()

    print("Uploading workflow...")
    workflow_id = upsert_workflow()

    print("Creating tools...")
    for tool in TOOLS:
        if tool["type"] == "workflow":
            if not workflow_id:
                AGENT["configuration"]["tools"][0]["tool_ids"].remove(tool["id"])
                print(f"  tool {tool['id']}: skipped (no workflow)")
                continue
            tool["configuration"]["workflow_id"] = workflow_id
        upsert("tools", tool)

    print("Creating agent...")
    upsert("agents", AGENT)

    print("\nSmoke test (ES|QL tool):")
    r = kibana("POST", "/api/agent_builder/tools/_execute",
               json={"tool_id": "papers_top_authors", "tool_params": {"topic": "agents"}})
    print(f"  {r.status_code} {r.text[:400]}")
    print(f"\nDone. Chat with it in Kibana > Agents, or run: uv run app.py")
    print(f"MCP endpoint for Claude Code / Cursor: {KIBANA_URL}/api/agent_builder/mcp")


if __name__ == "__main__":
    main()
