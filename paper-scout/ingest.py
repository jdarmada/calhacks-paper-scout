# /// script
# requires-python = ">=3.10"
# dependencies = ["elasticsearch>=9", "requests", "python-dotenv"]
# ///
"""Step 1: pull recent arXiv papers and index them into Elasticsearch.

    uv run ingest.py                       # ~1,000 papers: 5 CS categories x last 6 months
    uv run ingest.py --per-month 10        # quick version
    uv run ingest.py --categories cs.RO q-bio.NC --months 12
"""

import argparse
import time
from datetime import date
import xml.etree.ElementTree as ET

import requests
from elasticsearch import helpers

from common import EMBEDDING_ID, INDEX, es

ATOM = {"a": "http://www.w3.org/2005/Atom", "arxiv": "http://arxiv.org/schemas/atom"}
DEFAULT_CATEGORIES = ["cs.AI", "cs.CL", "cs.LG", "cs.CV", "cs.RO"]


def month_ranges(months: int):
    """(start, end) arXiv date strings for the last N calendar months, newest first."""
    y, m = date.today().year, date.today().month
    for _ in range(months):
        ny, nm = (y + 1, 1) if m == 12 else (y, m + 1)
        yield f"{y}{m:02d}010000", f"{ny}{nm:02d}010000"
        y, m = (y - 1, 12) if m == 1 else (y, m - 1)


def fetch_arxiv(category: str, n: int, date_range=None):
    """Yield paper dicts from the arXiv API, newest first (optionally within a date range)."""
    query = f"cat:{category}"
    if date_range:
        query += f" AND submittedDate:[{date_range[0]} TO {date_range[1]}]"
    batch = 100
    for start in range(0, n, batch):
        resp = requests.get(
            "https://export.arxiv.org/api/query",
            params={
                "search_query": query,
                "sortBy": "submittedDate",
                "sortOrder": "descending",
                "start": start,
                "max_results": min(batch, n - start),
            },
            timeout=60,
        )
        resp.raise_for_status()
        entries = ET.fromstring(resp.content).findall("a:entry", ATOM)
        for e in entries:
            text = lambda tag: " ".join((e.findtext(tag, "", ATOM) or "").split())
            arxiv_id = text("a:id").rsplit("/abs/", 1)[-1]
            title, abstract = text("a:title"), text("a:summary")
            yield {
                "_id": arxiv_id,
                "arxiv_id": arxiv_id,
                "title": title,
                "abstract": abstract,
                # The field Jina embeds. semantic_text chunks + embeds it for us at index time.
                "content": f"{title}\n\n{abstract}",
                "authors": [a.findtext("a:name", "", ATOM) for a in e.findall("a:author", ATOM)],
                "primary_category": e.find("arxiv:primary_category", ATOM).get("term"),
                "categories": [c.get("term") for c in e.findall("a:category", ATOM)],
                "published": text("a:published"),
                "updated": text("a:updated"),
                "url": f"https://arxiv.org/abs/{arxiv_id}",
                "pdf_url": f"https://arxiv.org/pdf/{arxiv_id}",
            }
        if len(entries) < batch:
            return
        time.sleep(3)  # arXiv asks for 3s between calls


def create_index(client, recreate: bool):
    if client.indices.exists(index=INDEX):
        if not recreate:
            print(f"Index '{INDEX}' exists, adding to it (use --recreate to start fresh)")
            return
        client.indices.delete(index=INDEX)

    semantic = {"type": "semantic_text"}
    try:
        client.inference.get(inference_id=EMBEDDING_ID)
        semantic["inference_id"] = EMBEDDING_ID
        print(f"Embedding with {EMBEDDING_ID}")
    except Exception:
        print(f"{EMBEDDING_ID} not found here, using this cluster's default semantic_text model")

    client.indices.create(
        index=INDEX,
        mappings={
            "properties": {
                "arxiv_id": {"type": "keyword"},
                "title": {"type": "text", "fields": {"raw": {"type": "keyword"}}},
                "abstract": {"type": "text"},
                "content": semantic,
                "authors": {"type": "keyword"},
                "primary_category": {"type": "keyword"},
                "categories": {"type": "keyword"},
                "published": {"type": "date"},
                "updated": {"type": "date"},
                "url": {"type": "keyword", "index": False},
                "pdf_url": {"type": "keyword", "index": False},
            }
        },
    )
    print(f"Created index '{INDEX}'")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--categories", nargs="+", default=DEFAULT_CATEGORIES)
    p.add_argument("--months", type=int, default=6, help="sample papers from each of the last N months")
    p.add_argument("--per-month", type=int, default=40, help="papers per category per month")
    p.add_argument("--recreate", action="store_true")
    p.add_argument("--batch", type=int, default=50, help="docs per bulk request (embeddings run during bulk)")
    args = p.parse_args()

    client = es()
    create_index(client, args.recreate)

    total = 0
    for cat in args.categories:
        docs = []
        for rng in month_ranges(args.months):
            docs += list(fetch_arxiv(cat, args.per_month, rng))
            time.sleep(3)
        ok, errors = helpers.bulk(
            client,
            ({"_index": INDEX, **d} for d in docs),
            chunk_size=args.batch,  # embeddings are computed during bulk, so keep batches modest
            raise_on_error=False,
        )
        total += ok
        print(f"  {cat}: indexed {ok} papers" + (f" ({len(errors)} errors: {errors[0]})" if errors else ""))

    client.indices.refresh(index=INDEX)
    print(f"Done. {client.count(index=INDEX)['count']} papers in '{INDEX}' (+{total} this run)")


if __name__ == "__main__":
    main()
