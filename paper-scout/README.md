# Paper Scout

An agent that does the research. Thousands of fresh arXiv papers in Elasticsearch, embedded with Jina, searchable three ways, and an Agent Builder agent that reads them, spots trends, and writes digests through a Workflow.

Built for the **Find the Signal with Elasticsearch** challenge at Cal Hacks. Get started: https://ela.st/calhacks

```
arXiv API ──► ingest.py ──► Elasticsearch Serverless (index: papers)
                                │  semantic_text ← Jina v5 embeddings (auto)
                                │
        ┌───────────────────────┼──────────────────────────────┐
        ▼                       ▼                              ▼
   app.py search          Agent Builder agent             Workflow: paper-digest
   keyword | semantic     tools: index_search,            search ► ai.agent ► index
   | hybrid + rerank      2× ES|QL, workflow              (manual, daily, or as a tool)
                                │
                                └──► MCP endpoint ► Claude Code / Cursor / your app
```

## Run it (about 5 minutes)

1. Create a free **Elasticsearch Serverless** project at https://ela.st/calhacks. From **Connection details**, copy the Elasticsearch URL and create an API key. You don't need the Kibana URL; the scripts derive it (same URL with `.kb.` instead of `.es.`).
2. Configure and run:

```bash
cp .env.example .env          # paste your Elasticsearch URL + API key
uv run ingest.py              # ~1,000 papers from the last 6 months, a few minutes
uv run setup_agent.py         # tools + workflow + agent
uv run app.py                 # http://localhost:8000
```

No `uv`? `pip install elasticsearch requests python-dotenv fastapi uvicorn`, then use `python` instead of `uv run`.

## What's in here

| File | What it shows |
| --- | --- |
| `ingest.py` | One mapping with a `semantic_text` field. Elastic chunks and embeds it with Jina at index time, so you don't run an embedding pipeline. |
| `app.py` | The same query three ways: BM25, semantic (Jina), and hybrid (RRF plus a reranker). Also "find related papers" and an ES\|QL trend chart. |
| `setup_agent.py` | Agent Builder over plain REST: an `index_search` tool, two parameterized ES\|QL tools, a `workflow` tool, and one agent. |
| `workflows/paper-digest.yaml` | An Elastic Workflow that searches, asks an agent to write a briefing, and saves it. It runs daily or when the agent calls it. |

## Use it from your coding agent

**MCP:** Agent Builder exposes every tool over MCP at `$KIBANA_URL/api/agent_builder/mcp`. To add it to Claude Code:

```bash
claude mcp add --transport http paper-scout "$KIBANA_URL/api/agent_builder/mcp" --header "Authorization: ApiKey $ELASTIC_API_KEY"
```

**Elastic Agent Skills:** these teach your coding agent current Elastic APIs, so it stops writing 2021-era Elasticsearch code.

```bash
claude plugin marketplace add https://github.com/elastic/agent-skills
claude plugin install elastic-elasticsearch@elastic-agent-skills
claude plugin install elastic-kibana@elastic-agent-skills
```

## Remix ideas

- **Swap the data.** Put anything messy in `ingest.py`: course reviews, Reddit threads, GitHub issues, city council minutes, clinical trials, or legal filings. The rest still works.
- **Full text, not just abstracts.** Pull PDFs and index the text into `semantic_text`. Long documents are chunked for you.
- **"Explain it to me" mode.** Add an ES\|QL tool that filters by category or date, and ask the agent to write a briefing at a freshman reading level.
- **Alerts on research.** Schedule a workflow that runs a saved semantic query every morning and posts new matches to Slack or Discord.
- **Citation graph.** Add references from Semantic Scholar and let the agent walk "papers that cite this".
