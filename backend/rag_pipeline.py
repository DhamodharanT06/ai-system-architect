"""
rag_pipeline.py  —  v5  (split async/CPU, encoder in main thread)
===================================================================

Root causes fixed from v3
--------------------------
1. 63s total: GitHub README fetches (3-6s each) + SentenceTransformer cold-start
   inside thread + low-confidence PDF re-fetch block (15-30s).
2. Sources lost on timeout: sync wrapper returned RAGResult(sources=[]) on any
   TimeoutError, discarding all links already collected during search phase.

Architecture
------------
Phase A — SEARCH   (~5-8s, concurrent)
  All 6 sources queried in parallel with 5s hard timeout each.
  Results stored in a shared _SourceStore immediately — survives timeout.

Phase B — EMBED+MMR  (~3-6s, CPU only)
  Uses abstracts/snippets already in doc.text — NO HTTP fetches.
  No README fetch, no PDF fetch, no redirects.
  SentenceTransformer loaded globally at import time (not in thread).
  STAGE1_KEEP=5 docs, TOP_K_FINAL=4 chunks, MAX_CONTEXT_CHARS=2500.

Phase C — FALLBACK
  If Phase B fails or times out, return Phase-A sources with empty context.
  Sources are ALWAYS available for Learning References.

Sync wrapper
------------
  Runs Phase A + B in a thread with 40s total budget.
  On TimeoutError: returns whatever sources Phase A already collected.
  Sources never lost regardless of what crashes.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import re
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

import httpx
import numpy as np

logger = logging.getLogger(__name__)

# ── tunables ─────────────────────────────────────────────────────────────────
SEARCH_TIMEOUT       = 5          # per-source (s) — tight, Render-safe
MAX_DOCS_PER_SOURCE  = 3          # fewer docs = faster search + less to embed
MAX_CHARS_PER_DOC    = 3_000      # abstract cap — no full-text fetch

STAGE1_KEEP          = 5          # top docs to embed after API ranking
CHUNK_SIZE           = 350
CHUNK_OVERLAP        = 40
TOP_K_FETCH          = 12
TOP_K_FINAL          = 4          # chunks sent to LLM
MMR_LAMBDA           = 0.65
MIN_RELIABLE_SOURCES = 2
MAX_CONTEXT_CHARS    = 2_500

EMBED_MODEL          = "all-MiniLM-L6-v2"

SOURCE_PRIORITY: Dict[str, int] = {
    "arxiv":            10,
    "semantic_scholar": 10,
    "core":             9,
    "crossref":         8,
    "tavily":           6,
    "github":           4,
}

LOW_CONFIDENCE_THRESHOLD = 0.35   # kept for future use, PDF fetch removed


# ════════════════════════════════════════════════════════════════════════════
# GLOBAL SINGLETONS — initialised at import time, reused forever
# ════════════════════════════════════════════════════════════════════════════

_ENCODER        = None
_ENCODER_LOCK   = threading.Lock()
_EMBED_CACHE: Dict[str, np.ndarray] = {}


def _get_encoder():
    global _ENCODER
    if _ENCODER is None:
        with _ENCODER_LOCK:
            if _ENCODER is None:
                from sentence_transformers import SentenceTransformer
                logger.info("Loading SentenceTransformer '%s'", EMBED_MODEL)
                _ENCODER = SentenceTransformer(EMBED_MODEL)
                logger.info("SentenceTransformer ready")
    return _ENCODER


def _get_faiss():
    import faiss
    return faiss


def _get_splitter():
    from langchain_text_splitters import RecursiveCharacterTextSplitter
    return RecursiveCharacterTextSplitter(
        chunk_size=CHUNK_SIZE,
        chunk_overlap=CHUNK_OVERLAP,
        separators=["\n\n", "\n", ". ", " ", ""],
        length_function=len,
        is_separator_regex=False,
    )


# ════════════════════════════════════════════════════════════════════════════
# DATA CLASSES  (schema unchanged)
# ════════════════════════════════════════════════════════════════════════════

@dataclass
class SourceDoc:
    title:     str
    url:       str
    source:    str
    text:      str           = ""
    pdf_url:   Optional[str] = None
    authors:   List[str]     = field(default_factory=list)
    year:      Optional[str] = None
    doi:       Optional[str] = None
    doc_type:  str           = "article"
    priority:  int           = 5
    api_score: float         = 0.0


@dataclass
class ChunkMetadata:
    source:    str
    title:     str
    url:       str
    doc_type:  str
    priority:  int
    chunk_idx: int


@dataclass
class RAGResult:
    context:      str
    sources:      List[SourceDoc]
    used_rag:     bool
    chunk_count:  int = 0
    source_count: int = 0


# ════════════════════════════════════════════════════════════════════════════
# SHARED SOURCE STORE — Phase A writes here; sync wrapper reads on timeout
# ════════════════════════════════════════════════════════════════════════════

class _SourceStore:
    """Thread-safe container written by Phase A, read by sync wrapper on timeout."""
    def __init__(self):
        self._lock    = threading.Lock()
        self._sources: List[SourceDoc] = []
        self.ready    = False

    def save(self, docs: List[SourceDoc]):
        with self._lock:
            self._sources = docs
            self.ready    = True

    def get(self) -> List[SourceDoc]:
        with self._lock:
            return list(self._sources)


# ════════════════════════════════════════════════════════════════════════════
# HELPERS
# ════════════════════════════════════════════════════════════════════════════

def _normalise_authors(raw: Any) -> List[str]:
    if not raw:
        return []
    if isinstance(raw, str):
        return [a.strip() for a in raw.split(",") if a.strip()]
    if isinstance(raw, list):
        out = []
        for item in raw:
            if isinstance(item, str):
                out.append(item.strip())
            elif isinstance(item, dict):
                name = (item.get("name")
                        or f"{item.get('given','')} {item.get('family','')}".strip())
                if name:
                    out.append(name.strip())
        return out
    return []


def _embed_cached(texts: List[str]) -> np.ndarray:
    encoder = _get_encoder()
    results, to_encode = [], []

    for i, t in enumerate(texts):
        key = hashlib.sha256(t.encode()).hexdigest()[:16]
        if key in _EMBED_CACHE:
            results.append((i, _EMBED_CACHE[key]))
        else:
            to_encode.append((i, t, key))

    if to_encode:
        embs = encoder.encode(
            [x[1] for x in to_encode],
            show_progress_bar=False, batch_size=64, normalize_embeddings=True,
        )
        for j, (orig_i, _, key) in enumerate(to_encode):
            _EMBED_CACHE[key] = embs[j]
            results.append((orig_i, embs[j]))

    results.sort(key=lambda x: x[0])
    return np.array([r[1] for r in results], dtype="float32")


# ════════════════════════════════════════════════════════════════════════════
# PHASE A — SEARCHERS  (abstract/snippet only, no HTTP fetches after search)
# ════════════════════════════════════════════════════════════════════════════

async def _search_arxiv(query: str, client: httpx.AsyncClient) -> List[SourceDoc]:
    try:
        r = await client.get(
            "https://export.arxiv.org/api/query",
            params={"search_query": f"all:{query}", "max_results": MAX_DOCS_PER_SOURCE,
                    "sortBy": "relevance"},
            timeout=SEARCH_TIMEOUT,
        )
        r.raise_for_status()
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(r.text, "xml")
        docs = []
        for pos, entry in enumerate(soup.find_all("entry")[:MAX_DOCS_PER_SOURCE]):
            title   = entry.find("title").get_text(strip=True)   if entry.find("title")   else ""
            link    = entry.find("id").get_text(strip=True)       if entry.find("id")      else ""
            summary = entry.find("summary").get_text(strip=True)  if entry.find("summary") else ""
            authors = _normalise_authors([
                a.find("name").get_text(strip=True)
                for a in entry.find_all("author") if a.find("name")
            ])
            year = entry.find("published").get_text(strip=True)[:4] if entry.find("published") else ""
            pdf_url = link.replace("/abs/", "/pdf/") + ".pdf" if "/abs/" in link else None
            if title and link:
                d = SourceDoc(title=title, url=link, source="arxiv",
                              pdf_url=pdf_url, authors=authors, year=year,
                              doc_type="paper", priority=SOURCE_PRIORITY["arxiv"],
                              api_score=1.0 / (pos + 1))
                d.text = summary[:MAX_CHARS_PER_DOC]
                docs.append(d)
        logger.info("arXiv: %d", len(docs))
        return docs
    except Exception as e:
        logger.warning("arXiv: %s", e)
        return []


async def _search_semantic_scholar(query: str, client: httpx.AsyncClient) -> List[SourceDoc]:
    try:
        r = await client.get(
            "https://api.semanticscholar.org/graph/v1/paper/search",
            params={"query": query, "limit": MAX_DOCS_PER_SOURCE,
                    "fields": "title,abstract,url,openAccessPdf,authors,year,citationCount"},
            timeout=SEARCH_TIMEOUT,
        )
        r.raise_for_status()
        items = r.json().get("data", [])
        max_cites = max((p.get("citationCount") or 0 for p in items), default=1) or 1
        docs = []
        for pos, p in enumerate(items[:MAX_DOCS_PER_SOURCE]):
            title    = p.get("title", "")
            link     = p.get("url") or f"https://www.semanticscholar.org/paper/{p.get('paperId','')}"
            abstract = p.get("abstract") or ""
            pdf_url  = (p.get("openAccessPdf") or {}).get("url")
            cites    = p.get("citationCount") or 0
            if title:
                d = SourceDoc(title=title, url=link, source="semantic_scholar",
                              pdf_url=pdf_url,
                              authors=_normalise_authors(p.get("authors")),
                              year=str(p.get("year", "")) if p.get("year") else "",
                              doc_type="paper", priority=SOURCE_PRIORITY["semantic_scholar"],
                              api_score=0.5*(1.0/(pos+1)) + 0.5*(cites/max_cites))
                d.text = abstract[:MAX_CHARS_PER_DOC]
                docs.append(d)
        logger.info("S2: %d", len(docs))
        return docs
    except Exception as e:
        logger.warning("S2: %s", e)
        return []


async def _search_crossref(query: str, client: httpx.AsyncClient) -> List[SourceDoc]:
    try:
        r = await client.get(
            "https://api.crossref.org/works",
            params={"query": query, "rows": MAX_DOCS_PER_SOURCE,
                    "select": "title,URL,abstract,author,published,DOI"},
            timeout=SEARCH_TIMEOUT,
        )
        r.raise_for_status()
        docs = []
        for pos, item in enumerate(r.json().get("message", {}).get("items", [])[:MAX_DOCS_PER_SOURCE]):
            titles   = item.get("title", [])
            title    = titles[0] if titles else ""
            link     = item.get("URL", "")
            abstract = re.sub(r"<[^>]+>", " ", item.get("abstract") or "").strip()
            pub      = item.get("published", {}).get("date-parts", [[]])
            year     = str(pub[0][0]) if pub and pub[0] else ""
            authors  = _normalise_authors([
                f"{a.get('given','')} {a.get('family','')}".strip()
                for a in (item.get("author") or [])
            ])
            if title and link:
                d = SourceDoc(title=title, url=link, source="crossref",
                              doi=item.get("DOI",""), authors=authors, year=year,
                              doc_type="paper", priority=SOURCE_PRIORITY["crossref"],
                              api_score=1.0/(pos+1))
                d.text = abstract[:MAX_CHARS_PER_DOC]
                docs.append(d)
        logger.info("CrossRef: %d", len(docs))
        return docs
    except Exception as e:
        logger.warning("CrossRef: %s", e)
        return []


async def _search_core(query: str, client: httpx.AsyncClient, api_key: str = "") -> List[SourceDoc]:
    try:
        headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        r = await client.get(
            "https://api.core.ac.uk/v3/search/works",
            params={"q": query, "limit": MAX_DOCS_PER_SOURCE},
            headers=headers, timeout=SEARCH_TIMEOUT,
        )
        if r.status_code == 401:
            return []
        r.raise_for_status()
        docs = []
        for pos, item in enumerate(r.json().get("results", [])[:MAX_DOCS_PER_SOURCE]):
            title    = item.get("title", "")
            link     = (item.get("sourceFulltextUrls") or [None])[0] or item.get("downloadUrl") or ""
            abstract = item.get("abstract") or ""
            year     = str(item.get("yearPublished","")) if item.get("yearPublished") else ""
            if title and link:
                d = SourceDoc(title=title, url=link, source="core",
                              doi=item.get("doi",""),
                              authors=_normalise_authors(item.get("authors")),
                              year=year, doc_type="paper",
                              priority=SOURCE_PRIORITY["core"],
                              api_score=1.0/(pos+1))
                d.text = abstract[:MAX_CHARS_PER_DOC]
                docs.append(d)
        logger.info("CORE: %d", len(docs))
        return docs
    except Exception as e:
        logger.warning("CORE: %s", e)
        return []


async def _search_tavily(query: str, client: httpx.AsyncClient, api_key: str = "") -> List[SourceDoc]:
    if not api_key:
        return []
    try:
        r = await client.post(
            "https://api.tavily.com/search",
            json={"api_key": api_key, "query": query, "search_depth": "basic",
                  "max_results": MAX_DOCS_PER_SOURCE, "include_raw_content": False},
            timeout=SEARCH_TIMEOUT,
        )
        r.raise_for_status()
        docs = []
        for item in r.json().get("results", [])[:MAX_DOCS_PER_SOURCE]:
            link     = item.get("url", "")
            title    = item.get("title") or link
            snippet  = item.get("content") or ""
            tv_score = float(item.get("score") or 0.0)
            url_l    = link.lower()
            doc_type = "documentation" if any(k in url_l for k in [
                "docs.", "/docs/", "readthedocs", "developer.", "/api/", "/reference/"
            ]) else "article"
            prio = SOURCE_PRIORITY["tavily"] + (2 if doc_type == "documentation" else 0)
            if link:
                d = SourceDoc(title=title, url=link, source="tavily",
                              doc_type=doc_type, priority=prio, api_score=tv_score)
                d.text = snippet[:MAX_CHARS_PER_DOC]
                docs.append(d)
        logger.info("Tavily: %d", len(docs))
        return docs
    except Exception as e:
        logger.warning("Tavily: %s", e)
        return []


async def _search_github(query: str, client: httpx.AsyncClient, token: str = "") -> List[SourceDoc]:
    try:
        headers = {"Accept": "application/vnd.github+json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        r = await client.get(
            "https://api.github.com/search/repositories",
            params={"q": query, "sort": "stars", "per_page": MAX_DOCS_PER_SOURCE},
            headers=headers, timeout=SEARCH_TIMEOUT,
        )
        r.raise_for_status()
        items     = r.json().get("items", [])
        max_stars = max((repo.get("stargazers_count") or 0 for repo in items), default=1) or 1
        docs      = []
        for repo in items[:MAX_DOCS_PER_SOURCE]:
            name  = repo.get("full_name", "")
            link  = repo.get("html_url", "")
            desc  = repo.get("description") or ""
            stars = repo.get("stargazers_count") or 0
            lang  = repo.get("language") or ""
            if name and link:
                d = SourceDoc(
                    title=f"{name} ({'⭐'+str(stars) if stars else lang})",
                    url=link, source="github", doc_type="repo",
                    priority=SOURCE_PRIORITY["github"],
                    api_score=stars/max_stars,
                )
                # Use description as text — no README fetch
                d.text = desc[:MAX_CHARS_PER_DOC]
                docs.append(d)
        logger.info("GitHub: %d", len(docs))
        return docs
    except Exception as e:
        logger.warning("GitHub: %s", e)
        return []


# ════════════════════════════════════════════════════════════════════════════
# STAGE-1 RANKER
# ════════════════════════════════════════════════════════════════════════════

def _stage1_rank(docs: List[SourceDoc], keep: int = STAGE1_KEEP) -> List[SourceDoc]:
    def _score(d: SourceDoc) -> float:
        return 0.6 * d.api_score + 0.4 * (d.priority / 10.0)
    ranked = sorted(docs, key=_score, reverse=True)
    logger.info("Stage-1: %d → %d for embedding", len(ranked), min(keep, len(ranked)))
    return ranked[:keep]


# ════════════════════════════════════════════════════════════════════════════
# CHUNKER
# ════════════════════════════════════════════════════════════════════════════

def _chunk_doc(doc: SourceDoc) -> Tuple[List[str], List[ChunkMetadata]]:
    if not doc.text.strip():
        return [], []
    splitter   = _get_splitter()
    raw_chunks = splitter.split_text(doc.text)
    author_str = ", ".join(str(a) for a in doc.authors[:2]) if doc.authors else ""
    header     = f"[{doc.source.upper()}] {doc.title}"
    if doc.year:
        header += f" ({doc.year})"
    if author_str:
        header += f" — {author_str}"
    header += "\n"

    texts, metas = [], []
    for idx, chunk in enumerate(raw_chunks):
        if len(chunk.strip()) < 40:
            continue
        texts.append(header + chunk.strip())
        metas.append(ChunkMetadata(
            source=doc.source, title=doc.title, url=doc.url,
            doc_type=doc.doc_type, priority=doc.priority, chunk_idx=idx,
        ))
    return texts, metas


# ════════════════════════════════════════════════════════════════════════════
# EMBED + FAISS
# ════════════════════════════════════════════════════════════════════════════

def _build_index(chunks: List[str]) -> Tuple[Any, np.ndarray]:
    faiss_mod  = _get_faiss()
    embs       = _embed_cached(chunks)
    index      = faiss_mod.IndexFlatIP(embs.shape[1])
    index.add(embs)
    return index, embs


# ════════════════════════════════════════════════════════════════════════════
# MMR RETRIEVAL
# ════════════════════════════════════════════════════════════════════════════

def _mmr_retrieve(
    query:          str,
    index:          Any,
    all_embeddings: np.ndarray,
    chunks:         List[str],
    metadatas:      List[ChunkMetadata],
    fetch_k:        int   = TOP_K_FETCH,
    final_k:        int   = TOP_K_FINAL,
    lambda_:        float = MMR_LAMBDA,
) -> Tuple[List[str], List[ChunkMetadata], float]:
    encoder = _get_encoder()
    q_emb   = encoder.encode([query], show_progress_bar=False, normalize_embeddings=True)
    q_emb   = np.array(q_emb, dtype="float32")

    fetch_k = min(fetch_k, len(chunks))
    scores, candidate_indices = index.search(q_emb, fetch_k)
    candidate_indices = candidate_indices[0]
    best_score = float(scores[0][0]) if scores.size else 0.0

    cand_embs  = all_embeddings[candidate_indices]
    relevance  = (cand_embs @ q_emb.T).squeeze()
    prio_bonus = np.array(
        [metadatas[i].priority / 100.0 for i in candidate_indices], dtype="float32"
    )
    relevance  = relevance + prio_bonus

    selected: List[int] = []
    for _ in range(min(final_k, fetch_k)):
        if not selected:
            best = int(np.argmax(relevance))
        else:
            sel_embs = cand_embs[selected]
            sim      = (cand_embs @ sel_embs.T).max(axis=1)
            mmr      = lambda_ * relevance - (1 - lambda_) * sim
            for s in selected:
                mmr[s] = -np.inf
            best = int(np.argmax(mmr))
        selected.append(best)

    result_chunks, result_metas = [], []
    for li in selected:
        gi = candidate_indices[li]
        result_chunks.append(chunks[gi])
        result_metas.append(metadatas[gi])

    paired = sorted(
        zip(result_chunks, result_metas),
        key=lambda x: (-x[1].priority, x[1].doc_type != "paper"),
    )
    if paired:
        rc, rm = zip(*paired)
        return list(rc), list(rm), best_score
    return [], [], best_score


# ════════════════════════════════════════════════════════════════════════════
# PHASE A — async search  (runs inside ThreadPoolExecutor)
# ════════════════════════════════════════════════════════════════════════════

async def _search_all(
    query:          str,
    store:          _SourceStore,
    tavily_api_key: str = "",
    core_api_key:   str = "",
    github_token:   str = "",
) -> List[SourceDoc]:
    """
    Fire all 6 searches with a shared wall-clock deadline.
    Any source that does not respond in time is cancelled — we never
    wait for the slowest one.  Results saved to store immediately.
    """
    async with httpx.AsyncClient(
        headers={"User-Agent": "AISystemArchitect/5.0"},
        follow_redirects=False,
    ) as client:
        tasks = [
            asyncio.ensure_future(_search_arxiv(query, client)),
            asyncio.ensure_future(_search_semantic_scholar(query, client)),
            asyncio.ensure_future(_search_crossref(query, client)),
            asyncio.ensure_future(_search_core(query, client, core_api_key)),
            asyncio.ensure_future(_search_tavily(query, client, tavily_api_key)),
            asyncio.ensure_future(_search_github(query, client, github_token)),
        ]
        done, pending = await asyncio.wait(tasks, timeout=SEARCH_TIMEOUT + 1)
        for p in pending:
            p.cancel()

    all_docs: List[SourceDoc] = []
    seen: set = set()
    for fut in done:
        try:
            result = fut.result()
            if isinstance(result, list):
                for doc in result:
                    if doc.url and doc.url not in seen:
                        seen.add(doc.url)
                        all_docs.append(doc)
        except Exception:
            pass

    store.save(all_docs)
    return all_docs


# ════════════════════════════════════════════════════════════════════════════
# PHASE B — embed + MMR  (runs in MAIN THREAD — encoder singleton is warm)
# ════════════════════════════════════════════════════════════════════════════

def _embed_and_retrieve(query: str, all_docs: List[SourceDoc]) -> Tuple[str, int, int]:
    """
    Synchronous CPU phase — always called from main thread where _ENCODER
    is already loaded.  Returns (context_str, chunk_count, source_count).
    """
    stage1   = _stage1_rank(all_docs)
    reliable = [d for d in stage1 if len(d.text.strip()) >= 60]
    logger.info("Reliable docs for embed: %d", len(reliable))

    if len(reliable) < MIN_RELIABLE_SOURCES:
        return "", 0, len(reliable)

    reliable.sort(key=lambda d: -d.priority)

    all_chunks:    List[str]           = []
    all_metadatas: List[ChunkMetadata] = []
    for doc in reliable:
        c, m = _chunk_doc(doc)
        all_chunks.extend(c)
        all_metadatas.extend(m)

    if not all_chunks:
        return "", 0, len(reliable)

    logger.info("Chunks to embed: %d", len(all_chunks))

    try:
        index, embeddings = _build_index(all_chunks)
        top_chunks, _, best_score = _mmr_retrieve(
            query=query, index=index, all_embeddings=embeddings,
            chunks=all_chunks, metadatas=all_metadatas,
        )
        logger.info("MMR score: %.3f  selected: %d", best_score, len(top_chunks))
    except Exception as e:
        logger.error("Embed/MMR failed: %s", e)
        top_chunks = all_chunks[:TOP_K_FINAL]

    context = "\n\n---\n\n".join(top_chunks)[:MAX_CONTEXT_CHARS]
    return context, len(all_chunks), len(reliable)


# ════════════════════════════════════════════════════════════════════════════
# SYNC WRAPPER  v5
# ────────────────────────────────────────────────────────────────────────────
# Architecture:
#   1. Phase A (async search) runs in a ThreadPoolExecutor thread — 6s budget.
#      Results saved to _SourceStore immediately.
#   2. Phase B (embed+MMR) runs in the CALLING (main FastAPI) thread so the
#      global _ENCODER singleton is already warm — no reload, ~2-4s.
#   3. On any timeout/error: return Phase-A sources so Learning References
#      always has links.
# ════════════════════════════════════════════════════════════════════════════

def run_rag_pipeline_sync(
    query:          str,
    tavily_api_key: str = "",
    core_api_key:   str = "",
    github_token:   str = "",
) -> RAGResult:
    import concurrent.futures

    t0    = time.time()
    store = _SourceStore()

    # ── Warm the encoder NOW in the main thread before spawning thread ────
    # This ensures _ENCODER is populated in this process before Phase B.
    try:
        _get_encoder()
    except Exception as e:
        logger.warning("Encoder pre-warm failed: %s", e)

    # ── Phase A: async search in a thread (6s budget) ─────────────────────
    def _run_search() -> List[SourceDoc]:
        return asyncio.run(
            _search_all(
                query=query, store=store,
                tavily_api_key=tavily_api_key,
                core_api_key=core_api_key,
                github_token=github_token,
            )
        )

    all_docs: List[SourceDoc] = []
    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            all_docs = pool.submit(_run_search).result(timeout=10)
        logger.info("Phase A: %d docs in %.1fs", len(all_docs), time.time()-t0)
    except concurrent.futures.TimeoutError:
        all_docs = store.get()
        logger.warning("Phase A timed out — got %d sources from store", len(all_docs))
    except Exception as e:
        all_docs = store.get()
        logger.error("Phase A failed: %s — got %d sources", e, len(all_docs))

    if not all_docs:
        return RAGResult(context="", sources=[], used_rag=False)

    # ── Phase B: embed+MMR in main thread (encoder already warm) ──────────
    context      = ""
    chunk_count  = 0
    source_count = 0
    used_rag     = False

    try:
        context, chunk_count, source_count = _embed_and_retrieve(query, all_docs)
        used_rag = bool(context)
        logger.info("Phase B done in %.1fs total — used_rag=%s", time.time()-t0, used_rag)
    except Exception as e:
        logger.error("Phase B failed: %s", e, exc_info=True)

    return RAGResult(
        context=context,
        sources=all_docs,
        used_rag=used_rag,
        chunk_count=chunk_count,
        source_count=source_count,
    )


# ------------------------------------------------------------------------

# """
# rag_pipeline.py  —  v4  (latency-first, sources-always)
# =========================================================

# Root causes fixed from v3
# --------------------------
# 1. 63s total: GitHub README fetches (3-6s each) + SentenceTransformer cold-start
#    inside thread + low-confidence PDF re-fetch block (15-30s).
# 2. Sources lost on timeout: sync wrapper returned RAGResult(sources=[]) on any
#    TimeoutError, discarding all links already collected during search phase.

# Architecture
# ------------
# Phase A — SEARCH   (~5-8s, concurrent)
#   All 6 sources queried in parallel with 5s hard timeout each.
#   Results stored in a shared _SourceStore immediately — survives timeout.

# Phase B — EMBED+MMR  (~3-6s, CPU only)
#   Uses abstracts/snippets already in doc.text — NO HTTP fetches.
#   No README fetch, no PDF fetch, no redirects.
#   SentenceTransformer loaded globally at import time (not in thread).
#   STAGE1_KEEP=5 docs, TOP_K_FINAL=4 chunks, MAX_CONTEXT_CHARS=2500.

# Phase C — FALLBACK
#   If Phase B fails or times out, return Phase-A sources with empty context.
#   Sources are ALWAYS available for Learning References.

# Sync wrapper
# ------------
#   Runs Phase A + B in a thread with 40s total budget.
#   On TimeoutError: returns whatever sources Phase A already collected.
#   Sources never lost regardless of what crashes.
# """

# from __future__ import annotations

# import asyncio
# import hashlib
# import logging
# import re
# import threading
# import time
# from dataclasses import dataclass, field
# from typing import Any, Dict, List, Optional, Tuple

# import httpx
# import numpy as np

# logger = logging.getLogger(__name__)

# # ── tunables ─────────────────────────────────────────────────────────────────
# SEARCH_TIMEOUT       = 5          # per-source (s) — tight, Render-safe
# MAX_DOCS_PER_SOURCE  = 3          # fewer docs = faster search + less to embed
# MAX_CHARS_PER_DOC    = 3_000      # abstract cap — no full-text fetch

# STAGE1_KEEP          = 5          # top docs to embed after API ranking
# CHUNK_SIZE           = 350
# CHUNK_OVERLAP        = 40
# TOP_K_FETCH          = 12
# TOP_K_FINAL          = 4          # chunks sent to LLM
# MMR_LAMBDA           = 0.65
# MIN_RELIABLE_SOURCES = 2
# MAX_CONTEXT_CHARS    = 2_500

# EMBED_MODEL          = "all-MiniLM-L6-v2"

# SOURCE_PRIORITY: Dict[str, int] = {
#     "arxiv":            10,
#     "semantic_scholar": 10,
#     "core":             9,
#     "crossref":         8,
#     "tavily":           6,
#     "github":           4,
# }

# LOW_CONFIDENCE_THRESHOLD = 0.35   # kept for future use, PDF fetch removed


# # ════════════════════════════════════════════════════════════════════════════
# # GLOBAL SINGLETONS — initialised at import time, reused forever
# # ════════════════════════════════════════════════════════════════════════════

# _ENCODER        = None
# _ENCODER_LOCK   = threading.Lock()
# _EMBED_CACHE: Dict[str, np.ndarray] = {}


# def _get_encoder():
#     global _ENCODER
#     if _ENCODER is None:
#         with _ENCODER_LOCK:
#             if _ENCODER is None:
#                 from sentence_transformers import SentenceTransformer
#                 logger.info("Loading SentenceTransformer '%s'", EMBED_MODEL)
#                 _ENCODER = SentenceTransformer(EMBED_MODEL)
#                 logger.info("SentenceTransformer ready")
#     return _ENCODER


# def _get_faiss():
#     import faiss
#     return faiss


# def _get_splitter():
#     from langchain_text_splitters import RecursiveCharacterTextSplitter
#     return RecursiveCharacterTextSplitter(
#         chunk_size=CHUNK_SIZE,
#         chunk_overlap=CHUNK_OVERLAP,
#         separators=["\n\n", "\n", ". ", " ", ""],
#         length_function=len,
#         is_separator_regex=False,
#     )


# # ════════════════════════════════════════════════════════════════════════════
# # DATA CLASSES  (schema unchanged)
# # ════════════════════════════════════════════════════════════════════════════

# @dataclass
# class SourceDoc:
#     title:     str
#     url:       str
#     source:    str
#     text:      str           = ""
#     pdf_url:   Optional[str] = None
#     authors:   List[str]     = field(default_factory=list)
#     year:      Optional[str] = None
#     doi:       Optional[str] = None
#     doc_type:  str           = "article"
#     priority:  int           = 5
#     api_score: float         = 0.0


# @dataclass
# class ChunkMetadata:
#     source:    str
#     title:     str
#     url:       str
#     doc_type:  str
#     priority:  int
#     chunk_idx: int


# @dataclass
# class RAGResult:
#     context:      str
#     sources:      List[SourceDoc]
#     used_rag:     bool
#     chunk_count:  int = 0
#     source_count: int = 0


# # ════════════════════════════════════════════════════════════════════════════
# # SHARED SOURCE STORE — Phase A writes here; sync wrapper reads on timeout
# # ════════════════════════════════════════════════════════════════════════════

# class _SourceStore:
#     """Thread-safe container written by Phase A, read by sync wrapper on timeout."""
#     def __init__(self):
#         self._lock    = threading.Lock()
#         self._sources: List[SourceDoc] = []
#         self.ready    = False

#     def save(self, docs: List[SourceDoc]):
#         with self._lock:
#             self._sources = docs
#             self.ready    = True

#     def get(self) -> List[SourceDoc]:
#         with self._lock:
#             return list(self._sources)


# # ════════════════════════════════════════════════════════════════════════════
# # HELPERS
# # ════════════════════════════════════════════════════════════════════════════

# def _normalise_authors(raw: Any) -> List[str]:
#     if not raw:
#         return []
#     if isinstance(raw, str):
#         return [a.strip() for a in raw.split(",") if a.strip()]
#     if isinstance(raw, list):
#         out = []
#         for item in raw:
#             if isinstance(item, str):
#                 out.append(item.strip())
#             elif isinstance(item, dict):
#                 name = (item.get("name")
#                         or f"{item.get('given','')} {item.get('family','')}".strip())
#                 if name:
#                     out.append(name.strip())
#         return out
#     return []


# def _embed_cached(texts: List[str]) -> np.ndarray:
#     encoder = _get_encoder()
#     results, to_encode = [], []

#     for i, t in enumerate(texts):
#         key = hashlib.sha256(t.encode()).hexdigest()[:16]
#         if key in _EMBED_CACHE:
#             results.append((i, _EMBED_CACHE[key]))
#         else:
#             to_encode.append((i, t, key))

#     if to_encode:
#         embs = encoder.encode(
#             [x[1] for x in to_encode],
#             show_progress_bar=False, batch_size=64, normalize_embeddings=True,
#         )
#         for j, (orig_i, _, key) in enumerate(to_encode):
#             _EMBED_CACHE[key] = embs[j]
#             results.append((orig_i, embs[j]))

#     results.sort(key=lambda x: x[0])
#     return np.array([r[1] for r in results], dtype="float32")


# # ════════════════════════════════════════════════════════════════════════════
# # PHASE A — SEARCHERS  (abstract/snippet only, no HTTP fetches after search)
# # ════════════════════════════════════════════════════════════════════════════

# async def _search_arxiv(query: str, client: httpx.AsyncClient) -> List[SourceDoc]:
#     try:
#         r = await client.get(
#             "https://export.arxiv.org/api/query",
#             params={"search_query": f"all:{query}", "max_results": MAX_DOCS_PER_SOURCE,
#                     "sortBy": "relevance"},
#             timeout=SEARCH_TIMEOUT,
#         )
#         r.raise_for_status()
#         from bs4 import BeautifulSoup
#         soup = BeautifulSoup(r.text, "xml")
#         docs = []
#         for pos, entry in enumerate(soup.find_all("entry")[:MAX_DOCS_PER_SOURCE]):
#             title   = entry.find("title").get_text(strip=True)   if entry.find("title")   else ""
#             link    = entry.find("id").get_text(strip=True)       if entry.find("id")      else ""
#             summary = entry.find("summary").get_text(strip=True)  if entry.find("summary") else ""
#             authors = _normalise_authors([
#                 a.find("name").get_text(strip=True)
#                 for a in entry.find_all("author") if a.find("name")
#             ])
#             year = entry.find("published").get_text(strip=True)[:4] if entry.find("published") else ""
#             pdf_url = link.replace("/abs/", "/pdf/") + ".pdf" if "/abs/" in link else None
#             if title and link:
#                 d = SourceDoc(title=title, url=link, source="arxiv",
#                               pdf_url=pdf_url, authors=authors, year=year,
#                               doc_type="paper", priority=SOURCE_PRIORITY["arxiv"],
#                               api_score=1.0 / (pos + 1))
#                 d.text = summary[:MAX_CHARS_PER_DOC]
#                 docs.append(d)
#         logger.info("arXiv: %d", len(docs))
#         return docs
#     except Exception as e:
#         logger.warning("arXiv: %s", e)
#         return []


# async def _search_semantic_scholar(query: str, client: httpx.AsyncClient) -> List[SourceDoc]:
#     try:
#         r = await client.get(
#             "https://api.semanticscholar.org/graph/v1/paper/search",
#             params={"query": query, "limit": MAX_DOCS_PER_SOURCE,
#                     "fields": "title,abstract,url,openAccessPdf,authors,year,citationCount"},
#             timeout=SEARCH_TIMEOUT,
#         )
#         r.raise_for_status()
#         items = r.json().get("data", [])
#         max_cites = max((p.get("citationCount") or 0 for p in items), default=1) or 1
#         docs = []
#         for pos, p in enumerate(items[:MAX_DOCS_PER_SOURCE]):
#             title    = p.get("title", "")
#             link     = p.get("url") or f"https://www.semanticscholar.org/paper/{p.get('paperId','')}"
#             abstract = p.get("abstract") or ""
#             pdf_url  = (p.get("openAccessPdf") or {}).get("url")
#             cites    = p.get("citationCount") or 0
#             if title:
#                 d = SourceDoc(title=title, url=link, source="semantic_scholar",
#                               pdf_url=pdf_url,
#                               authors=_normalise_authors(p.get("authors")),
#                               year=str(p.get("year", "")) if p.get("year") else "",
#                               doc_type="paper", priority=SOURCE_PRIORITY["semantic_scholar"],
#                               api_score=0.5*(1.0/(pos+1)) + 0.5*(cites/max_cites))
#                 d.text = abstract[:MAX_CHARS_PER_DOC]
#                 docs.append(d)
#         logger.info("S2: %d", len(docs))
#         return docs
#     except Exception as e:
#         logger.warning("S2: %s", e)
#         return []


# async def _search_crossref(query: str, client: httpx.AsyncClient) -> List[SourceDoc]:
#     try:
#         r = await client.get(
#             "https://api.crossref.org/works",
#             params={"query": query, "rows": MAX_DOCS_PER_SOURCE,
#                     "select": "title,URL,abstract,author,published,DOI"},
#             timeout=SEARCH_TIMEOUT,
#         )
#         r.raise_for_status()
#         docs = []
#         for pos, item in enumerate(r.json().get("message", {}).get("items", [])[:MAX_DOCS_PER_SOURCE]):
#             titles   = item.get("title", [])
#             title    = titles[0] if titles else ""
#             link     = item.get("URL", "")
#             abstract = re.sub(r"<[^>]+>", " ", item.get("abstract") or "").strip()
#             pub      = item.get("published", {}).get("date-parts", [[]])
#             year     = str(pub[0][0]) if pub and pub[0] else ""
#             authors  = _normalise_authors([
#                 f"{a.get('given','')} {a.get('family','')}".strip()
#                 for a in (item.get("author") or [])
#             ])
#             if title and link:
#                 d = SourceDoc(title=title, url=link, source="crossref",
#                               doi=item.get("DOI",""), authors=authors, year=year,
#                               doc_type="paper", priority=SOURCE_PRIORITY["crossref"],
#                               api_score=1.0/(pos+1))
#                 d.text = abstract[:MAX_CHARS_PER_DOC]
#                 docs.append(d)
#         logger.info("CrossRef: %d", len(docs))
#         return docs
#     except Exception as e:
#         logger.warning("CrossRef: %s", e)
#         return []


# async def _search_core(query: str, client: httpx.AsyncClient, api_key: str = "") -> List[SourceDoc]:
#     try:
#         headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
#         r = await client.get(
#             "https://api.core.ac.uk/v3/search/works",
#             params={"q": query, "limit": MAX_DOCS_PER_SOURCE},
#             headers=headers, timeout=SEARCH_TIMEOUT,
#         )
#         if r.status_code == 401:
#             return []
#         r.raise_for_status()
#         docs = []
#         for pos, item in enumerate(r.json().get("results", [])[:MAX_DOCS_PER_SOURCE]):
#             title    = item.get("title", "")
#             link     = (item.get("sourceFulltextUrls") or [None])[0] or item.get("downloadUrl") or ""
#             abstract = item.get("abstract") or ""
#             year     = str(item.get("yearPublished","")) if item.get("yearPublished") else ""
#             if title and link:
#                 d = SourceDoc(title=title, url=link, source="core",
#                               doi=item.get("doi",""),
#                               authors=_normalise_authors(item.get("authors")),
#                               year=year, doc_type="paper",
#                               priority=SOURCE_PRIORITY["core"],
#                               api_score=1.0/(pos+1))
#                 d.text = abstract[:MAX_CHARS_PER_DOC]
#                 docs.append(d)
#         logger.info("CORE: %d", len(docs))
#         return docs
#     except Exception as e:
#         logger.warning("CORE: %s", e)
#         return []


# async def _search_tavily(query: str, client: httpx.AsyncClient, api_key: str = "") -> List[SourceDoc]:
#     if not api_key:
#         return []
#     try:
#         r = await client.post(
#             "https://api.tavily.com/search",
#             json={"api_key": api_key, "query": query, "search_depth": "basic",
#                   "max_results": MAX_DOCS_PER_SOURCE, "include_raw_content": False},
#             timeout=SEARCH_TIMEOUT,
#         )
#         r.raise_for_status()
#         docs = []
#         for item in r.json().get("results", [])[:MAX_DOCS_PER_SOURCE]:
#             link     = item.get("url", "")
#             title    = item.get("title") or link
#             snippet  = item.get("content") or ""
#             tv_score = float(item.get("score") or 0.0)
#             url_l    = link.lower()
#             doc_type = "documentation" if any(k in url_l for k in [
#                 "docs.", "/docs/", "readthedocs", "developer.", "/api/", "/reference/"
#             ]) else "article"
#             prio = SOURCE_PRIORITY["tavily"] + (2 if doc_type == "documentation" else 0)
#             if link:
#                 d = SourceDoc(title=title, url=link, source="tavily",
#                               doc_type=doc_type, priority=prio, api_score=tv_score)
#                 d.text = snippet[:MAX_CHARS_PER_DOC]
#                 docs.append(d)
#         logger.info("Tavily: %d", len(docs))
#         return docs
#     except Exception as e:
#         logger.warning("Tavily: %s", e)
#         return []


# async def _search_github(query: str, client: httpx.AsyncClient, token: str = "") -> List[SourceDoc]:
#     try:
#         headers = {"Accept": "application/vnd.github+json"}
#         if token:
#             headers["Authorization"] = f"Bearer {token}"
#         r = await client.get(
#             "https://api.github.com/search/repositories",
#             params={"q": query, "sort": "stars", "per_page": MAX_DOCS_PER_SOURCE},
#             headers=headers, timeout=SEARCH_TIMEOUT,
#         )
#         r.raise_for_status()
#         items     = r.json().get("items", [])
#         max_stars = max((repo.get("stargazers_count") or 0 for repo in items), default=1) or 1
#         docs      = []
#         for repo in items[:MAX_DOCS_PER_SOURCE]:
#             name  = repo.get("full_name", "")
#             link  = repo.get("html_url", "")
#             desc  = repo.get("description") or ""
#             stars = repo.get("stargazers_count") or 0
#             lang  = repo.get("language") or ""
#             if name and link:
#                 d = SourceDoc(
#                     title=f"{name} ({'⭐'+str(stars) if stars else lang})",
#                     url=link, source="github", doc_type="repo",
#                     priority=SOURCE_PRIORITY["github"],
#                     api_score=stars/max_stars,
#                 )
#                 # Use description as text — no README fetch
#                 d.text = desc[:MAX_CHARS_PER_DOC]
#                 docs.append(d)
#         logger.info("GitHub: %d", len(docs))
#         return docs
#     except Exception as e:
#         logger.warning("GitHub: %s", e)
#         return []


# # ════════════════════════════════════════════════════════════════════════════
# # STAGE-1 RANKER
# # ════════════════════════════════════════════════════════════════════════════

# def _stage1_rank(docs: List[SourceDoc], keep: int = STAGE1_KEEP) -> List[SourceDoc]:
#     def _score(d: SourceDoc) -> float:
#         return 0.6 * d.api_score + 0.4 * (d.priority / 10.0)
#     ranked = sorted(docs, key=_score, reverse=True)
#     logger.info("Stage-1: %d → %d for embedding", len(ranked), min(keep, len(ranked)))
#     return ranked[:keep]


# # ════════════════════════════════════════════════════════════════════════════
# # CHUNKER
# # ════════════════════════════════════════════════════════════════════════════

# def _chunk_doc(doc: SourceDoc) -> Tuple[List[str], List[ChunkMetadata]]:
#     if not doc.text.strip():
#         return [], []
#     splitter   = _get_splitter()
#     raw_chunks = splitter.split_text(doc.text)
#     author_str = ", ".join(str(a) for a in doc.authors[:2]) if doc.authors else ""
#     header     = f"[{doc.source.upper()}] {doc.title}"
#     if doc.year:
#         header += f" ({doc.year})"
#     if author_str:
#         header += f" — {author_str}"
#     header += "\n"

#     texts, metas = [], []
#     for idx, chunk in enumerate(raw_chunks):
#         if len(chunk.strip()) < 40:
#             continue
#         texts.append(header + chunk.strip())
#         metas.append(ChunkMetadata(
#             source=doc.source, title=doc.title, url=doc.url,
#             doc_type=doc.doc_type, priority=doc.priority, chunk_idx=idx,
#         ))
#     return texts, metas


# # ════════════════════════════════════════════════════════════════════════════
# # EMBED + FAISS
# # ════════════════════════════════════════════════════════════════════════════

# def _build_index(chunks: List[str]) -> Tuple[Any, np.ndarray]:
#     faiss_mod  = _get_faiss()
#     embs       = _embed_cached(chunks)
#     index      = faiss_mod.IndexFlatIP(embs.shape[1])
#     index.add(embs)
#     return index, embs


# # ════════════════════════════════════════════════════════════════════════════
# # MMR RETRIEVAL
# # ════════════════════════════════════════════════════════════════════════════

# def _mmr_retrieve(
#     query:          str,
#     index:          Any,
#     all_embeddings: np.ndarray,
#     chunks:         List[str],
#     metadatas:      List[ChunkMetadata],
#     fetch_k:        int   = TOP_K_FETCH,
#     final_k:        int   = TOP_K_FINAL,
#     lambda_:        float = MMR_LAMBDA,
# ) -> Tuple[List[str], List[ChunkMetadata], float]:
#     encoder = _get_encoder()
#     q_emb   = encoder.encode([query], show_progress_bar=False, normalize_embeddings=True)
#     q_emb   = np.array(q_emb, dtype="float32")

#     fetch_k = min(fetch_k, len(chunks))
#     scores, candidate_indices = index.search(q_emb, fetch_k)
#     candidate_indices = candidate_indices[0]
#     best_score = float(scores[0][0]) if scores.size else 0.0

#     cand_embs  = all_embeddings[candidate_indices]
#     relevance  = (cand_embs @ q_emb.T).squeeze()
#     prio_bonus = np.array(
#         [metadatas[i].priority / 100.0 for i in candidate_indices], dtype="float32"
#     )
#     relevance  = relevance + prio_bonus

#     selected: List[int] = []
#     for _ in range(min(final_k, fetch_k)):
#         if not selected:
#             best = int(np.argmax(relevance))
#         else:
#             sel_embs = cand_embs[selected]
#             sim      = (cand_embs @ sel_embs.T).max(axis=1)
#             mmr      = lambda_ * relevance - (1 - lambda_) * sim
#             for s in selected:
#                 mmr[s] = -np.inf
#             best = int(np.argmax(mmr))
#         selected.append(best)

#     result_chunks, result_metas = [], []
#     for li in selected:
#         gi = candidate_indices[li]
#         result_chunks.append(chunks[gi])
#         result_metas.append(metadatas[gi])

#     paired = sorted(
#         zip(result_chunks, result_metas),
#         key=lambda x: (-x[1].priority, x[1].doc_type != "paper"),
#     )
#     if paired:
#         rc, rm = zip(*paired)
#         return list(rc), list(rm), best_score
#     return [], [], best_score


# # ════════════════════════════════════════════════════════════════════════════
# # MAIN ASYNC PIPELINE
# # ════════════════════════════════════════════════════════════════════════════

# async def run_rag_pipeline(
#     query:          str,
#     store:          _SourceStore,          # written immediately after search
#     tavily_api_key: str = "",
#     core_api_key:   str = "",
#     github_token:   str = "",
# ) -> RAGResult:
#     t0 = time.time()
#     logger.info("RAG v4 start: %s", query[:80])

#     # ── Phase A: search all 6 sources concurrently ───────────────────────
#     async with httpx.AsyncClient(
#         headers={"User-Agent": "AISystemArchitect/4.0"},
#         follow_redirects=False,
#     ) as client:
#         search_results = await asyncio.gather(
#             _search_arxiv(query, client),
#             _search_semantic_scholar(query, client),
#             _search_crossref(query, client),
#             _search_core(query, client, core_api_key),
#             _search_tavily(query, client, tavily_api_key),
#             _search_github(query, client, github_token),
#             return_exceptions=True,
#         )

#     all_docs: List[SourceDoc] = []
#     seen: set = set()
#     for r in search_results:
#         if isinstance(r, list):
#             for doc in r:
#                 if doc.url and doc.url not in seen:
#                     seen.add(doc.url)
#                     all_docs.append(doc)

#     logger.info("Phase A done in %.1fs — %d unique docs", time.time()-t0, len(all_docs))

#     # ── Save sources immediately so sync wrapper can expose them on timeout ─
#     store.save(all_docs)

#     if not all_docs:
#         return RAGResult(context="", sources=[], used_rag=False)

#     # ── Phase B: Stage-1 rank → chunk → embed → MMR  (CPU only, no HTTP) ─
#     stage1 = _stage1_rank(all_docs)
#     reliable = [d for d in stage1 if len(d.text.strip()) >= 60]
#     logger.info("Reliable docs: %d", len(reliable))

#     if len(reliable) < MIN_RELIABLE_SOURCES:
#         return RAGResult(context="", sources=all_docs, used_rag=False,
#                          source_count=len(reliable))

#     reliable.sort(key=lambda d: -d.priority)

#     all_chunks:    List[str]           = []
#     all_metadatas: List[ChunkMetadata] = []
#     for doc in reliable:
#         c, m = _chunk_doc(doc)
#         all_chunks.extend(c)
#         all_metadatas.extend(m)

#     if not all_chunks:
#         return RAGResult(context="", sources=all_docs, used_rag=False)

#     logger.info("Chunks: %d", len(all_chunks))

#     try:
#         index, embeddings = _build_index(all_chunks)
#         top_chunks, top_metas, best_score = _mmr_retrieve(
#             query=query, index=index, all_embeddings=embeddings,
#             chunks=all_chunks, metadatas=all_metadatas,
#         )
#         logger.info("MMR score: %.3f  chunks→LLM: %d", best_score, len(top_chunks))
#     except Exception as e:
#         logger.error("Embed/MMR failed: %s", e)
#         top_chunks = all_chunks[:TOP_K_FINAL]

#     context = "\n\n---\n\n".join(top_chunks)[:MAX_CONTEXT_CHARS]
#     elapsed = time.time() - t0
#     logger.info("RAG v4 done in %.1fs — %d sources, %d chunks→LLM",
#                 elapsed, len(all_docs), len(top_chunks))

#     return RAGResult(
#         context=context,
#         sources=all_docs,
#         used_rag=True,
#         chunk_count=len(all_chunks),
#         source_count=len(reliable),
#     )


# # ════════════════════════════════════════════════════════════════════════════
# # SYNC WRAPPER — sources always returned even on timeout
# # ════════════════════════════════════════════════════════════════════════════

# def run_rag_pipeline_sync(
#     query:          str,
#     tavily_api_key: str = "",
#     core_api_key:   str = "",
#     github_token:   str = "",
# ) -> RAGResult:
#     import concurrent.futures

#     store = _SourceStore()

#     def _run() -> RAGResult:
#         return asyncio.run(
#             run_rag_pipeline(
#                 query=query, store=store,
#                 tavily_api_key=tavily_api_key,
#                 core_api_key=core_api_key,
#                 github_token=github_token,
#             )
#         )

#     try:
#         with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
#             return pool.submit(_run).result(timeout=40)

#     except concurrent.futures.TimeoutError:
#         # Phase A likely finished — return its sources with empty context
#         saved = store.get()
#         logger.warning(
#             "RAG timed out — returning %d sources from Phase A (prompt-only context)",
#             len(saved),
#         )
#         return RAGResult(context="", sources=saved, used_rag=False)

#     except Exception as e:
#         saved = store.get()
#         logger.error("RAG failed: %s — returning %d Phase-A sources", e, len(saved), exc_info=True)
#         return RAGResult(context="", sources=saved, used_rag=False)

# ----------------------------------------------------------------

# from __future__ import annotations

# import asyncio
# import hashlib
# import io
# import logging
# import re
# import time
# from dataclasses import dataclass, field
# from functools import lru_cache
# from typing import Any, Dict, List, Optional, Tuple

# import httpx
# import numpy as np

# logger = logging.getLogger(__name__)

# # ── tunables ────────────────────────────────────────────────────────────────
# SEARCH_TIMEOUT      = 8          # per-source HTTP timeout (s)
# FETCH_TIMEOUT       = 6          # full-text / PDF fetch timeout (s)
# MAX_DOCS_PER_SOURCE = 4          # results requested per source
# MAX_CHARS_PER_DOC   = 6_000      # abstract/snippet cap before chunking

# # Stage-1 pre-ranking
# STAGE1_KEEP         = 6          # top-N docs to embed after API-level ranking

# # Stage-2 embed + MMR
# CHUNK_SIZE          = 400
# CHUNK_OVERLAP       = 50
# SEPARATORS          = ["\n\n", "\n", ". ", " ", ""]
# TOP_K_FETCH         = 15         # FAISS candidates before MMR
# TOP_K_FINAL         = 5          # chunks sent to LLM
# MMR_LAMBDA          = 0.65

# # Confidence threshold: if best FAISS score < this, fetch 1-2 PDFs for richer text
# LOW_CONFIDENCE_THRESHOLD = 0.35

# MIN_RELIABLE_SOURCES = 2
# EMBED_MODEL          = "all-MiniLM-L6-v2"
# MAX_CONTEXT_CHARS    = 3_000

# SOURCE_PRIORITY: Dict[str, int] = {
#     "arxiv":            10,
#     "semantic_scholar": 10,
#     "core":             9,
#     "crossref":         8,
#     "tavily":           6,
#     "github":           4,
# }


# # ════════════════════════════════════════════════════════════════════════════
# # GLOBAL SINGLETON — loaded once, reused across all requests
# # ════════════════════════════════════════════════════════════════════════════

# _ENCODER = None   # SentenceTransformer instance

# def _get_encoder():
#     global _ENCODER
#     if _ENCODER is None:
#         from sentence_transformers import SentenceTransformer
#         logger.info("Loading SentenceTransformer '%s' (one-time)", EMBED_MODEL)
#         _ENCODER = SentenceTransformer(EMBED_MODEL)
#         logger.info("SentenceTransformer loaded")
#     return _ENCODER

# def _get_faiss():
#     import faiss
#     return faiss

# def _get_splitter():
#     from langchain_text_splitters import RecursiveCharacterTextSplitter
#     return RecursiveCharacterTextSplitter(
#         chunk_size=CHUNK_SIZE,
#         chunk_overlap=CHUNK_OVERLAP,
#         separators=SEPARATORS,
#         length_function=len,
#         is_separator_regex=False,
#     )


# # ════════════════════════════════════════════════════════════════════════════
# # DATA CLASSES  (schema unchanged)
# # ════════════════════════════════════════════════════════════════════════════

# @dataclass
# class SourceDoc:
#     title:    str
#     url:      str
#     source:   str
#     text:     str           = ""
#     pdf_url:  Optional[str] = None
#     authors:  List[str]     = field(default_factory=list)
#     year:     Optional[str] = None
#     doi:      Optional[str] = None
#     doc_type: str           = "article"
#     priority: int           = 5
#     # Stage-1 ranking score (higher = more relevant at API level)
#     api_score: float        = 0.0


# @dataclass
# class ChunkMetadata:
#     source:    str
#     title:     str
#     url:       str
#     doc_type:  str
#     priority:  int
#     chunk_idx: int


# @dataclass
# class RAGResult:
#     context:      str
#     sources:      List[SourceDoc]
#     used_rag:     bool
#     chunk_count:  int = 0
#     source_count: int = 0


# # ════════════════════════════════════════════════════════════════════════════
# # EMBEDDING CACHE  (keyed on text hash — avoids re-embedding same abstracts)
# # ════════════════════════════════════════════════════════════════════════════

# _EMBED_CACHE: Dict[str, np.ndarray] = {}  # sha256[:16] → 1-D float32 array

# def _embed_cached(texts: List[str]) -> np.ndarray:
#     encoder = _get_encoder()
#     results = []
#     to_encode_idx  = []
#     to_encode_text = []

#     for i, t in enumerate(texts):
#         key = hashlib.sha256(t.encode()).hexdigest()[:16]
#         if key in _EMBED_CACHE:
#             results.append((i, _EMBED_CACHE[key]))
#         else:
#             to_encode_idx.append(i)
#             to_encode_text.append((i, t, key))

#     if to_encode_text:
#         raw_texts = [x[1] for x in to_encode_text]
#         embs = encoder.encode(raw_texts, show_progress_bar=False,
#                               batch_size=32, normalize_embeddings=True)
#         for j, (orig_i, _, key) in enumerate(to_encode_text):
#             _EMBED_CACHE[key] = embs[j]
#             results.append((orig_i, embs[j]))

#     results.sort(key=lambda x: x[0])
#     return np.array([r[1] for r in results], dtype="float32")


# # ════════════════════════════════════════════════════════════════════════════
# # AUTHOR NORMALISER
# # ════════════════════════════════════════════════════════════════════════════

# def _normalise_authors(raw: Any) -> List[str]:
#     if not raw:
#         return []
#     if isinstance(raw, str):
#         return [a.strip() for a in raw.split(",") if a.strip()]
#     if isinstance(raw, list):
#         result = []
#         for item in raw:
#             if isinstance(item, str):
#                 result.append(item.strip())
#             elif isinstance(item, dict):
#                 name = (
#                     item.get("name")
#                     or f"{item.get('given', '')} {item.get('family', '')}".strip()
#                 )
#                 if name:
#                     result.append(name.strip())
#         return result
#     return []


# # ════════════════════════════════════════════════════════════════════════════
# # 1.  SEARCHERS  — return SourceDoc with api_score set
# # ════════════════════════════════════════════════════════════════════════════

# async def _search_arxiv(query: str, client: httpx.AsyncClient) -> List[SourceDoc]:
#     try:
#         r = await client.get(
#             "https://export.arxiv.org/api/query",
#             params={"search_query": f"all:{query}", "max_results": MAX_DOCS_PER_SOURCE, "sortBy": "relevance"},
#             timeout=SEARCH_TIMEOUT,
#         )
#         r.raise_for_status()
#         from bs4 import BeautifulSoup
#         soup = BeautifulSoup(r.text, "xml")
#         docs = []
#         for pos, entry in enumerate(soup.find_all("entry")[:MAX_DOCS_PER_SOURCE]):
#             title   = entry.find("title").get_text(strip=True)   if entry.find("title")   else ""
#             link    = entry.find("id").get_text(strip=True)       if entry.find("id")      else ""
#             summary = entry.find("summary").get_text(strip=True)  if entry.find("summary") else ""
#             authors = _normalise_authors([a.find("name").get_text(strip=True)
#                                           for a in entry.find_all("author") if a.find("name")])
#             year    = (entry.find("published").get_text(strip=True)[:4]
#                        if entry.find("published") else "")
#             pdf_url = link.replace("/abs/", "/pdf/") + ".pdf" if "/abs/" in link else None
#             if title and link:
#                 # api_score: position-decayed relevance (first result = highest)
#                 api_score = 1.0 / (pos + 1)
#                 d = SourceDoc(title=title, url=link, source="arxiv",
#                               pdf_url=pdf_url, authors=authors, year=year,
#                               doc_type="paper", priority=SOURCE_PRIORITY["arxiv"],
#                               api_score=api_score)
#                 d.text = summary[:MAX_CHARS_PER_DOC]
#                 docs.append(d)
#         logger.info("arXiv: %d", len(docs))
#         return docs
#     except Exception as e:
#         logger.warning("arXiv failed: %s", e)
#         return []


# async def _search_semantic_scholar(query: str, client: httpx.AsyncClient) -> List[SourceDoc]:
#     try:
#         r = await client.get(
#             "https://api.semanticscholar.org/graph/v1/paper/search",
#             params={"query": query, "limit": MAX_DOCS_PER_SOURCE,
#                     "fields": "title,abstract,url,openAccessPdf,authors,year,citationCount"},
#             timeout=SEARCH_TIMEOUT,
#         )
#         r.raise_for_status()
#         docs = []
#         items = r.json().get("data", [])
#         max_cites = max((p.get("citationCount") or 0 for p in items), default=1) or 1
#         for pos, p in enumerate(items[:MAX_DOCS_PER_SOURCE]):
#             title    = p.get("title", "")
#             link     = p.get("url") or f"https://www.semanticscholar.org/paper/{p.get('paperId','')}"
#             abstract = p.get("abstract") or ""
#             pdf_url  = (p.get("openAccessPdf") or {}).get("url")
#             authors  = _normalise_authors(p.get("authors"))
#             year     = str(p.get("year", "")) if p.get("year") else ""
#             cites    = p.get("citationCount") or 0
#             # api_score = 0.5*position_score + 0.5*normalised_citations
#             api_score = 0.5 * (1.0 / (pos + 1)) + 0.5 * (cites / max_cites)
#             if title:
#                 d = SourceDoc(title=title, url=link, source="semantic_scholar",
#                               pdf_url=pdf_url, authors=authors, year=year,
#                               doc_type="paper", priority=SOURCE_PRIORITY["semantic_scholar"],
#                               api_score=api_score)
#                 d.text = abstract[:MAX_CHARS_PER_DOC]
#                 docs.append(d)
#         logger.info("Semantic Scholar: %d", len(docs))
#         return docs
#     except Exception as e:
#         logger.warning("Semantic Scholar failed: %s", e)
#         return []


# async def _search_crossref(query: str, client: httpx.AsyncClient) -> List[SourceDoc]:
#     try:
#         r = await client.get(
#             "https://api.crossref.org/works",
#             params={"query": query, "rows": MAX_DOCS_PER_SOURCE,
#                     "select": "title,URL,abstract,author,published,DOI"},
#             timeout=SEARCH_TIMEOUT,
#         )
#         r.raise_for_status()
#         docs = []
#         for pos, item in enumerate(r.json().get("message", {}).get("items", [])[:MAX_DOCS_PER_SOURCE]):
#             titles   = item.get("title", [])
#             title    = titles[0] if titles else ""
#             link     = item.get("URL", "")
#             doi      = item.get("DOI", "")
#             abstract = re.sub(r"<[^>]+>", " ", item.get("abstract") or "").strip()
#             authors  = _normalise_authors([
#                 f"{a.get('given','')} {a.get('family','')}".strip()
#                 for a in (item.get("author") or [])
#             ])
#             pub  = item.get("published", {}).get("date-parts", [[]])
#             year = str(pub[0][0]) if pub and pub[0] else ""
#             if title and link:
#                 d = SourceDoc(title=title, url=link, source="crossref",
#                               doi=doi, authors=authors, year=year,
#                               doc_type="paper", priority=SOURCE_PRIORITY["crossref"],
#                               api_score=1.0 / (pos + 1))
#                 d.text = abstract[:MAX_CHARS_PER_DOC]
#                 docs.append(d)
#         logger.info("CrossRef: %d", len(docs))
#         return docs
#     except Exception as e:
#         logger.warning("CrossRef failed: %s", e)
#         return []


# async def _search_core(query: str, client: httpx.AsyncClient, api_key: str = "") -> List[SourceDoc]:
#     try:
#         headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
#         r = await client.get(
#             "https://api.core.ac.uk/v3/search/works",
#             params={"q": query, "limit": MAX_DOCS_PER_SOURCE},
#             headers=headers, timeout=SEARCH_TIMEOUT,
#         )
#         if r.status_code == 401:
#             logger.info("CORE: no key")
#             return []
#         r.raise_for_status()
#         docs = []
#         for pos, item in enumerate(r.json().get("results", [])[:MAX_DOCS_PER_SOURCE]):
#             title    = item.get("title", "")
#             link     = (item.get("sourceFulltextUrls") or [None])[0] or item.get("downloadUrl") or ""
#             abstract = item.get("abstract") or ""
#             authors  = _normalise_authors(item.get("authors"))
#             year     = str(item.get("yearPublished", "")) if item.get("yearPublished") else ""
#             doi      = item.get("doi", "")
#             if title and link:
#                 d = SourceDoc(title=title, url=link, source="core",
#                               doi=doi, authors=authors, year=year,
#                               doc_type="paper", priority=SOURCE_PRIORITY["core"],
#                               api_score=1.0 / (pos + 1))
#                 d.text = abstract[:MAX_CHARS_PER_DOC]
#                 docs.append(d)
#         logger.info("CORE: %d", len(docs))
#         return docs
#     except Exception as e:
#         logger.warning("CORE failed: %s", e)
#         return []


# async def _search_tavily(query: str, client: httpx.AsyncClient, api_key: str = "") -> List[SourceDoc]:
#     if not api_key:
#         return []
#     try:
#         r = await client.post(
#             "https://api.tavily.com/search",
#             json={"api_key": api_key, "query": query,
#                   "search_depth": "basic", "max_results": MAX_DOCS_PER_SOURCE,
#                   "include_raw_content": False},
#             timeout=SEARCH_TIMEOUT,
#         )
#         r.raise_for_status()
#         docs = []
#         for item in r.json().get("results", [])[:MAX_DOCS_PER_SOURCE]:
#             title    = item.get("title") or item.get("url", "")
#             link     = item.get("url", "")
#             snippet  = item.get("content") or ""
#             tv_score = float(item.get("score") or 0.0)   # Tavily provides relevance score
#             url_lower = link.lower()
#             doc_type = "documentation" if any(k in url_lower for k in [
#                 "docs.", "/docs/", "readthedocs", "developer.", "/api/", "/reference/"
#             ]) else "article"
#             priority = SOURCE_PRIORITY["tavily"] + (2 if doc_type == "documentation" else 0)
#             if link:
#                 d = SourceDoc(title=title, url=link, source="tavily",
#                               doc_type=doc_type, priority=priority,
#                               api_score=tv_score)
#                 d.text = snippet[:MAX_CHARS_PER_DOC]
#                 docs.append(d)
#         logger.info("Tavily: %d", len(docs))
#         return docs
#     except Exception as e:
#         logger.warning("Tavily failed: %s", e)
#         return []


# async def _search_github(query: str, client: httpx.AsyncClient, token: str = "") -> List[SourceDoc]:
#     try:
#         headers = {"Accept": "application/vnd.github+json"}
#         if token:
#             headers["Authorization"] = f"Bearer {token}"
#         r = await client.get(
#             "https://api.github.com/search/repositories",
#             params={"q": query, "sort": "stars", "per_page": MAX_DOCS_PER_SOURCE},
#             headers=headers, timeout=SEARCH_TIMEOUT,
#         )
#         r.raise_for_status()
#         items = r.json().get("items", [])
#         max_stars = max((repo.get("stargazers_count") or 0 for repo in items), default=1) or 1
#         docs = []
#         for repo in items[:MAX_DOCS_PER_SOURCE]:
#             name     = repo.get("full_name", "")
#             link     = repo.get("html_url", "")
#             desc     = repo.get("description") or ""
#             stars    = repo.get("stargazers_count") or 0
#             lang     = repo.get("language") or ""
#             readme   = f"https://raw.githubusercontent.com/{name}/HEAD/README.md"
#             if name and link:
#                 d = SourceDoc(
#                     title=f"{name} ({'⭐' + str(stars) if stars else lang})",
#                     url=link, source="github",
#                     pdf_url=readme, doc_type="repo",
#                     priority=SOURCE_PRIORITY["github"],
#                     api_score=stars / max_stars,
#                 )
#                 d.text = desc[:MAX_CHARS_PER_DOC]
#                 docs.append(d)
#         logger.info("GitHub: %d", len(docs))
#         return docs
#     except Exception as e:
#         logger.warning("GitHub failed: %s", e)
#         return []


# # ════════════════════════════════════════════════════════════════════════════
# # 2.  STAGE-1 RANKER  — pick best STAGE1_KEEP docs before embedding
# # ════════════════════════════════════════════════════════════════════════════

# def _stage1_rank(docs: List[SourceDoc], keep: int = STAGE1_KEEP) -> List[SourceDoc]:
#     """
#     Combine api_score (0–1) and source priority into a single ranking score,
#     then return the top `keep` documents for embedding.
#     All documents are still returned to the caller for Learning References.
#     """
#     def _rank_score(doc: SourceDoc) -> float:
#         norm_priority = doc.priority / 10.0          # 0–1
#         return 0.6 * doc.api_score + 0.4 * norm_priority

#     ranked = sorted(docs, key=_rank_score, reverse=True)
#     logger.info("Stage-1: keeping %d / %d docs for embedding", min(keep, len(ranked)), len(ranked))
#     return ranked[:keep]


# # ════════════════════════════════════════════════════════════════════════════
# # 3.  SELECTIVE FETCHER  — full text only when needed
# # ════════════════════════════════════════════════════════════════════════════

# async def _fetch_readme(doc: SourceDoc, client: httpx.AsyncClient) -> str:
#     """Fetch GitHub README (no redirects, fast)."""
#     if not doc.pdf_url:
#         return doc.text
#     try:
#         r = await client.get(doc.pdf_url, timeout=FETCH_TIMEOUT, follow_redirects=False)
#         if r.status_code == 200:
#             return r.text[:MAX_CHARS_PER_DOC]
#         # Try following one redirect for raw.githubusercontent.com
#         if r.status_code in (301, 302):
#             loc = r.headers.get("location", "")
#             if loc:
#                 r2 = await client.get(loc, timeout=FETCH_TIMEOUT, follow_redirects=False)
#                 if r2.status_code == 200:
#                     return r2.text[:MAX_CHARS_PER_DOC]
#     except Exception as e:
#         logger.debug("README fetch failed for %s: %s", doc.url, e)
#     return doc.text


# async def _fetch_pdf(doc: SourceDoc, client: httpx.AsyncClient) -> str:
#     """Download and extract PDF text via PyMuPDF — only called for low-confidence docs."""
#     if not doc.pdf_url:
#         return doc.text
#     try:
#         r = await client.get(doc.pdf_url, timeout=FETCH_TIMEOUT, follow_redirects=True)
#         if r.status_code == 200 and len(r.content) > 1_000:
#             import fitz
#             pdf  = fitz.open(stream=io.BytesIO(r.content), filetype="pdf")
#             text = ""
#             for page in pdf:
#                 text += page.get_text()
#                 if len(text) >= MAX_CHARS_PER_DOC:
#                     break
#             if text.strip():
#                 return text[:MAX_CHARS_PER_DOC]
#     except Exception as e:
#         logger.debug("PDF fetch failed for %s: %s", doc.url, e)
#     return doc.text


# async def _enrich_stage1_docs(
#     docs: List[SourceDoc],
#     client: httpx.AsyncClient,
#     pdf_fetch_count: int = 0,
# ) -> List[SourceDoc]:
#     """
#     Enrich Stage-1 docs:
#       - GitHub → always fetch README (fast, plain text)
#       - Papers → fetch top `pdf_fetch_count` PDFs (only when confidence is low)
#       - Others → use abstract/snippet already in doc.text
#     """
#     async def _noop(d: SourceDoc) -> str:
#         return d.text

#     tasks = []
#     for i, doc in enumerate(docs):
#         if doc.source == "github":
#             tasks.append(_fetch_readme(doc, client))
#         elif doc.source in ("arxiv", "semantic_scholar", "core") and i < pdf_fetch_count:
#             tasks.append(_fetch_pdf(doc, client))
#         else:
#             tasks.append(_noop(doc))

#     texts = await asyncio.gather(*tasks, return_exceptions=True)
#     for doc, text in zip(docs, texts):
#         if isinstance(text, str) and text.strip():
#             doc.text = text
#     return docs


# # ════════════════════════════════════════════════════════════════════════════
# # 4.  CHUNKER
# # ════════════════════════════════════════════════════════════════════════════

# def _chunk_doc(doc: SourceDoc) -> Tuple[List[str], List[ChunkMetadata]]:
#     if not doc.text.strip():
#         return [], []
#     splitter  = _get_splitter()
#     raw_chunks = splitter.split_text(doc.text)
#     author_str = ", ".join(str(a) for a in doc.authors[:2]) if doc.authors else ""
#     header     = f"[{doc.source.upper()}] {doc.title}"
#     if doc.year:
#         header += f" ({doc.year})"
#     if author_str:
#         header += f" — {author_str}"
#     header += "\n"

#     texts, metas = [], []
#     for idx, chunk in enumerate(raw_chunks):
#         if len(chunk.strip()) < 40:
#             continue
#         texts.append(header + chunk.strip())
#         metas.append(ChunkMetadata(
#             source=doc.source, title=doc.title, url=doc.url,
#             doc_type=doc.doc_type, priority=doc.priority, chunk_idx=idx,
#         ))
#     return texts, metas


# # ════════════════════════════════════════════════════════════════════════════
# # 5.  EMBED + FAISS  (cosine via normalised inner product)
# # ════════════════════════════════════════════════════════════════════════════

# def _build_index(chunks: List[str]) -> Tuple[Any, np.ndarray]:
#     faiss_mod  = _get_faiss()
#     embs       = _embed_cached(chunks)
#     index      = faiss_mod.IndexFlatIP(embs.shape[1])
#     index.add(embs)
#     return index, embs


# # ════════════════════════════════════════════════════════════════════════════
# # 6.  MMR RETRIEVAL  +  CONFIDENCE CHECK
# # ════════════════════════════════════════════════════════════════════════════

# def _mmr_retrieve(
#     query:          str,
#     index:          Any,
#     all_embeddings: np.ndarray,
#     chunks:         List[str],
#     metadatas:      List[ChunkMetadata],
#     fetch_k:        int   = TOP_K_FETCH,
#     final_k:        int   = TOP_K_FINAL,
#     lambda_:        float = MMR_LAMBDA,
# ) -> Tuple[List[str], List[ChunkMetadata], float]:
#     """
#     Returns (selected_chunks, selected_metas, best_score).
#     best_score is used to decide whether to re-fetch PDFs.
#     """
#     encoder = _get_encoder()
#     q_emb   = encoder.encode([query], show_progress_bar=False, normalize_embeddings=True)
#     q_emb   = np.array(q_emb, dtype="float32")

#     fetch_k = min(fetch_k, len(chunks))
#     scores, candidate_indices = index.search(q_emb, fetch_k)
#     candidate_indices = candidate_indices[0]
#     best_score = float(scores[0][0]) if scores.size else 0.0

#     cand_embs  = all_embeddings[candidate_indices]
#     relevance  = (cand_embs @ q_emb.T).squeeze()
#     priority_bonus = np.array(
#         [metadatas[i].priority / 100.0 for i in candidate_indices], dtype="float32"
#     )
#     relevance = relevance + priority_bonus

#     selected: List[int] = []
#     for _ in range(min(final_k, fetch_k)):
#         if not selected:
#             best = int(np.argmax(relevance))
#         else:
#             sel_embs = cand_embs[selected]
#             sim      = (cand_embs @ sel_embs.T).max(axis=1)
#             mmr      = lambda_ * relevance - (1 - lambda_) * sim
#             for s in selected:
#                 mmr[s] = -np.inf
#             best = int(np.argmax(mmr))
#         selected.append(best)

#     result_chunks = []
#     result_metas  = []
#     for li in selected:
#         gi = candidate_indices[li]
#         result_chunks.append(chunks[gi])
#         result_metas.append(metadatas[gi])

#     # Sort: papers first, then priority desc
#     paired = sorted(
#         zip(result_chunks, result_metas),
#         key=lambda x: (-x[1].priority, x[1].doc_type != "paper"),
#     )
#     if paired:
#         rc, rm = zip(*paired)
#         return list(rc), list(rm), best_score
#     return [], [], best_score


# # ════════════════════════════════════════════════════════════════════════════
# # 7.  MAIN ASYNC PIPELINE
# # ════════════════════════════════════════════════════════════════════════════

# async def run_rag_pipeline(
#     query:          str,
#     tavily_api_key: str = "",
#     core_api_key:   str = "",
#     github_token:   str = "",
# ) -> RAGResult:
#     t0 = time.time()
#     logger.info("RAG v3 start: %s", query[:80])

#     async with httpx.AsyncClient(
#         headers={"User-Agent": "AISystemArchitect/3.0"},
#         follow_redirects=False,   # disabled globally — enabled per-call only where needed
#     ) as client:

#         # ── Phase 1: search all 6 sources concurrently ───────────────────
#         search_results = await asyncio.gather(
#             _search_arxiv(query, client),
#             _search_semantic_scholar(query, client),
#             _search_crossref(query, client),
#             _search_core(query, client, core_api_key),
#             _search_tavily(query, client, tavily_api_key),
#             _search_github(query, client, github_token),
#             return_exceptions=True,
#         )

#         all_docs: List[SourceDoc] = []
#         seen: set = set()
#         for r in search_results:
#             if isinstance(r, list):
#                 for doc in r:
#                     if doc.url and doc.url not in seen:
#                         seen.add(doc.url)
#                         all_docs.append(doc)

#         logger.info("Unique docs: %d", len(all_docs))

#         if not all_docs:
#             return RAGResult(context="", sources=[], used_rag=False)

#         # ── Phase 2: Stage-1 rank → pick top STAGE1_KEEP for embedding ───
#         stage1_docs = _stage1_rank(all_docs, keep=STAGE1_KEEP)

#         # ── Phase 3: enrich Stage-1 docs (READMEs + optional PDFs) ───────
#         # First pass: fetch READMEs only (fast)
#         enriched = await _enrich_stage1_docs(stage1_docs, client, pdf_fetch_count=0)

#     # ── Outside async block — CPU work ───────────────────────────────────

#     # Docs with enough text
#     reliable = [d for d in enriched if len(d.text.strip()) >= 80]
#     logger.info("Reliable Stage-1 docs: %d / %d", len(reliable), len(enriched))

#     if len(reliable) < MIN_RELIABLE_SOURCES:
#         logger.warning("Too few reliable docs — prompt-only fallback")
#         return RAGResult(context="", sources=all_docs, used_rag=False,
#                          source_count=len(reliable))

#     reliable.sort(key=lambda d: -d.priority)

#     # ── Phase 4: chunk ────────────────────────────────────────────────────
#     all_chunks:    List[str]           = []
#     all_metadatas: List[ChunkMetadata] = []
#     for doc in reliable:
#         chunks, metas = _chunk_doc(doc)
#         all_chunks.extend(chunks)
#         all_metadatas.extend(metas)

#     if not all_chunks:
#         return RAGResult(context="", sources=all_docs, used_rag=False)

#     logger.info("Chunks to index: %d", len(all_chunks))

#     # ── Phase 5: embed + FAISS + MMR ─────────────────────────────────────
#     try:
#         index, embeddings = _build_index(all_chunks)
#         top_chunks, top_metas, best_score = _mmr_retrieve(
#             query=query, index=index, all_embeddings=embeddings,
#             chunks=all_chunks, metadatas=all_metadatas,
#             fetch_k=TOP_K_FETCH, final_k=TOP_K_FINAL, lambda_=MMR_LAMBDA,
#         )
#         logger.info("MMR best score: %.3f (threshold=%.2f)", best_score, LOW_CONFIDENCE_THRESHOLD)

#         # ── Phase 5b: low-confidence → fetch top-2 PDFs and re-index ─────
#         if best_score < LOW_CONFIDENCE_THRESHOLD:
#             logger.info("Low confidence — fetching top-2 PDFs for richer context")
#             paper_docs = [d for d in reliable if d.source in ("arxiv", "semantic_scholar", "core")][:2]
#             if paper_docs:
#                 async with httpx.AsyncClient(
#                     headers={"User-Agent": "AISystemArchitect/3.0"},
#                     follow_redirects=True,
#                 ) as pdf_client:
#                     enriched2 = await _enrich_stage1_docs(
#                         paper_docs, pdf_client, pdf_fetch_count=2
#                     )
#                 # Re-chunk only the newly enriched docs
#                 for doc in enriched2:
#                     new_c, new_m = _chunk_doc(doc)
#                     all_chunks.extend(new_c)
#                     all_metadatas.extend(new_m)
#                 # Re-index
#                 index2, embeddings2 = _build_index(all_chunks)
#                 top_chunks, top_metas, best_score = _mmr_retrieve(
#                     query=query, index=index2, all_embeddings=embeddings2,
#                     chunks=all_chunks, metadatas=all_metadatas,
#                     fetch_k=TOP_K_FETCH, final_k=TOP_K_FINAL, lambda_=MMR_LAMBDA,
#                 )
#                 logger.info("After PDF enrich — best score: %.3f", best_score)

#     except Exception as e:
#         logger.error("Embed/MMR failed: %s — using first %d chunks", e, TOP_K_FINAL)
#         top_chunks = all_chunks[:TOP_K_FINAL]
#         top_metas  = all_metadatas[:TOP_K_FINAL]

#     # ── Phase 6: assemble context ─────────────────────────────────────────
#     context = "\n\n---\n\n".join(top_chunks)[:MAX_CONTEXT_CHARS]

#     elapsed = time.time() - t0
#     logger.info(
#         "RAG v3 done in %.1fs — %d total sources, %d stage-1, %d chunks, %d→LLM",
#         elapsed, len(all_docs), len(reliable), len(all_chunks), len(top_chunks),
#     )

#     return RAGResult(
#         context=context,
#         sources=all_docs,          # ALL discovered docs for Learning References
#         used_rag=True,
#         chunk_count=len(all_chunks),
#         source_count=len(reliable),
#     )


# # ════════════════════════════════════════════════════════════════════════════
# # 8.  SYNC WRAPPER  (25 s timeout — Render-safe)
# # ════════════════════════════════════════════════════════════════════════════

# def run_rag_pipeline_sync(
#     query:          str,
#     tavily_api_key: str = "",
#     core_api_key:   str = "",
#     github_token:   str = "",
# ) -> RAGResult:
#     import concurrent.futures

#     def _run():
#         return asyncio.run(
#             run_rag_pipeline(query, tavily_api_key, core_api_key, github_token)
#         )

#     try:
#         with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
#             return pool.submit(_run).result(timeout=25)
#     except concurrent.futures.TimeoutError:
#         logger.error("RAG timed out after 25s — prompt-only fallback")
#         return RAGResult(context="", sources=[], used_rag=False)
#     except Exception as e:
#         logger.error("RAG sync wrapper failed: %s", e, exc_info=True)
#         return RAGResult(context="", sources=[], used_rag=False)