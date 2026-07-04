"""
rag_pipeline.py
===============
Research-driven RAG pipeline for AI System Architect.

Flow
----
1. Search  — Semantic Scholar, CrossRef, arXiv, CORE, Tavily, GitHub (concurrent)
2. Fetch   — download full text / PDF / README with BeautifulSoup + PyMuPDF
3. Embed   — chunk text → SentenceTransformers → FAISS index
4. Retrieve — top-k chunks most relevant to the query
5. Fallback — if < MIN_RELIABLE_SOURCES found, skip RAG and return empty context

All retrieved source links are always returned so the frontend can display them
in the Learning References section regardless of whether RAG was used.
"""

from __future__ import annotations

import asyncio
import io
import logging
import re
import time
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

import httpx
import numpy as np

logger = logging.getLogger(__name__)

# ── tunables ────────────────────────────────────────────────────────────────
SEARCH_TIMEOUT        = 10      # seconds per source
FETCH_TIMEOUT         = 15      # seconds per document
MAX_DOCS_PER_SOURCE   = 5
MAX_CHARS_PER_DOC     = 12_000  # truncate long docs before chunking
CHUNK_SIZE            = 400     # chars per chunk
CHUNK_OVERLAP         = 80
TOP_K_CHUNKS          = 12      # chunks fed to LLM
MIN_RELIABLE_SOURCES  = 2       # fall back to prompt-only below this
EMBED_MODEL           = "all-MiniLM-L6-v2"
MAX_CONTEXT_CHARS     = 6_000   # total RAG context injected into prompt


# ── data classes ────────────────────────────────────────────────────────────

@dataclass
class SourceDoc:
    title:  str
    url:    str
    source: str          # "arxiv" | "semantic_scholar" | "crossref" | "core" | "tavily" | "github"
    text:   str  = ""    # full extracted text (filled during fetch)
    pdf_url: Optional[str] = None


@dataclass
class RAGResult:
    context:        str               # assembled top-k text chunks
    sources:        List[SourceDoc]   # all discovered sources (for UI)
    used_rag:       bool              # True if RAG context was injected
    chunk_count:    int = 0
    source_count:   int = 0


# ── lazy imports (heavy deps only loaded when actually used) ─────────────────

def _get_sentence_transformer():
    from sentence_transformers import SentenceTransformer
    return SentenceTransformer(EMBED_MODEL)

def _get_faiss():
    import faiss
    return faiss


# ════════════════════════════════════════════════════════════════════════════
# 1. SEARCHERS
# ════════════════════════════════════════════════════════════════════════════

async def _search_arxiv(query: str, client: httpx.AsyncClient) -> List[SourceDoc]:
    try:
        url = "https://export.arxiv.org/api/query"
        params = {"search_query": f"all:{query}", "max_results": MAX_DOCS_PER_SOURCE, "sortBy": "relevance"}
        r = await client.get(url, params=params, timeout=SEARCH_TIMEOUT)
        r.raise_for_status()

        from bs4 import BeautifulSoup
        soup = BeautifulSoup(r.text, "xml")
        docs = []
        for entry in soup.find_all("entry")[:MAX_DOCS_PER_SOURCE]:
            title   = entry.find("title").get_text(strip=True) if entry.find("title") else ""
            link    = entry.find("id").get_text(strip=True)    if entry.find("id")    else ""
            pdf_url = link.replace("/abs/", "/pdf/") + ".pdf"  if "/abs/" in link else None
            summary = entry.find("summary").get_text(strip=True) if entry.find("summary") else ""
            if title and link:
                d = SourceDoc(title=title, url=link, source="arxiv", pdf_url=pdf_url)
                d.text = summary[:MAX_CHARS_PER_DOC]
                docs.append(d)
        logger.info("arXiv: %d results", len(docs))
        return docs
    except Exception as e:
        logger.warning("arXiv search failed: %s", e)
        return []


async def _search_semantic_scholar(query: str, client: httpx.AsyncClient) -> List[SourceDoc]:
    try:
        url = "https://api.semanticscholar.org/graph/v1/paper/search"
        params = {
            "query": query,
            "limit": MAX_DOCS_PER_SOURCE,
            "fields": "title,abstract,url,openAccessPdf",
        }
        r = await client.get(url, params=params, timeout=SEARCH_TIMEOUT)
        r.raise_for_status()
        data = r.json().get("data", [])
        docs = []
        for p in data[:MAX_DOCS_PER_SOURCE]:
            title   = p.get("title", "")
            link    = p.get("url", "") or f"https://www.semanticscholar.org/paper/{p.get('paperId','')}"
            abstract= p.get("abstract") or ""
            pdf_url = (p.get("openAccessPdf") or {}).get("url")
            if title:
                d = SourceDoc(title=title, url=link, source="semantic_scholar", pdf_url=pdf_url)
                d.text = abstract[:MAX_CHARS_PER_DOC]
                docs.append(d)
        logger.info("Semantic Scholar: %d results", len(docs))
        return docs
    except Exception as e:
        logger.warning("Semantic Scholar search failed: %s", e)
        return []


async def _search_crossref(query: str, client: httpx.AsyncClient) -> List[SourceDoc]:
    try:
        url = "https://api.crossref.org/works"
        params = {"query": query, "rows": MAX_DOCS_PER_SOURCE, "select": "title,URL,abstract"}
        r = await client.get(url, params=params, timeout=SEARCH_TIMEOUT)
        r.raise_for_status()
        items = r.json().get("message", {}).get("items", [])
        docs = []
        for item in items[:MAX_DOCS_PER_SOURCE]:
            titles = item.get("title", [])
            title  = titles[0] if titles else ""
            link   = item.get("URL", "")
            abstract = item.get("abstract", "") or ""
            # crossref abstracts use JATS XML tags — strip them
            abstract = re.sub(r"<[^>]+>", " ", abstract).strip()
            if title and link:
                d = SourceDoc(title=title, url=link, source="crossref")
                d.text = abstract[:MAX_CHARS_PER_DOC]
                docs.append(d)
        logger.info("CrossRef: %d results", len(docs))
        return docs
    except Exception as e:
        logger.warning("CrossRef search failed: %s", e)
        return []


async def _search_core(query: str, client: httpx.AsyncClient, api_key: str = "") -> List[SourceDoc]:
    try:
        url = "https://api.core.ac.uk/v3/search/works"
        headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        params  = {"q": query, "limit": MAX_DOCS_PER_SOURCE}
        r = await client.get(url, params=params, headers=headers, timeout=SEARCH_TIMEOUT)
        if r.status_code == 401:
            logger.info("CORE: no API key, skipping")
            return []
        r.raise_for_status()
        results = r.json().get("results", [])
        docs = []
        for item in results[:MAX_DOCS_PER_SOURCE]:
            title    = item.get("title", "")
            link     = item.get("sourceFulltextUrls", [None])[0] or item.get("downloadUrl") or ""
            abstract = item.get("abstract") or ""
            if title and link:
                d = SourceDoc(title=title, url=link, source="core")
                d.text = abstract[:MAX_CHARS_PER_DOC]
                docs.append(d)
        logger.info("CORE: %d results", len(docs))
        return docs
    except Exception as e:
        logger.warning("CORE search failed: %s", e)
        return []


async def _search_tavily(query: str, client: httpx.AsyncClient, api_key: str = "") -> List[SourceDoc]:
    if not api_key:
        logger.info("Tavily: no API key, skipping")
        return []
    try:
        url = "https://api.tavily.com/search"
        payload = {
            "api_key": api_key,
            "query": query,
            "search_depth": "basic",
            "max_results": MAX_DOCS_PER_SOURCE,
            "include_raw_content": False,
        }
        r = await client.post(url, json=payload, timeout=SEARCH_TIMEOUT)
        r.raise_for_status()
        results = r.json().get("results", [])
        docs = []
        for item in results[:MAX_DOCS_PER_SOURCE]:
            title   = item.get("title", "") or item.get("url", "")
            link    = item.get("url", "")
            snippet = item.get("content", "") or ""
            if link:
                d = SourceDoc(title=title, url=link, source="tavily")
                d.text = snippet[:MAX_CHARS_PER_DOC]
                docs.append(d)
        logger.info("Tavily: %d results", len(docs))
        return docs
    except Exception as e:
        logger.warning("Tavily search failed: %s", e)
        return []


async def _search_github(query: str, client: httpx.AsyncClient, token: str = "") -> List[SourceDoc]:
    try:
        url = "https://api.github.com/search/repositories"
        headers = {"Accept": "application/vnd.github+json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        params = {"q": query, "sort": "stars", "per_page": MAX_DOCS_PER_SOURCE}
        r = await client.get(url, params=params, headers=headers, timeout=SEARCH_TIMEOUT)
        r.raise_for_status()
        items = r.json().get("items", [])
        docs = []
        for repo in items[:MAX_DOCS_PER_SOURCE]:
            title       = repo.get("full_name", "")
            link        = repo.get("html_url", "")
            description = repo.get("description") or ""
            readme_url  = f"https://raw.githubusercontent.com/{title}/HEAD/README.md"
            if title and link:
                d = SourceDoc(title=title, url=link, source="github", pdf_url=None)
                d.text = description[:500]   # seed text; README fetched later
                d.pdf_url = readme_url       # reuse field for README raw URL
                docs.append(d)
        logger.info("GitHub: %d results", len(docs))
        return docs
    except Exception as e:
        logger.warning("GitHub search failed: %s", e)
        return []


# ════════════════════════════════════════════════════════════════════════════
# 2. FETCHER  (full text / PDF / README)
# ════════════════════════════════════════════════════════════════════════════

async def _fetch_doc_text(doc: SourceDoc, client: httpx.AsyncClient) -> str:
    """
    Try to get full text for a document.
    - GitHub → fetch README markdown
    - PDF url → PyMuPDF
    - HTML url → BeautifulSoup
    Returns extracted text (may be empty on failure).
    """
    # GitHub README
    if doc.source == "github" and doc.pdf_url:
        try:
            r = await client.get(doc.pdf_url, timeout=FETCH_TIMEOUT)
            if r.status_code == 200:
                return r.text[:MAX_CHARS_PER_DOC]
        except Exception:
            pass
        return doc.text  # fall back to description

    # PDF
    if doc.pdf_url:
        try:
            r = await client.get(doc.pdf_url, timeout=FETCH_TIMEOUT)
            if r.status_code == 200 and len(r.content) > 1000:
                import fitz  # PyMuPDF
                pdf = fitz.open(stream=io.BytesIO(r.content), filetype="pdf")
                text = ""
                for page in pdf:
                    text += page.get_text()
                    if len(text) >= MAX_CHARS_PER_DOC:
                        break
                return text[:MAX_CHARS_PER_DOC]
        except Exception as e:
            logger.debug("PDF fetch failed for %s: %s", doc.url, e)

    # HTML page (only if we don't already have decent abstract text)
    if len(doc.text) < 200:
        try:
            r = await client.get(doc.url, timeout=FETCH_TIMEOUT, follow_redirects=True)
            if r.status_code == 200 and "text/html" in r.headers.get("content-type", ""):
                from bs4 import BeautifulSoup
                soup = BeautifulSoup(r.text, "html.parser")
                # remove nav/script/style noise
                for tag in soup(["script", "style", "nav", "footer", "header", "aside"]):
                    tag.decompose()
                text = soup.get_text(separator=" ", strip=True)
                return text[:MAX_CHARS_PER_DOC]
        except Exception as e:
            logger.debug("HTML fetch failed for %s: %s", doc.url, e)

    return doc.text


async def _enrich_docs(docs: List[SourceDoc], client: httpx.AsyncClient) -> List[SourceDoc]:
    """Fetch full text for all docs concurrently."""
    tasks = [_fetch_doc_text(doc, client) for doc in docs]
    texts = await asyncio.gather(*tasks, return_exceptions=True)
    for doc, text in zip(docs, texts):
        if isinstance(text, str) and text.strip():
            doc.text = text
    return docs


# ════════════════════════════════════════════════════════════════════════════
# 3. CHUNKER
# ════════════════════════════════════════════════════════════════════════════

def _chunk_text(text: str, size: int = CHUNK_SIZE, overlap: int = CHUNK_OVERLAP) -> List[str]:
    """Split text into overlapping character chunks."""
    if not text.strip():
        return []
    chunks = []
    start  = 0
    while start < len(text):
        end = start + size
        chunks.append(text[start:end].strip())
        start += size - overlap
    return [c for c in chunks if len(c) > 40]


# ════════════════════════════════════════════════════════════════════════════
# 4. EMBED + FAISS INDEX
# ════════════════════════════════════════════════════════════════════════════

def _build_faiss_index(chunks: List[str]) -> Tuple[object, object, List[str]]:
    """
    Embed chunks with SentenceTransformers and build a FAISS flat-L2 index.
    Returns (index, model, chunks) so we can query it.
    """
    model      = _get_sentence_transformer()
    faiss_mod  = _get_faiss()

    embeddings = model.encode(chunks, show_progress_bar=False, batch_size=32)
    embeddings = np.array(embeddings, dtype="float32")

    dim   = embeddings.shape[1]
    index = faiss_mod.IndexFlatL2(dim)
    index.add(embeddings)

    return index, model, chunks


def _retrieve_top_k(query: str, index, model, chunks: List[str], k: int = TOP_K_CHUNKS) -> List[str]:
    """Retrieve top-k most relevant chunks for the query."""
    q_emb = model.encode([query], show_progress_bar=False)
    q_emb = np.array(q_emb, dtype="float32")
    k     = min(k, len(chunks))
    _, indices = index.search(q_emb, k)
    return [chunks[i] for i in indices[0] if i < len(chunks)]


# ════════════════════════════════════════════════════════════════════════════
# 5. PUBLIC ENTRY POINT
# ════════════════════════════════════════════════════════════════════════════

async def run_rag_pipeline(
    query:          str,
    tavily_api_key: str = "",
    core_api_key:   str = "",
    github_token:   str = "",
) -> RAGResult:
    """
    Full async RAG pipeline.

    Returns a RAGResult with:
      - context:      assembled top-k text to inject into the LLM prompt
      - sources:      all discovered SourceDoc objects (for Learning References UI)
      - used_rag:     False if < MIN_RELIABLE_SOURCES had enough text
      - chunk_count:  total chunks indexed
      - source_count: number of sources with usable text
    """
    t0 = time.time()
    logger.info("RAG pipeline started for query: %s", query[:80])

    async with httpx.AsyncClient(
        headers={"User-Agent": "AISystemArchitect/1.0 research-rag"},
        follow_redirects=True,
    ) as client:

        # ── Phase 1: search all sources concurrently ──────────────────────
        search_tasks = [
            _search_arxiv(query, client),
            _search_semantic_scholar(query, client),
            _search_crossref(query, client),
            _search_core(query, client, core_api_key),
            _search_tavily(query, client, tavily_api_key),
            _search_github(query, client, github_token),
        ]
        results = await asyncio.gather(*search_tasks, return_exceptions=True)

        all_docs: List[SourceDoc] = []
        for r in results:
            if isinstance(r, list):
                all_docs.extend(r)

        # Deduplicate by URL
        seen_urls = set()
        unique_docs: List[SourceDoc] = []
        for doc in all_docs:
            if doc.url not in seen_urls:
                seen_urls.add(doc.url)
                unique_docs.append(doc)

        logger.info("Total unique docs found: %d", len(unique_docs))

        if not unique_docs:
            logger.warning("RAG: no documents found, falling back to prompt-only")
            return RAGResult(context="", sources=[], used_rag=False)

        # ── Phase 2: enrich (fetch full text) ────────────────────────────
        enriched = await _enrich_docs(unique_docs, client)

    # Docs with enough text to be useful
    reliable = [d for d in enriched if len(d.text.strip()) >= 100]
    logger.info("Reliable docs (≥100 chars text): %d", len(reliable))

    if len(reliable) < MIN_RELIABLE_SOURCES:
        logger.warning("RAG: only %d reliable source(s), falling back to prompt-only", len(reliable))
        return RAGResult(
            context="",
            sources=enriched,   # still return all for the UI
            used_rag=False,
            source_count=len(reliable),
        )

    # ── Phase 3: chunk ───────────────────────────────────────────────────
    all_chunks: List[str] = []
    for doc in reliable:
        header = f"[{doc.source.upper()}] {doc.title}\n"
        for chunk in _chunk_text(doc.text):
            all_chunks.append(header + chunk)

    if not all_chunks:
        return RAGResult(context="", sources=enriched, used_rag=False)

    logger.info("Total chunks to index: %d", len(all_chunks))

    # ── Phase 4: embed + FAISS ───────────────────────────────────────────
    try:
        index, model, chunks = _build_faiss_index(all_chunks)
        top_chunks = _retrieve_top_k(query, index, model, chunks, k=TOP_K_CHUNKS)
    except Exception as e:
        logger.error("FAISS indexing failed: %s", e)
        # Graceful degradation: use first N chunks without FAISS
        top_chunks = all_chunks[:TOP_K_CHUNKS]

    # ── Phase 5: assemble context ────────────────────────────────────────
    context = "\n\n---\n\n".join(top_chunks)
    context = context[:MAX_CONTEXT_CHARS]   # hard cap for token budget

    elapsed = time.time() - t0
    logger.info(
        "RAG pipeline complete in %.1fs — %d sources, %d chunks, %d top-k",
        elapsed, len(enriched), len(all_chunks), len(top_chunks),
    )

    return RAGResult(
        context=context,
        sources=enriched,
        used_rag=True,
        chunk_count=len(all_chunks),
        source_count=len(reliable),
    )


def run_rag_pipeline_sync(
    query:          str,
    tavily_api_key: str = "",
    core_api_key:   str = "",
    github_token:   str = "",
) -> RAGResult:
    """Synchronous wrapper — runs the async pipeline in a new event loop."""
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            # Inside FastAPI's async context — use asyncio.run_coroutine_threadsafe
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(
                    asyncio.run,
                    run_rag_pipeline(query, tavily_api_key, core_api_key, github_token),
                )
                return future.result(timeout=60)
        else:
            return loop.run_until_complete(
                run_rag_pipeline(query, tavily_api_key, core_api_key, github_token)
            )
    except Exception as e:
        logger.error("RAG pipeline sync wrapper failed: %s", e)
        return RAGResult(context="", sources=[], used_rag=False)