"""
rag_pipeline.py
===============
Optimized Research-driven RAG pipeline for AI System Architect.

Changes from v1
---------------
- Chunking   : plain splitter → LangChain RecursiveCharacterTextSplitter
- Retrieval  : top-k similarity → MMR (Maximal Marginal Relevance) for diversity
- Top-K      : 12 → 6 (smaller context, lower latency, higher quality signal)
- Source priority : research papers & official docs ranked above web/GitHub
- Metadata   : every chunk carries {source, title, url, doc_type, priority}
- All sources: ALL discovered links always returned for Learning References UI
               regardless of whether they pass the RAG relevance threshold
- Architecture: unchanged (same public API, same dataclasses, same fallback logic)
"""

from __future__ import annotations

import asyncio
import io
import logging
import re
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

import httpx
import numpy as np

logger = logging.getLogger(__name__)

# ── tunables ────────────────────────────────────────────────────────────────
SEARCH_TIMEOUT       = 10        # seconds per source
FETCH_TIMEOUT        = 15        # seconds per document
MAX_DOCS_PER_SOURCE  = 5
MAX_CHARS_PER_DOC    = 12_000    # truncate long docs before chunking

# RecursiveCharacterTextSplitter settings
CHUNK_SIZE           = 512       # characters — balanced for sentence-level context
CHUNK_OVERLAP        = 64        # small overlap keeps boundaries coherent
SEPARATORS           = ["\n\n", "\n", ". ", "! ", "? ", " ", ""]  # RC order

# MMR retrieval
TOP_K_FETCH          = 20        # candidates fetched from FAISS before MMR re-ranking
TOP_K_FINAL          = 6         # chunks sent to LLM after MMR (reduced from 12)
MMR_LAMBDA           = 0.6       # 0 = max diversity, 1 = max relevance

MIN_RELIABLE_SOURCES = 2         # fall back to prompt-only below this
EMBED_MODEL          = "all-MiniLM-L6-v2"
MAX_CONTEXT_CHARS    = 4_500     # tighter cap — 6 focused chunks need less space

# Source priority weights (higher = preferred for context injection)
SOURCE_PRIORITY: Dict[str, int] = {
    "arxiv":            10,
    "semantic_scholar": 10,
    "core":             9,
    "crossref":         8,
    "tavily":           5,
    "github":           4,
}


# ── data classes ────────────────────────────────────────────────────────────

@dataclass
class SourceDoc:
    title:    str
    url:      str
    source:   str                    # "arxiv"|"semantic_scholar"|"crossref"|"core"|"tavily"|"github"
    text:     str            = ""    # full extracted text (filled during fetch)
    pdf_url:  Optional[str]  = None
    # ── new metadata fields ────────────────────────────────────────────────
    authors:  List[str]      = field(default_factory=list)
    year:     Optional[str]  = None
    doi:      Optional[str]  = None
    doc_type: str            = "article"   # "paper"|"documentation"|"repo"|"article"
    priority: int            = 5           # derived from SOURCE_PRIORITY


@dataclass
class ChunkMetadata:
    """Metadata attached to every chunk before embedding."""
    source:   str
    title:    str
    url:      str
    doc_type: str
    priority: int
    chunk_idx: int


@dataclass
class RAGResult:
    context:      str               # assembled MMR-ranked text chunks
    sources:      List[SourceDoc]   # ALL discovered sources (always returned for UI)
    used_rag:     bool
    chunk_count:  int = 0
    source_count: int = 0


# ── lazy imports ─────────────────────────────────────────────────────────────

def _get_sentence_transformer():
    from sentence_transformers import SentenceTransformer
    return SentenceTransformer(EMBED_MODEL)

def _get_faiss():
    import faiss
    return faiss

def _get_splitter():
    from langchain_text_splitters import RecursiveCharacterTextSplitter
    return RecursiveCharacterTextSplitter(
        chunk_size=CHUNK_SIZE,
        chunk_overlap=CHUNK_OVERLAP,
        separators=SEPARATORS,
        length_function=len,
        is_separator_regex=False,
    )


# ════════════════════════════════════════════════════════════════════════════
# 0.5  AUTHOR NORMALISER
# ════════════════════════════════════════════════════════════════════════════

def _normalise_authors(raw: any) -> List[str]:
    """
    Coerce whatever an API returns for authors into List[str].
    Handles:
      - None / missing              → []
      - ["Alice", "Bob"]            → ["Alice", "Bob"]          (already correct)
      - [{"name": "Alice"}, ...]    → ["Alice", ...]             (CORE, S2)
      - [{"given":"A","family":"B"}]→ ["A B", ...]              (CrossRef — already handled inline)
      - "Alice, Bob"                → ["Alice", "Bob"]           (plain string)
    """
    if not raw:
        return []
    if isinstance(raw, str):
        return [a.strip() for a in raw.split(",") if a.strip()]
    if isinstance(raw, list):
        result = []
        for item in raw:
            if isinstance(item, str):
                result.append(item.strip())
            elif isinstance(item, dict):
                # CORE: {"name": "Alice Smith"}
                # Semantic Scholar: {"authorId": "...", "name": "Alice Smith"}
                name = (
                    item.get("name")
                    or f"{item.get('given', '')} {item.get('family', '')}".strip()
                    or str(item)
                )
                if name:
                    result.append(name.strip())
        return result
    return []


# ════════════════════════════════════════════════════════════════════════════
# 1. SEARCHERS  (same as v1 — enriched with extra metadata fields)
# ════════════════════════════════════════════════════════════════════════════

async def _search_arxiv(query: str, client: httpx.AsyncClient) -> List[SourceDoc]:
    try:
        params = {
            "search_query": f"all:{query}",
            "max_results":  MAX_DOCS_PER_SOURCE,
            "sortBy":       "relevance",
        }
        r = await client.get("https://export.arxiv.org/api/query", params=params, timeout=SEARCH_TIMEOUT)
        r.raise_for_status()
        from bs4 import BeautifulSoup
        soup  = BeautifulSoup(r.text, "xml")
        docs  = []
        for entry in soup.find_all("entry")[:MAX_DOCS_PER_SOURCE]:
            title   = entry.find("title").get_text(strip=True)   if entry.find("title")   else ""
            link    = entry.find("id").get_text(strip=True)       if entry.find("id")      else ""
            summary = entry.find("summary").get_text(strip=True)  if entry.find("summary") else ""
            authors = _normalise_authors([a.find("name").get_text(strip=True) for a in entry.find_all("author") if a.find("name")])
            year    = ""
            pub_tag = entry.find("published")
            if pub_tag:
                year = pub_tag.get_text(strip=True)[:4]
            pdf_url = link.replace("/abs/", "/pdf/") + ".pdf" if "/abs/" in link else None
            if title and link:
                d = SourceDoc(
                    title=title, url=link, source="arxiv",
                    pdf_url=pdf_url, authors=authors, year=year,
                    doc_type="paper", priority=SOURCE_PRIORITY["arxiv"],
                )
                d.text = summary[:MAX_CHARS_PER_DOC]
                docs.append(d)
        logger.info("arXiv: %d results", len(docs))
        return docs
    except Exception as e:
        logger.warning("arXiv search failed: %s", e)
        return []


async def _search_semantic_scholar(query: str, client: httpx.AsyncClient) -> List[SourceDoc]:
    try:
        params = {
            "query":  query,
            "limit":  MAX_DOCS_PER_SOURCE,
            "fields": "title,abstract,url,openAccessPdf,authors,year",
        }
        r = await client.get(
            "https://api.semanticscholar.org/graph/v1/paper/search",
            params=params, timeout=SEARCH_TIMEOUT,
        )
        r.raise_for_status()
        docs = []
        for p in r.json().get("data", [])[:MAX_DOCS_PER_SOURCE]:
            title    = p.get("title", "")
            link     = p.get("url", "") or f"https://www.semanticscholar.org/paper/{p.get('paperId','')}"
            abstract = p.get("abstract") or ""
            pdf_url  = (p.get("openAccessPdf") or {}).get("url")
            authors  = _normalise_authors(p.get("authors"))
            year     = str(p.get("year", "")) if p.get("year") else ""
            if title:
                d = SourceDoc(
                    title=title, url=link, source="semantic_scholar",
                    pdf_url=pdf_url, authors=authors, year=year,
                    doc_type="paper", priority=SOURCE_PRIORITY["semantic_scholar"],
                )
                d.text = abstract[:MAX_CHARS_PER_DOC]
                docs.append(d)
        logger.info("Semantic Scholar: %d results", len(docs))
        return docs
    except Exception as e:
        logger.warning("Semantic Scholar search failed: %s", e)
        return []


async def _search_crossref(query: str, client: httpx.AsyncClient) -> List[SourceDoc]:
    try:
        params = {"query": query, "rows": MAX_DOCS_PER_SOURCE, "select": "title,URL,abstract,author,published,DOI"}
        r = await client.get("https://api.crossref.org/works", params=params, timeout=SEARCH_TIMEOUT)
        r.raise_for_status()
        docs = []
        for item in r.json().get("message", {}).get("items", [])[:MAX_DOCS_PER_SOURCE]:
            titles   = item.get("title", [])
            title    = titles[0] if titles else ""
            link     = item.get("URL", "")
            doi      = item.get("DOI", "")
            abstract = re.sub(r"<[^>]+>", " ", item.get("abstract", "") or "").strip()
            authors  = [
                f"{a.get('given','')} {a.get('family','')}".strip()
                for a in (item.get("author") or [])
            ]
            year = ""
            pub  = item.get("published", {}).get("date-parts", [[]])
            if pub and pub[0]:
                year = str(pub[0][0])
            if title and link:
                d = SourceDoc(
                    title=title, url=link, source="crossref",
                    doi=doi, authors=authors, year=year,
                    doc_type="paper", priority=SOURCE_PRIORITY["crossref"],
                )
                d.text = abstract[:MAX_CHARS_PER_DOC]
                docs.append(d)
        logger.info("CrossRef: %d results", len(docs))
        return docs
    except Exception as e:
        logger.warning("CrossRef search failed: %s", e)
        return []


async def _search_core(query: str, client: httpx.AsyncClient, api_key: str = "") -> List[SourceDoc]:
    try:
        headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        params  = {"q": query, "limit": MAX_DOCS_PER_SOURCE}
        r = await client.get(
            "https://api.core.ac.uk/v3/search/works",
            params=params, headers=headers, timeout=SEARCH_TIMEOUT,
        )
        if r.status_code == 401:
            logger.info("CORE: no API key, skipping")
            return []
        r.raise_for_status()
        docs = []
        for item in r.json().get("results", [])[:MAX_DOCS_PER_SOURCE]:
            title    = item.get("title", "")
            link     = (item.get("sourceFulltextUrls") or [None])[0] or item.get("downloadUrl") or ""
            abstract = item.get("abstract") or ""
            authors  = _normalise_authors(item.get("authors"))
            year     = str(item.get("yearPublished", "")) if item.get("yearPublished") else ""
            doi      = item.get("doi", "")
            if title and link:
                d = SourceDoc(
                    title=title, url=link, source="core",
                    doi=doi, authors=authors, year=year,
                    doc_type="paper", priority=SOURCE_PRIORITY["core"],
                )
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
        payload = {
            "api_key":       api_key,
            "query":         query,
            "search_depth":  "basic",
            "max_results":   MAX_DOCS_PER_SOURCE,
            "include_raw_content": False,
        }
        r = await client.post("https://api.tavily.com/search", json=payload, timeout=SEARCH_TIMEOUT)
        r.raise_for_status()
        docs = []
        for item in r.json().get("results", [])[:MAX_DOCS_PER_SOURCE]:
            title   = item.get("title", "") or item.get("url", "")
            link    = item.get("url", "")
            snippet = item.get("content", "") or ""
            # Infer doc_type from URL for priority bumping
            url_lower = link.lower()
            doc_type = "documentation" if any(k in url_lower for k in [
                "docs.", "/docs/", "documentation", "readthedocs", "developer.", "/api/", "/reference/"
            ]) else "article"
            priority = SOURCE_PRIORITY["tavily"] + (2 if doc_type == "documentation" else 0)
            if link:
                d = SourceDoc(
                    title=title, url=link, source="tavily",
                    doc_type=doc_type, priority=priority,
                )
                d.text = snippet[:MAX_CHARS_PER_DOC]
                docs.append(d)
        logger.info("Tavily: %d results", len(docs))
        return docs
    except Exception as e:
        logger.warning("Tavily search failed: %s", e)
        return []


async def _search_github(query: str, client: httpx.AsyncClient, token: str = "") -> List[SourceDoc]:
    try:
        headers = {"Accept": "application/vnd.github+json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        params = {"q": query, "sort": "stars", "per_page": MAX_DOCS_PER_SOURCE}
        r = await client.get(
            "https://api.github.com/search/repositories",
            params=params, headers=headers, timeout=SEARCH_TIMEOUT,
        )
        r.raise_for_status()
        docs = []
        for repo in r.json().get("items", [])[:MAX_DOCS_PER_SOURCE]:
            name        = repo.get("full_name", "")
            link        = repo.get("html_url", "")
            description = repo.get("description") or ""
            stars       = repo.get("stargazers_count", 0)
            language    = repo.get("language") or ""
            readme_url  = f"https://raw.githubusercontent.com/{name}/HEAD/README.md"
            if name and link:
                d = SourceDoc(
                    title=f"{name} ({'⭐'+str(stars) if stars else language})",
                    url=link, source="github",
                    pdf_url=readme_url,        # reused for README fetch
                    doc_type="repo",
                    priority=SOURCE_PRIORITY["github"],
                )
                d.text = description[:500]
                docs.append(d)
        logger.info("GitHub: %d results", len(docs))
        return docs
    except Exception as e:
        logger.warning("GitHub search failed: %s", e)
        return []


# ════════════════════════════════════════════════════════════════════════════
# 2. FETCHER  (unchanged logic, same BS4 + PyMuPDF approach)
# ════════════════════════════════════════════════════════════════════════════

async def _fetch_doc_text(doc: SourceDoc, client: httpx.AsyncClient) -> str:
    # GitHub README
    if doc.source == "github" and doc.pdf_url:
        try:
            r = await client.get(doc.pdf_url, timeout=FETCH_TIMEOUT)
            if r.status_code == 200:
                return r.text[:MAX_CHARS_PER_DOC]
        except Exception:
            pass
        return doc.text

    # PDF via PyMuPDF
    if doc.pdf_url:
        try:
            r = await client.get(doc.pdf_url, timeout=FETCH_TIMEOUT)
            if r.status_code == 200 and len(r.content) > 1_000:
                import fitz
                pdf  = fitz.open(stream=io.BytesIO(r.content), filetype="pdf")
                text = ""
                for page in pdf:
                    text += page.get_text()
                    if len(text) >= MAX_CHARS_PER_DOC:
                        break
                return text[:MAX_CHARS_PER_DOC]
        except Exception as e:
            logger.debug("PDF fetch failed for %s: %s", doc.url, e)

    # HTML via BeautifulSoup (only if abstract is thin)
    if len(doc.text) < 200:
        try:
            r = await client.get(doc.url, timeout=FETCH_TIMEOUT, follow_redirects=True)
            if r.status_code == 200 and "text/html" in r.headers.get("content-type", ""):
                from bs4 import BeautifulSoup
                soup = BeautifulSoup(r.text, "html.parser")
                for tag in soup(["script", "style", "nav", "footer", "header", "aside"]):
                    tag.decompose()
                return soup.get_text(separator=" ", strip=True)[:MAX_CHARS_PER_DOC]
        except Exception as e:
            logger.debug("HTML fetch failed for %s: %s", doc.url, e)

    return doc.text


async def _enrich_docs(docs: List[SourceDoc], client: httpx.AsyncClient) -> List[SourceDoc]:
    texts = await asyncio.gather(*[_fetch_doc_text(d, client) for d in docs], return_exceptions=True)
    for doc, text in zip(docs, texts):
        if isinstance(text, str) and text.strip():
            doc.text = text
    return docs


# ════════════════════════════════════════════════════════════════════════════
# 3. CHUNKER — RecursiveCharacterTextSplitter
# ════════════════════════════════════════════════════════════════════════════

def _chunk_doc(doc: SourceDoc) -> Tuple[List[str], List[ChunkMetadata]]:
    """
    Split a SourceDoc into chunks using RecursiveCharacterTextSplitter,
    attaching metadata to every chunk.
    Returns (texts, metadatas).
    """
    if not doc.text.strip():
        return [], []

    splitter = _get_splitter()
    raw_chunks = splitter.split_text(doc.text)

    # Build metadata-prefixed texts for LLM consumption
    # Format: [SOURCE | title | year | authors] \n chunk_text
    author_str = ", ".join(str(a) for a in doc.authors[:3]) if doc.authors else ""
    meta_header = f"[{doc.source.upper()}] {doc.title}"
    if doc.year:
        meta_header += f" ({doc.year})"
    if author_str:
        meta_header += f" — {author_str}"
    meta_header += "\n"

    texts = []
    metas = []
    for idx, chunk in enumerate(raw_chunks):
        if len(chunk.strip()) < 40:
            continue
        texts.append(meta_header + chunk.strip())
        metas.append(ChunkMetadata(
            source=doc.source,
            title=doc.title,
            url=doc.url,
            doc_type=doc.doc_type,
            priority=doc.priority,
            chunk_idx=idx,
        ))
    return texts, metas


# ════════════════════════════════════════════════════════════════════════════
# 4. EMBED + FAISS  (Inner-product index for cosine similarity)
# ════════════════════════════════════════════════════════════════════════════

def _build_index(chunks: List[str]) -> Tuple[Any, Any, np.ndarray]:
    """
    Embed chunks → L2-normalise → FAISS IndexFlatIP (inner product ≡ cosine).
    Returns (index, model, embeddings_array).
    """
    model      = _get_sentence_transformer()
    faiss_mod  = _get_faiss()

    embs = model.encode(chunks, show_progress_bar=False, batch_size=32, normalize_embeddings=True)
    embs = np.array(embs, dtype="float32")

    index = faiss_mod.IndexFlatIP(embs.shape[1])
    index.add(embs)
    return index, model, embs


# ════════════════════════════════════════════════════════════════════════════
# 5. MMR RETRIEVAL
# ════════════════════════════════════════════════════════════════════════════

def _mmr_retrieve(
    query: str,
    index,
    model,
    all_embeddings: np.ndarray,
    chunks:         List[str],
    metadatas:      List[ChunkMetadata],
    fetch_k: int = TOP_K_FETCH,
    final_k: int = TOP_K_FINAL,
    lambda_: float = MMR_LAMBDA,
) -> Tuple[List[str], List[ChunkMetadata]]:
    """
    Maximal Marginal Relevance retrieval:
      1. Fetch top fetch_k candidates by cosine similarity.
      2. Apply priority boost: research papers score higher.
      3. Iteratively pick the chunk that maximises
         λ·relevance − (1−λ)·max_similarity_to_already_selected.

    Returns (selected_chunks, selected_metadatas).
    """
    q_emb = model.encode([query], show_progress_bar=False, normalize_embeddings=True)
    q_emb = np.array(q_emb, dtype="float32")

    fetch_k = min(fetch_k, len(chunks))
    _, candidate_indices = index.search(q_emb, fetch_k)
    candidate_indices = candidate_indices[0]

    # Relevance scores (cosine — higher = more relevant)
    # Compute dot product of query with each candidate embedding
    cand_embs = all_embeddings[candidate_indices]            # (fetch_k, dim)
    relevance  = (cand_embs @ q_emb.T).squeeze()            # (fetch_k,)

    # Priority boost: add a small bonus so papers float up
    priority_bonus = np.array(
        [metadatas[i].priority / 100.0 for i in candidate_indices],
        dtype="float32",
    )
    relevance = relevance + priority_bonus

    selected_local_indices: List[int] = []  # indices into candidate_indices

    for _ in range(min(final_k, fetch_k)):
        if not selected_local_indices:
            # First pick: highest relevance
            best_local = int(np.argmax(relevance))
        else:
            # MMR score = λ·relevance − (1−λ)·max_sim_to_selected
            selected_embs = cand_embs[selected_local_indices]   # (n_selected, dim)
            sim_to_selected = (cand_embs @ selected_embs.T).max(axis=1)  # (fetch_k,)
            mmr_scores = lambda_ * relevance - (1 - lambda_) * sim_to_selected

            # Mask already selected
            for idx in selected_local_indices:
                mmr_scores[idx] = -np.inf

            best_local = int(np.argmax(mmr_scores))

        selected_local_indices.append(best_local)

    # Map back to global chunk indices
    result_chunks: List[str]          = []
    result_metas:  List[ChunkMetadata] = []
    for li in selected_local_indices:
        gi = candidate_indices[li]
        result_chunks.append(chunks[gi])
        result_metas.append(metadatas[gi])

    # Sort final selection: papers first, then by source priority descending
    paired = sorted(
        zip(result_chunks, result_metas),
        key=lambda x: (-x[1].priority, x[1].doc_type != "paper"),
    )
    if paired:
        result_chunks, result_metas = zip(*paired)
        return list(result_chunks), list(result_metas)
    return [], []


# ════════════════════════════════════════════════════════════════════════════
# 6. PUBLIC ENTRY POINT  (same signature as v1)
# ════════════════════════════════════════════════════════════════════════════

async def run_rag_pipeline(
    query:          str,
    tavily_api_key: str = "",
    core_api_key:   str = "",
    github_token:   str = "",
) -> RAGResult:
    """
    Optimized async RAG pipeline.

    Returns RAGResult with:
      context      — MMR-ranked, priority-sorted text (≤ MAX_CONTEXT_CHARS)
      sources      — ALL discovered SourceDoc objects (papers + docs + repos + web)
      used_rag     — False when < MIN_RELIABLE_SOURCES pass the text threshold
      chunk_count  — total chunks indexed
      source_count — sources with usable text
    """
    t0 = time.time()
    logger.info("RAG pipeline v2 started for: %s", query[:80])

    async with httpx.AsyncClient(
        headers={"User-Agent": "AISystemArchitect/2.0 research-rag"},
        follow_redirects=True,
    ) as client:

        # ── Phase 1: search all 6 sources concurrently ───────────────────
        results = await asyncio.gather(
            _search_arxiv(query, client),
            _search_semantic_scholar(query, client),
            _search_crossref(query, client),
            _search_core(query, client, core_api_key),
            _search_tavily(query, client, tavily_api_key),
            _search_github(query, client, github_token),
            return_exceptions=True,
        )

        all_docs: List[SourceDoc] = []
        for r in results:
            if isinstance(r, list):
                all_docs.extend(r)

        # Deduplicate by URL
        seen: set = set()
        unique_docs: List[SourceDoc] = []
        for doc in all_docs:
            if doc.url and doc.url not in seen:
                seen.add(doc.url)
                unique_docs.append(doc)

        logger.info("Unique docs discovered: %d", len(unique_docs))

        if not unique_docs:
            logger.warning("RAG: no documents found — falling back to prompt-only")
            return RAGResult(context="", sources=[], used_rag=False)

        # ── Phase 2: enrich (fetch full text) ────────────────────────────
        enriched = await _enrich_docs(unique_docs, client)

    # ALL sources returned for UI — even low-text ones
    all_sources = enriched

    # Filter for reliable (enough text to chunk)
    reliable = [d for d in enriched if len(d.text.strip()) >= 100]
    logger.info("Reliable docs (text ≥ 100 chars): %d / %d", len(reliable), len(enriched))

    if len(reliable) < MIN_RELIABLE_SOURCES:
        logger.warning("RAG: too few reliable sources (%d) — falling back to prompt-only", len(reliable))
        return RAGResult(
            context="",
            sources=all_sources,      # still expose all links to the UI
            used_rag=False,
            source_count=len(reliable),
        )

    # Sort reliable docs by priority so high-priority sources get chunked first
    reliable.sort(key=lambda d: -d.priority)

    # ── Phase 3: chunk with RecursiveCharacterTextSplitter ────────────────
    all_chunks:  List[str]           = []
    all_metadatas: List[ChunkMetadata] = []

    for doc in reliable:
        chunks, metas = _chunk_doc(doc)
        all_chunks.extend(chunks)
        all_metadatas.extend(metas)

    if not all_chunks:
        return RAGResult(context="", sources=all_sources, used_rag=False)

    logger.info("Total chunks indexed: %d", len(all_chunks))

    # ── Phase 4: embed + FAISS ────────────────────────────────────────────
    try:
        index, model, embeddings = _build_index(all_chunks)
    except Exception as e:
        logger.error("FAISS build failed: %s — using first %d chunks", e, TOP_K_FINAL)
        top_chunks = all_chunks[:TOP_K_FINAL]
        top_metas  = all_metadatas[:TOP_K_FINAL]
    else:
        # ── Phase 5: MMR retrieval ────────────────────────────────────────
        top_chunks, top_metas = _mmr_retrieve(
            query=query,
            index=index,
            model=model,
            all_embeddings=embeddings,
            chunks=all_chunks,
            metadatas=all_metadatas,
            fetch_k=TOP_K_FETCH,
            final_k=TOP_K_FINAL,
            lambda_=MMR_LAMBDA,
        )

    # ── Phase 6: assemble context ─────────────────────────────────────────
    context = "\n\n---\n\n".join(top_chunks)
    context = context[:MAX_CONTEXT_CHARS]

    elapsed = time.time() - t0
    logger.info(
        "RAG v2 complete in %.1fs — %d total sources, %d reliable, %d chunks, %d sent to LLM",
        elapsed, len(all_sources), len(reliable), len(all_chunks), len(top_chunks),
    )

    return RAGResult(
        context=context,
        sources=all_sources,      # ALL sources — not just the ones selected by MMR
        used_rag=True,
        chunk_count=len(all_chunks),
        source_count=len(reliable),
    )


# ════════════════════════════════════════════════════════════════════════════
# 7. SYNC WRAPPER  (same as v1)
# ════════════════════════════════════════════════════════════════════════════

def run_rag_pipeline_sync(
    query:          str,
    tavily_api_key: str = "",
    core_api_key:   str = "",
    github_token:   str = "",
) -> RAGResult:
    """
    Synchronous wrapper — runs the async pipeline safely from any context.

    Python 3.10+ deprecates asyncio.get_event_loop() when no loop is running.
    FastAPI runs its own event loop, so we must NEVER call loop.run_until_complete()
    from inside it. We always spin up a fresh loop in a ThreadPoolExecutor thread,
    which is guaranteed to have no running loop.
    """
    import concurrent.futures

    def _run_in_thread() -> RAGResult:
        # Each ThreadPoolExecutor thread starts with no event loop — safe to use asyncio.run()
        return asyncio.run(
            run_rag_pipeline(query, tavily_api_key, core_api_key, github_token)
        )

    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(_run_in_thread)
            return future.result(timeout=90)
    except concurrent.futures.TimeoutError:
        logger.error("RAG pipeline timed out after 90s")
        return RAGResult(context="", sources=[], used_rag=False)
    except Exception as e:
        logger.error("RAG sync wrapper failed: %s", e, exc_info=True)
        return RAGResult(context="", sources=[], used_rag=False)

# -------------------------------------------------------------------------------------------------------- backup

# """
# rag_pipeline.py
# ===============
# Research-driven RAG pipeline for AI System Architect.

# Flow
# ----
# 1. Search  — Semantic Scholar, CrossRef, arXiv, CORE, Tavily, GitHub (concurrent)
# 2. Fetch   — download full text / PDF / README with BeautifulSoup + PyMuPDF
# 3. Embed   — chunk text → SentenceTransformers → FAISS index
# 4. Retrieve — top-k chunks most relevant to the query
# 5. Fallback — if < MIN_RELIABLE_SOURCES found, skip RAG and return empty context

# All retrieved source links are always returned so the frontend can display them
# in the Learning References section regardless of whether RAG was used.
# """

# from __future__ import annotations

# import asyncio
# import io
# import logging
# import re
# import time
# from dataclasses import dataclass, field
# from typing import List, Optional, Tuple
# from bs4 import BeautifulSoup

# import httpx
# import numpy as np

# logger = logging.getLogger(__name__)

# # ── tunables ────────────────────────────────────────────────────────────────
# SEARCH_TIMEOUT        = 10      # seconds per source
# FETCH_TIMEOUT         = 15      # seconds per document
# MAX_DOCS_PER_SOURCE   = 5
# MAX_CHARS_PER_DOC     = 12_000  # truncate long docs before chunking
# CHUNK_SIZE            = 400     # chars per chunk
# CHUNK_OVERLAP         = 80
# TOP_K_CHUNKS          = 12      # chunks fed to LLM
# MIN_RELIABLE_SOURCES  = 2       # fall back to prompt-only below this
# EMBED_MODEL           = "all-MiniLM-L6-v2"
# MAX_CONTEXT_CHARS     = 6_000   # total RAG context injected into prompt


# # ── data classes ────────────────────────────────────────────────────────────

# @dataclass
# class SourceDoc:
#     title:  str
#     url:    str
#     source: str          # "arxiv" | "semantic_scholar" | "crossref" | "core" | "tavily" | "github"
#     text:   str  = ""    # full extracted text (filled during fetch)
#     pdf_url: Optional[str] = None


# @dataclass
# class RAGResult:
#     context:        str               # assembled top-k text chunks
#     sources:        List[SourceDoc]   # all discovered sources (for UI)
#     used_rag:       bool              # True if RAG context was injected
#     chunk_count:    int = 0
#     source_count:   int = 0


# # ── lazy imports (heavy deps only loaded when actually used) ─────────────────

# def _get_sentence_transformer():
#     from sentence_transformers import SentenceTransformer
#     return SentenceTransformer(EMBED_MODEL)

# def _get_faiss():
#     import faiss
#     return faiss


# # ════════════════════════════════════════════════════════════════════════════
# # 1. SEARCHERS
# # ════════════════════════════════════════════════════════════════════════════

# async def _search_arxiv(query: str, client: httpx.AsyncClient) -> List[SourceDoc]:
#     try:
#         url = "https://export.arxiv.org/api/query"
#         params = {"search_query": f"all:{query}", "max_results": MAX_DOCS_PER_SOURCE, "sortBy": "relevance"}
#         r = await client.get(url, params=params, timeout=SEARCH_TIMEOUT)
#         r.raise_for_status()

#         soup = BeautifulSoup(r.text, "xml")
#         docs = []
#         for entry in soup.find_all("entry")[:MAX_DOCS_PER_SOURCE]:
#             title   = entry.find("title").get_text(strip=True) if entry.find("title") else ""
#             link    = entry.find("id").get_text(strip=True)    if entry.find("id")    else ""
#             pdf_url = link.replace("/abs/", "/pdf/") + ".pdf"  if "/abs/" in link else None
#             summary = entry.find("summary").get_text(strip=True) if entry.find("summary") else ""
#             if title and link:
#                 d = SourceDoc(title=title, url=link, source="arxiv", pdf_url=pdf_url)
#                 d.text = summary[:MAX_CHARS_PER_DOC]
#                 docs.append(d)
#         logger.info("arXiv: %d results", len(docs))
#         return docs
#     except Exception as e:
#         logger.warning("arXiv search failed: %s", e)
#         return []


# async def _search_semantic_scholar(query: str, client: httpx.AsyncClient) -> List[SourceDoc]:
#     try:
#         url = "https://api.semanticscholar.org/graph/v1/paper/search"
#         params = {
#             "query": query,
#             "limit": MAX_DOCS_PER_SOURCE,
#             "fields": "title,abstract,url,openAccessPdf",
#         }
#         r = await client.get(url, params=params, timeout=SEARCH_TIMEOUT)
#         r.raise_for_status()
#         data = r.json().get("data", [])
#         docs = []
#         for p in data[:MAX_DOCS_PER_SOURCE]:
#             title   = p.get("title", "")
#             link    = p.get("url", "") or f"https://www.semanticscholar.org/paper/{p.get('paperId','')}"
#             abstract= p.get("abstract") or ""
#             pdf_url = (p.get("openAccessPdf") or {}).get("url")
#             if title:
#                 d = SourceDoc(title=title, url=link, source="semantic_scholar", pdf_url=pdf_url)
#                 d.text = abstract[:MAX_CHARS_PER_DOC]
#                 docs.append(d)
#         logger.info("Semantic Scholar: %d results", len(docs))
#         return docs
#     except Exception as e:
#         logger.warning("Semantic Scholar search failed: %s", e)
#         return []


# async def _search_crossref(query: str, client: httpx.AsyncClient) -> List[SourceDoc]:
#     try:
#         url = "https://api.crossref.org/works"
#         params = {"query": query, "rows": MAX_DOCS_PER_SOURCE, "select": "title,URL,abstract"}
#         r = await client.get(url, params=params, timeout=SEARCH_TIMEOUT)
#         r.raise_for_status()
#         items = r.json().get("message", {}).get("items", [])
#         docs = []
#         for item in items[:MAX_DOCS_PER_SOURCE]:
#             titles = item.get("title", [])
#             title  = titles[0] if titles else ""
#             link   = item.get("URL", "")
#             abstract = item.get("abstract", "") or ""
#             # crossref abstracts use JATS XML tags — strip them
#             abstract = re.sub(r"<[^>]+>", " ", abstract).strip()
#             if title and link:
#                 d = SourceDoc(title=title, url=link, source="crossref")
#                 d.text = abstract[:MAX_CHARS_PER_DOC]
#                 docs.append(d)
#         logger.info("CrossRef: %d results", len(docs))
#         return docs
#     except Exception as e:
#         logger.warning("CrossRef search failed: %s", e)
#         return []


# async def _search_core(query: str, client: httpx.AsyncClient, api_key: str = "") -> List[SourceDoc]:
#     try:
#         url = "https://api.core.ac.uk/v3/search/works"
#         headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
#         params  = {"q": query, "limit": MAX_DOCS_PER_SOURCE}
#         r = await client.get(url, params=params, headers=headers, timeout=SEARCH_TIMEOUT)
#         if r.status_code == 401:
#             logger.info("CORE: no API key, skipping")
#             return []
#         r.raise_for_status()
#         results = r.json().get("results", [])
#         docs = []
#         for item in results[:MAX_DOCS_PER_SOURCE]:
#             title    = item.get("title", "")
#             link     = item.get("sourceFulltextUrls", [None])[0] or item.get("downloadUrl") or ""
#             abstract = item.get("abstract") or ""
#             if title and link:
#                 d = SourceDoc(title=title, url=link, source="core")
#                 d.text = abstract[:MAX_CHARS_PER_DOC]
#                 docs.append(d)
#         logger.info("CORE: %d results", len(docs))
#         return docs
#     except Exception as e:
#         logger.warning("CORE search failed: %s", e)
#         return []


# async def _search_tavily(query: str, client: httpx.AsyncClient, api_key: str = "") -> List[SourceDoc]:
#     if not api_key:
#         logger.info("Tavily: no API key, skipping")
#         return []
#     try:
#         url = "https://api.tavily.com/search"
#         payload = {
#             "api_key": api_key,
#             "query": query,
#             "search_depth": "basic",
#             "max_results": MAX_DOCS_PER_SOURCE,
#             "include_raw_content": False,
#         }
#         r = await client.post(url, json=payload, timeout=SEARCH_TIMEOUT)
#         r.raise_for_status()
#         results = r.json().get("results", [])
#         docs = []
#         for item in results[:MAX_DOCS_PER_SOURCE]:
#             title   = item.get("title", "") or item.get("url", "")
#             link    = item.get("url", "")
#             snippet = item.get("content", "") or ""
#             if link:
#                 d = SourceDoc(title=title, url=link, source="tavily")
#                 d.text = snippet[:MAX_CHARS_PER_DOC]
#                 docs.append(d)
#         logger.info("Tavily: %d results", len(docs))
#         return docs
#     except Exception as e:
#         logger.warning("Tavily search failed: %s", e)
#         return []


# async def _search_github(query: str, client: httpx.AsyncClient, token: str = "") -> List[SourceDoc]:
#     try:
#         url = "https://api.github.com/search/repositories"
#         headers = {"Accept": "application/vnd.github+json"}
#         if token:
#             headers["Authorization"] = f"Bearer {token}"
#         params = {"q": query, "sort": "stars", "per_page": MAX_DOCS_PER_SOURCE}
#         r = await client.get(url, params=params, headers=headers, timeout=SEARCH_TIMEOUT)
#         r.raise_for_status()
#         items = r.json().get("items", [])
#         docs = []
#         for repo in items[:MAX_DOCS_PER_SOURCE]:
#             title       = repo.get("full_name", "")
#             link        = repo.get("html_url", "")
#             description = repo.get("description") or ""
#             readme_url  = f"https://raw.githubusercontent.com/{title}/HEAD/README.md"
#             if title and link:
#                 d = SourceDoc(title=title, url=link, source="github", pdf_url=None)
#                 d.text = description[:500]   # seed text; README fetched later
#                 d.pdf_url = readme_url       # reuse field for README raw URL
#                 docs.append(d)
#         logger.info("GitHub: %d results", len(docs))
#         return docs
#     except Exception as e:
#         logger.warning("GitHub search failed: %s", e)
#         return []


# # ════════════════════════════════════════════════════════════════════════════
# # 2. FETCHER  (full text / PDF / README)
# # ════════════════════════════════════════════════════════════════════════════

# async def _fetch_doc_text(doc: SourceDoc, client: httpx.AsyncClient) -> str:
#     """
#     Try to get full text for a document.
#     - GitHub → fetch README markdown
#     - PDF url → PyMuPDF
#     - HTML url → BeautifulSoup
#     Returns extracted text (may be empty on failure).
#     """
#     # GitHub README
#     if doc.source == "github" and doc.pdf_url:
#         try:
#             r = await client.get(doc.pdf_url, timeout=FETCH_TIMEOUT)
#             if r.status_code == 200:
#                 return r.text[:MAX_CHARS_PER_DOC]
#         except Exception:
#             pass
#         return doc.text  # fall back to description

#     # PDF
#     if doc.pdf_url:
#         try:
#             r = await client.get(doc.pdf_url, timeout=FETCH_TIMEOUT)
#             if r.status_code == 200 and len(r.content) > 1000:
#                 import fitz  # PyMuPDF
#                 pdf = fitz.open(stream=io.BytesIO(r.content), filetype="pdf")
#                 text = ""
#                 for page in pdf:
#                     text += page.get_text()
#                     if len(text) >= MAX_CHARS_PER_DOC:
#                         break
#                 return text[:MAX_CHARS_PER_DOC]
#         except Exception as e:
#             logger.debug("PDF fetch failed for %s: %s", doc.url, e)

#     # HTML page (only if we don't already have decent abstract text)
#     if len(doc.text) < 200:
#         try:
#             r = await client.get(doc.url, timeout=FETCH_TIMEOUT, follow_redirects=True)
#             if r.status_code == 200 and "text/html" in r.headers.get("content-type", ""):
#                 soup = BeautifulSoup(r.text, "html.parser")
#                 # remove nav/script/style noise
#                 for tag in soup(["script", "style", "nav", "footer", "header", "aside"]):
#                     tag.decompose()
#                 text = soup.get_text(separator=" ", strip=True)
#                 return text[:MAX_CHARS_PER_DOC]
#         except Exception as e:
#             logger.debug("HTML fetch failed for %s: %s", doc.url, e)

#     return doc.text


# async def _enrich_docs(docs: List[SourceDoc], client: httpx.AsyncClient) -> List[SourceDoc]:
#     """Fetch full text for all docs concurrently."""
#     tasks = [_fetch_doc_text(doc, client) for doc in docs]
#     texts = await asyncio.gather(*tasks, return_exceptions=True)
#     for doc, text in zip(docs, texts):
#         if isinstance(text, str) and text.strip():
#             doc.text = text
#     return docs


# # ════════════════════════════════════════════════════════════════════════════
# # 3. CHUNKER
# # ════════════════════════════════════════════════════════════════════════════

# def _chunk_text(text: str, size: int = CHUNK_SIZE, overlap: int = CHUNK_OVERLAP) -> List[str]:
#     """Split text into overlapping character chunks."""
#     if not text.strip():
#         return []
#     chunks = []
#     start  = 0
#     while start < len(text):
#         end = start + size
#         chunks.append(text[start:end].strip())
#         start += size - overlap
#     return [c for c in chunks if len(c) > 40]


# # ════════════════════════════════════════════════════════════════════════════
# # 4. EMBED + FAISS INDEX
# # ════════════════════════════════════════════════════════════════════════════

# def _build_faiss_index(chunks: List[str]) -> Tuple[object, object, List[str]]:
#     """
#     Embed chunks with SentenceTransformers and build a FAISS flat-L2 index.
#     Returns (index, model, chunks) so we can query it.
#     """
#     model      = _get_sentence_transformer()
#     faiss_mod  = _get_faiss()

#     embeddings = model.encode(chunks, show_progress_bar=False, batch_size=32)
#     embeddings = np.array(embeddings, dtype="float32")

#     dim   = embeddings.shape[1]
#     index = faiss_mod.IndexFlatL2(dim)
#     index.add(embeddings)

#     return index, model, chunks


# def _retrieve_top_k(query: str, index, model, chunks: List[str], k: int = TOP_K_CHUNKS) -> List[str]:
#     """Retrieve top-k most relevant chunks for the query."""
#     q_emb = model.encode([query], show_progress_bar=False)
#     q_emb = np.array(q_emb, dtype="float32")
#     k     = min(k, len(chunks))
#     _, indices = index.search(q_emb, k)
#     return [chunks[i] for i in indices[0] if i < len(chunks)]


# # ════════════════════════════════════════════════════════════════════════════
# # 5. PUBLIC ENTRY POINT
# # ════════════════════════════════════════════════════════════════════════════

# async def run_rag_pipeline(
#     query:          str,
#     tavily_api_key: str = "",
#     core_api_key:   str = "",
#     github_token:   str = "",
# ) -> RAGResult:
#     """
#     Full async RAG pipeline.

#     Returns a RAGResult with:
#       - context:      assembled top-k text to inject into the LLM prompt
#       - sources:      all discovered SourceDoc objects (for Learning References UI)
#       - used_rag:     False if < MIN_RELIABLE_SOURCES had enough text
#       - chunk_count:  total chunks indexed
#       - source_count: number of sources with usable text
#     """
#     t0 = time.time()
#     logger.info("RAG pipeline started for query: %s", query[:80])

#     async with httpx.AsyncClient(
#         headers={"User-Agent": "AISystemArchitect/1.0 research-rag"},
#         follow_redirects=True,
#     ) as client:

#         # ── Phase 1: search all sources concurrently ──────────────────────
#         search_tasks = [
#             _search_arxiv(query, client),
#             _search_semantic_scholar(query, client),
#             _search_crossref(query, client),
#             _search_core(query, client, core_api_key),
#             _search_tavily(query, client, tavily_api_key),
#             _search_github(query, client, github_token),
#         ]
#         results = await asyncio.gather(*search_tasks, return_exceptions=True)

#         all_docs: List[SourceDoc] = []
#         for r in results:
#             if isinstance(r, list):
#                 all_docs.extend(r)

#         # Deduplicate by URL
#         seen_urls = set()
#         unique_docs: List[SourceDoc] = []
#         for doc in all_docs:
#             if doc.url not in seen_urls:
#                 seen_urls.add(doc.url)
#                 unique_docs.append(doc)

#         logger.info("Total unique docs found: %d", len(unique_docs))

#         if not unique_docs:
#             logger.warning("RAG: no documents found, falling back to prompt-only")
#             return RAGResult(context="", sources=[], used_rag=False)

#         # ── Phase 2: enrich (fetch full text) ────────────────────────────
#         enriched = await _enrich_docs(unique_docs, client)

#     # Docs with enough text to be useful
#     reliable = [d for d in enriched if len(d.text.strip()) >= 100]
#     logger.info("Reliable docs (≥100 chars text): %d", len(reliable))

#     if len(reliable) < MIN_RELIABLE_SOURCES:
#         logger.warning("RAG: only %d reliable source(s), falling back to prompt-only", len(reliable))
#         return RAGResult(
#             context="",
#             sources=enriched,   # still return all for the UI
#             used_rag=False,
#             source_count=len(reliable),
#         )

#     # ── Phase 3: chunk ───────────────────────────────────────────────────
#     all_chunks: List[str] = []
#     for doc in reliable:
#         header = f"[{doc.source.upper()}] {doc.title}\n"
#         for chunk in _chunk_text(doc.text):
#             all_chunks.append(header + chunk)

#     if not all_chunks:
#         return RAGResult(context="", sources=enriched, used_rag=False)

#     logger.info("Total chunks to index: %d", len(all_chunks))

#     # ── Phase 4: embed + FAISS ───────────────────────────────────────────
#     try:
#         index, model, chunks = _build_faiss_index(all_chunks)
#         top_chunks = _retrieve_top_k(query, index, model, chunks, k=TOP_K_CHUNKS)
#     except Exception as e:
#         logger.error("FAISS indexing failed: %s", e)
#         # Graceful degradation: use first N chunks without FAISS
#         top_chunks = all_chunks[:TOP_K_CHUNKS]

#     # ── Phase 5: assemble context ────────────────────────────────────────
#     context = "\n\n---\n\n".join(top_chunks)
#     context = context[:MAX_CONTEXT_CHARS]   # hard cap for token budget

#     elapsed = time.time() - t0
#     logger.info(
#         "RAG pipeline complete in %.1fs — %d sources, %d chunks, %d top-k",
#         elapsed, len(enriched), len(all_chunks), len(top_chunks),
#     )

#     return RAGResult(
#         context=context,
#         sources=enriched,
#         used_rag=True,
#         chunk_count=len(all_chunks),
#         source_count=len(reliable),
#     )


# def run_rag_pipeline_sync(
#     query:          str,
#     tavily_api_key: str = "",
#     core_api_key:   str = "",
#     github_token:   str = "",
# ) -> RAGResult:
#     """Synchronous wrapper — runs the async pipeline in a new event loop."""
#     try:
#         loop = asyncio.get_event_loop()
#         if loop.is_running():
#             # Inside FastAPI's async context — use asyncio.run_coroutine_threadsafe
#             import concurrent.futures
#             with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
#                 future = pool.submit(
#                     asyncio.run,
#                     run_rag_pipeline(query, tavily_api_key, core_api_key, github_token),
#                 )
#                 return future.result(timeout=60)
#         else:
#             return loop.run_until_complete(
#                 run_rag_pipeline(query, tavily_api_key, core_api_key, github_token)
#             )
#     except Exception as e:
#         logger.error("RAG pipeline sync wrapper failed: %s", e)
#         return RAGResult(context="", sources=[], used_rag=False)