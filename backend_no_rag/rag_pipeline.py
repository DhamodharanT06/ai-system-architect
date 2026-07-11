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
# import numpy as np

logger = logging.getLogger(__name__)

# tunables 
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


# GLOBAL SINGLETONS — initialised at import time, reused forever
_ENCODER        = None
_ENCODER_LOCK   = threading.Lock()
_EMBED_CACHE: Dict[str, np.ndarray] = {}

# NO_RAG
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

# NO_RAG
# def _get_faiss():
#     import faiss
#     return faiss

# NO_RAG
# def _get_splitter():
#     from langchain_text_splitters import RecursiveCharacterTextSplitter
#     return RecursiveCharacterTextSplitter(
#         chunk_size=CHUNK_SIZE,
#         chunk_overlap=CHUNK_OVERLAP,
#         separators=["\n\n", "\n", ". ", " ", ""],
#         length_function=len,
#         is_separator_regex=False,
#     )


# DATA CLASSES  
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


# SHARED SOURCE STORE — Phase A writes here; sync wrapper reads on timeout
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


# HELPERS
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


# PHASE A — SEARCHERS  (abstract/snippet only, no HTTP fetches after search)
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


# STAGE-1 RANKER
def _stage1_rank(docs: List[SourceDoc], keep: int = STAGE1_KEEP) -> List[SourceDoc]:
    def _score(d: SourceDoc) -> float:
        return 0.6 * d.api_score + 0.4 * (d.priority / 10.0)
    ranked = sorted(docs, key=_score, reverse=True)
    logger.info("Stage-1: %d → %d for embedding", len(ranked), min(keep, len(ranked)))
    return ranked[:keep]


# CHUNKER

# NO_RAG
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


# EMBED + FAISS

# NO_RAG
# def _build_index(chunks: List[str]) -> Tuple[Any, np.ndarray]:
#     faiss_mod  = _get_faiss()
#     embs       = _embed_cached(chunks)
#     index      = faiss_mod.IndexFlatIP(embs.shape[1])
#     index.add(embs)
#     return index, embs


# MMR RETRIEVAL

# NO_RAG
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


# PHASE A — async search  (runs inside ThreadPoolExecutor)

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


# PHASE B — embed + MMR  (runs in MAIN THREAD — encoder singleton is warm)

# NO_RAG
# def _embed_and_retrieve(query: str, all_docs: List[SourceDoc]) -> Tuple[str, int, int]:
#     """
#     Synchronous CPU phase — always called from main thread where _ENCODER
#     is already loaded.  Returns (context_str, chunk_count, source_count).
#     """
#     stage1   = _stage1_rank(all_docs)
#     reliable = [d for d in stage1 if len(d.text.strip()) >= 60]
#     logger.info("Reliable docs for embed: %d", len(reliable))

#     if len(reliable) < MIN_RELIABLE_SOURCES:
#         return "", 0, len(reliable)

#     reliable.sort(key=lambda d: -d.priority)

#     all_chunks:    List[str]           = []
#     all_metadatas: List[ChunkMetadata] = []
#     for doc in reliable:
#         c, m = _chunk_doc(doc)
#         all_chunks.extend(c)
#         all_metadatas.extend(m)

#     if not all_chunks:
#         return "", 0, len(reliable)

#     logger.info("Chunks to embed: %d", len(all_chunks))

#     try:
#         index, embeddings = _build_index(all_chunks)
#         top_chunks, _, best_score = _mmr_retrieve(
#             query=query, index=index, all_embeddings=embeddings,
#             chunks=all_chunks, metadatas=all_metadatas,
#         )
#         logger.info("MMR score: %.3f  selected: %d", best_score, len(top_chunks))
#     except Exception as e:
#         logger.error("Embed/MMR failed: %s", e)
#         top_chunks = all_chunks[:TOP_K_FINAL]

#     context = "\n\n---\n\n".join(top_chunks)[:MAX_CONTEXT_CHARS]
#     return context, len(all_chunks), len(reliable)


# SYNC WRAPPER 

def run_rag_pipeline_sync(
    query:          str,
    tavily_api_key: str = "",
    core_api_key:   str = "",
    github_token:   str = "",
) -> RAGResult:
    return RAGResult(
        context="",
        sources=all_docs,
        used_rag=False,
        chunk_count=0,
        source_count=len(all_docs),
    )
    # import concurrent.futures

    # t0    = time.time()
    # store = _SourceStore()

    # #  Warm the encoder NOW in the main thread before spawning thread 
    # # This ensures _ENCODER is populated in this process before Phase B.
    # try:
    #     _get_encoder()
    # except Exception as e:
    #     logger.warning("Encoder pre-warm failed: %s", e)

    # #  Phase A: async search in a thread (6s budget) 
    # def _run_search() -> List[SourceDoc]:
    #     return asyncio.run(
    #         _search_all(
    #             query=query, store=store,
    #             tavily_api_key=tavily_api_key,
    #             core_api_key=core_api_key,
    #             github_token=github_token,
    #         )
    #     )

    # all_docs: List[SourceDoc] = []
    # try:
    #     with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
    #         all_docs = pool.submit(_run_search).result(timeout=10)
    #     logger.info("Phase A: %d docs in %.1fs", len(all_docs), time.time()-t0)
    # except concurrent.futures.TimeoutError:
    #     all_docs = store.get()
    #     logger.warning("Phase A timed out — got %d sources from store", len(all_docs))
    # except Exception as e:
    #     all_docs = store.get()
    #     logger.error("Phase A failed: %s — got %d sources", e, len(all_docs))

    # if not all_docs:
    #     return RAGResult(context="", sources=[], used_rag=False)

    # #  Phase B: embed+MMR in main thread (encoder already warm) 
    # context      = ""
    # chunk_count  = 0
    # source_count = 0
    # used_rag     = False

    # try:
    #     context, chunk_count, source_count = _embed_and_retrieve(query, all_docs)
    #     used_rag = bool(context)
    #     logger.info("Phase B done in %.1fs total — used_rag=%s", time.time()-t0, used_rag)
    # except Exception as e:
    #     logger.error("Phase B failed: %s", e, exc_info=True)

    # return RAGResult(
    #     context=context,
    #     sources=all_docs,
    #     used_rag=used_rag,
    #     chunk_count=chunk_count,
    #     source_count=source_count,
    # )

