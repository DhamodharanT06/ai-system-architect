"""
groq_service.py
Optimized LLM service for ArchiMind.

Architecture:
    Blueprint generation = ONLY LLM operation.
    Runtime Flow          = Python from cached Blueprint.
    UI Preview            = Python from cached Blueprint.
    Streaming             = cached Blueprint streamed in chunks.

Groq is the PRIMARY LLM route.
OpenRouter is the FALLBACK route only when Groq raises an exception or returns an unusable blueprint.

Cache:
    - process-local LRU/TTL cache
    - duplicate concurrent requests for the same project are coalesced
    - exact problem/context key is preferred
    - project-name alias is used by /preview and /runtime-flow
    - no endpoint changes are required

Important Vercel note:
    This cache is per warm serverless instance. It is not a shared persistent
    cache between different Vercel instances.
"""

from __future__ import annotations

import asyncio
import copy
import html
import json
import logging
import os
import threading
import time
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Dict, List, Optional, Tuple

from config import settings
from models import ProjectBlueprint, LearningReference
from langchain_groq import ChatGroq
from langchain_openai import ChatOpenAI

logger = logging.getLogger(__name__)

# ============================================================================
# CONFIG
# ============================================================================

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"

# EXACT models requested. Do not read/override these from .env.
PREFERRED_CHAT_MODELS = [
    "openai/gpt-oss-120b",
    "openai/gpt-oss-20b",
]

# Primary Groq model = first preferred model.
GROQ_PRIMARY_MODEL = PREFERRED_CHAT_MODELS[0]

# Keep enough room for the complete blueprint, but do not reserve 8000 tokens.
MAX_RESPONSE_TOKENS = int(os.getenv("BLUEPRINT_MAX_TOKENS", "5500"))

CACHE_TTL_SECONDS = int(os.getenv("BLUEPRINT_CACHE_TTL", "1800"))  # 30 min
CACHE_MAX_ENTRIES = int(os.getenv("BLUEPRINT_CACHE_MAX", "32"))
STREAM_CHUNK_SIZE = 700

# Specialized outputs are cached separately so opening the same preview/flow
# repeatedly does not spend tokens again.
SPECIALIZED_CACHE_TTL_SECONDS = int(os.getenv("SPECIALIZED_CACHE_TTL", "1800"))
SPECIALIZED_CACHE_MAX_ENTRIES = int(os.getenv("SPECIALIZED_CACHE_MAX", "32"))
UI_MAX_TOKENS = int(os.getenv("UI_MAX_TOKENS", "3200"))
FLOW_MAX_TOKENS = int(os.getenv("FLOW_MAX_TOKENS", "1000"))

_specialized_cache: "OrderedDict[str, Tuple[float, Any]]" = OrderedDict()
_specialized_inflight: Dict[str, threading.Event] = {}

# Search is ONLY used to collect links for learning_references.
# Search content is NEVER sent to the LLM.
SEARCH_TIMEOUT_SECONDS = 10

# ============================================================================
# BLUEPRINT CACHE
# ============================================================================

_cache_lock = threading.RLock()
_blueprint_cache: "OrderedDict[str, Tuple[float, ProjectBlueprint]]" = OrderedDict()

# Prevent two simultaneous requests for the same key from making two LLM calls.
_inflight: Dict[str, threading.Event] = {}


def _normalise(value: Optional[str]) -> str:
    if not value:
        return ""
    return " ".join(str(value).strip().lower().split())


def _cache_key(problem_statement: str, context: Optional[str]) -> str:
    return f"{_normalise(problem_statement)}::{_normalise(context)}"


def _project_cache_key(project_name: str) -> str:
    return f"project::{_normalise(project_name)}"


def _clone_blueprint(blueprint: ProjectBlueprint) -> ProjectBlueprint:
    """
    Return a copy so callers cannot accidentally mutate the cached object.
    Pydantic v2 -> model_copy(); fallback -> deepcopy.
    """
    try:
        return blueprint.model_copy(deep=True)
    except AttributeError:
        return copy.deepcopy(blueprint)


def _remove_expired_cache_entries() -> None:
    now = time.time()
    expired = [
        key
        for key, (created, _) in _blueprint_cache.items()
        if now - created >= CACHE_TTL_SECONDS
    ]
    for key in expired:
        _blueprint_cache.pop(key, None)


def _get_cached_blueprint(
    problem_statement: str,
    context: Optional[str] = None,
) -> Optional[ProjectBlueprint]:
    """
    Exact key first, then project-name alias.

    The alias is useful because /preview and /runtime-flow normally receive
    project_name rather than the original full problem statement.
    """
    exact_key = _cache_key(problem_statement, context)

    with _cache_lock:
        _remove_expired_cache_entries()

        item = _blueprint_cache.get(exact_key)
        if item:
            _blueprint_cache.move_to_end(exact_key)
            logger.info("Blueprint cache HIT: exact")
            return _clone_blueprint(item[1])

        project_key = _project_cache_key(problem_statement)
        item = _blueprint_cache.get(project_key)
        if item:
            _blueprint_cache.move_to_end(project_key)
            logger.info("Blueprint cache HIT: project alias")
            return _clone_blueprint(item[1])

    return None


def _save_blueprint_to_cache(
    problem_statement: str,
    context: Optional[str],
    blueprint: ProjectBlueprint,
) -> None:
    exact_key = _cache_key(problem_statement, context)
    project_name = str(getattr(blueprint, "project_name", "") or problem_statement)
    project_key = _project_cache_key(project_name)

    value = _clone_blueprint(blueprint)
    now = time.time()

    with _cache_lock:
        _blueprint_cache[exact_key] = (now, value)
        _blueprint_cache.move_to_end(exact_key)

        # Project alias points to the same logical blueprint.
        _blueprint_cache[project_key] = (now, value)
        _blueprint_cache.move_to_end(project_key)

        # Remove oldest entries until the configured limit is respected.
        while len(_blueprint_cache) > CACHE_MAX_ENTRIES:
            _blueprint_cache.popitem(last=False)

    logger.info(
        "Blueprint cached: project=%s ttl=%ss entries=%d",
        project_name,
        CACHE_TTL_SECONDS,
        len(_blueprint_cache),
    )


# ============================================================================
# JSON PARSING
# ============================================================================

try:
    from json_repair import repair_json

    _JSON_REPAIR_AVAILABLE = True
except ImportError:
    repair_json = None
    _JSON_REPAIR_AVAILABLE = False
    logger.warning("json-repair is not installed; malformed JSON recovery disabled.")


def _clean_json_str(raw: str) -> str:
    text = (raw or "").strip()

    if "```json" in text:
        text = text.split("```json", 1)[1]
        if "```" in text:
            text = text.split("```", 1)[0]
    elif text.startswith("```"):
        text = text[3:]
        if "```" in text:
            text = text.split("```", 1)[0]

    return text.strip()


def _extract_first_json_object(text: str) -> Optional[str]:
    """
    Extract the first complete JSON object while respecting quoted strings.
    """
    start = text.find("{")
    if start < 0:
        return None

    depth = 0
    in_string = False
    escaped = False

    for i in range(start, len(text)):
        ch = text[i]

        if escaped:
            escaped = False
            continue

        if ch == "\\":
            escaped = True
            continue

        if ch == '"':
            in_string = not in_string
            continue

        if in_string:
            continue

        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[start : i + 1]

    return None


def _parse_blueprint_response(
    response_text: str,
    problem_statement: str,
) -> ProjectBlueprint:
    """
    Local parsing only.
    IMPORTANT: this function never calls the LLM again.
    """
    candidates: List[str] = []

    cleaned = _clean_json_str(response_text)
    if cleaned:
        candidates.append(cleaned)

    extracted = _extract_first_json_object(response_text)
    if extracted and extracted not in candidates:
        candidates.append(extracted)

    for candidate in candidates:
        try:
            data = json.loads(candidate)
            blueprint = ProjectBlueprint(**data)
            logger.info(
                "Blueprint parsed successfully for: %s",
                problem_statement[:60],
            )
            return blueprint
        except Exception:
            pass

    if _JSON_REPAIR_AVAILABLE:
        try:
            repaired = repair_json(cleaned, return_objects=False)
            data = json.loads(repaired)
            blueprint = ProjectBlueprint(**data)
            logger.info(
                "Blueprint parsed using local json-repair for: %s",
                problem_statement[:60],
            )
            return blueprint
        except Exception as exc:
            logger.error("Local json-repair failed: %s", exc)

    logger.error(
        "Blueprint JSON parse failed. Response length=%d first500=%r",
        len(response_text or ""),
        (response_text or "")[:500],
    )
    raise ValueError(
        "Failed to parse blueprint JSON from LLM response. "
        "No second LLM call was made."
    )


# ============================================================================
# LLM FACTORIES + PRIMARY/FALLBACK ROUTING
# ============================================================================

def _get_groq_llm(model: str) -> ChatGroq:
    api_key = getattr(settings, "groq_api_key", None)
    if not api_key:
        raise RuntimeError("GROQ_API_KEY is not configured.")

    return ChatGroq(
        api_key=api_key,
        model_name=model,
        temperature=0.1,
        max_tokens=MAX_RESPONSE_TOKENS,
    )


def _get_openrouter_llm(model: str) -> ChatOpenAI:
    api_key = getattr(settings, "openrouter_api_key", None)
    if not api_key:
        raise RuntimeError("OPENROUTER_API_KEY is not configured.")

    return ChatOpenAI(
        api_key=api_key,
        base_url=OPENROUTER_BASE_URL,
        model=model,
        temperature=0.1,
        max_tokens=MAX_RESPONSE_TOKENS,
        default_headers={
            "HTTP-Referer": "https://ai-system-architect.vercel.app",
            "X-Title": "ArchiMind",
        },
    )


def _invoke_llm(llm: Any, user_message: str) -> str:
    """Invoke the Blueprint LLM with the global Blueprint system prompt."""
    return _invoke_specialized_llm(llm, SYSTEM_PROMPT, user_message)


def _invoke_specialized_llm(
    llm: Any,
    system_prompt: str,
    user_message: str,
) -> str:
    """Direct message invocation; avoids ChatPromptTemplate brace parsing."""
    result = llm.invoke([
        ("system", system_prompt),
        ("human", user_message),
    ])
    raw = result.content if hasattr(result, "content") else str(result)
    if isinstance(raw, list):
        raw = "".join(
            item.get("text", str(item)) if isinstance(item, dict) else str(item)
            for item in raw
        )
    raw = str(raw).strip()
    if not raw:
        raise ValueError("LLM returned an empty response.")
    return raw


# ============================================================================
# BLUEPRINT LLM CALL
# ============================================================================

def _call_llm_for_blueprint(
    problem_statement: str,
    context: Optional[str],
) -> ProjectBlueprint:
    """
    Exact routing requested:

        1. Groq PRIMARY -> openai/gpt-oss-120b
        2. If Groq raises ANY exception (400/401/403/404/408/429/5xx,
           timeout, quota, model error, malformed output, etc.), go to OpenRouter.
        3. OpenRouter tries ONLY the two requested models, in order:
             - openai/gpt-oss-120b
             - openai/gpt-oss-20b
        4. Runtime Flow and UI Preview NEVER call this function directly;
           they use the cached blueprint and Python rendering.
    """
    user_message = _build_user_message(problem_statement, context)

    groq_error: Optional[Exception] = None

    # ------------------------------------------------------------------
    # 1) PRIMARY: GROQ
    # ------------------------------------------------------------------
    if getattr(settings, "groq_api_key", None):
        try:
            logger.info(
                "LLM PRIMARY: Groq/%s for blueprint: %s",
                GROQ_PRIMARY_MODEL,
                problem_statement[:70],
            )

            raw = _invoke_llm(
                _get_groq_llm(GROQ_PRIMARY_MODEL),
                user_message,
            )

            # Parsing is part of the Groq attempt. If Groq returns bad JSON,
            # treat that as a failed primary attempt and fall back to OR.
            blueprint = _parse_blueprint_response(raw, problem_statement)
            logger.info("Groq blueprint SUCCESS: %s", GROQ_PRIMARY_MODEL)
            return blueprint

        except Exception as exc:
            groq_error = exc
            logger.warning(
                "Groq failed; falling back to OpenRouter. model=%s error=%s",
                GROQ_PRIMARY_MODEL,
                exc,
            )
    else:
        groq_error = RuntimeError("GROQ_API_KEY is not configured.")
        logger.warning("Groq skipped: GROQ_API_KEY is not configured.")

    # ------------------------------------------------------------------
    # 2) FALLBACK: OPENROUTER
    # ------------------------------------------------------------------
    if not getattr(settings, "openrouter_api_key", None):
        raise RuntimeError(
            f"Groq failed and OPENROUTER_API_KEY is not configured. "
            f"Groq error: {groq_error}"
        ) from groq_error

    openrouter_errors: List[str] = []

    for model in PREFERRED_CHAT_MODELS:
        try:
            logger.warning(
                "LLM FALLBACK: OpenRouter/%s for blueprint",
                model,
            )

            raw = _invoke_llm(
                _get_openrouter_llm(model),
                user_message,
            )

            blueprint = _parse_blueprint_response(raw, problem_statement)
            logger.info("OpenRouter blueprint SUCCESS: %s", model)
            return blueprint

        except Exception as exc:
            openrouter_errors.append(f"{model}: {exc}")
            logger.error(
                "OpenRouter model failed: %s -> %s",
                model,
                exc,
            )
            # Try the second requested OpenRouter model only after this
            # configured fallback model fails.
            continue

    raise RuntimeError(
        "All configured LLM routes failed. "
        f"Groq error: {groq_error}. "
        f"OpenRouter errors: {' | '.join(openrouter_errors)}"
    ) from groq_error


# ============================================================================
# SEARCH-ONLY REFERENCES
# ============================================================================

def _search_sources_only(query: str) -> List[Any]:
    """
    Collect real learning links without running the embedding/RAG pipeline.

    This is intentionally independent from rag_pipeline.py's 5-second
    per-source timeout. Each source gets its own HTTP request and failures
    are isolated, so one slow/rate-limited API cannot make all references
    disappear.

    Sources:
        arXiv, Semantic Scholar, CrossRef, CORE, Tavily, GitHub
    """
    try:
        import httpx
        import re
        from types import SimpleNamespace
    except Exception as exc:
        logger.warning("Reference-search dependencies unavailable: %r", exc)
        return []

    # Keep the query short and search-friendly. Sending the complete
    # problem/blueprint text to scholarly APIs often causes timeouts.
    search_query = " ".join(str(query or "").split())[:350]
    if not search_query:
        return []

    async def fetch_sources() -> List[Any]:
        timeout = httpx.Timeout(12.0, connect=5.0)
        headers = {
            "User-Agent": "ArchiMind/2.0 (+https://ai-system-architecture.vercel.app)",
            "Accept": "application/json, text/plain, */*",
        }

        async with httpx.AsyncClient(
            headers=headers,
            follow_redirects=True,
            timeout=timeout,
        ) as client:

            async def arxiv():
                try:
                    r = await client.get(
                        "https://export.arxiv.org/api/query",
                        params={
                            "search_query": f"all:{search_query}",
                            "max_results": 3,
                            "sortBy": "relevance",
                        },
                    )
                    r.raise_for_status()
                    from bs4 import BeautifulSoup
                    soup = BeautifulSoup(r.text, "xml")
                    docs = []
                    for entry in soup.find_all("entry")[:3]:
                        title_node = entry.find("title")
                        id_node = entry.find("id")
                        summary_node = entry.find("summary")
                        title = title_node.get_text(" ", strip=True) if title_node else ""
                        url = id_node.get_text(" ", strip=True) if id_node else ""
                        if title and url:
                            docs.append(SimpleNamespace(
                                title=title,
                                url=url,
                                source="arxiv",
                                priority=10,
                                doc_type="paper",
                            ))
                    logger.info("Learning refs arXiv: %d", len(docs))
                    return docs
                except Exception as exc:
                    logger.warning("Learning refs arXiv failed: %r", exc)
                    return []

            async def semantic_scholar():
                try:
                    r = await client.get(
                        "https://api.semanticscholar.org/graph/v1/paper/search",
                        params={
                            "query": search_query,
                            "limit": 3,
                            "fields": "title,url,paperId,year",
                        },
                    )
                    r.raise_for_status()
                    docs = []
                    for item in r.json().get("data", [])[:3]:
                        title = str(item.get("title") or "").strip()
                        url = str(item.get("url") or "").strip()
                        if not url and item.get("paperId"):
                            url = f"https://www.semanticscholar.org/paper/{item['paperId']}"
                        if title and url:
                            docs.append(SimpleNamespace(
                                title=title,
                                url=url,
                                source="semantic_scholar",
                                priority=10,
                                doc_type="paper",
                            ))
                    logger.info("Learning refs Semantic Scholar: %d", len(docs))
                    return docs
                except Exception as exc:
                    logger.warning("Learning refs Semantic Scholar failed: %r", exc)
                    return []

            async def crossref():
                try:
                    r = await client.get(
                        "https://api.crossref.org/works",
                        params={
                            "query.bibliographic": search_query,
                            "rows": 3,
                            "select": "title,URL,DOI",
                        },
                    )
                    r.raise_for_status()
                    docs = []
                    for item in r.json().get("message", {}).get("items", [])[:3]:
                        titles = item.get("title") or []
                        title = str(titles[0] if titles else "").strip()
                        url = str(item.get("URL") or "").strip()
                        if not url and item.get("DOI"):
                            url = f"https://doi.org/{item['DOI']}"
                        if title and url:
                            docs.append(SimpleNamespace(
                                title=title,
                                url=url,
                                source="crossref",
                                priority=8,
                                doc_type="paper",
                            ))
                    logger.info("Learning refs CrossRef: %d", len(docs))
                    return docs
                except Exception as exc:
                    logger.warning("Learning refs CrossRef failed: %r", exc)
                    return []

            async def core():
                api_key = getattr(settings, "core_api_key", "") or ""
                if not api_key:
                    logger.info("Learning refs CORE: skipped (CORE_API_KEY not configured)")
                    return []
                try:
                    r = await client.get(
                        "https://api.core.ac.uk/v3/search/works",
                        params={"q": search_query, "limit": 3},
                        headers={"Authorization": f"Bearer {api_key}"},
                    )
                    r.raise_for_status()
                    docs = []
                    for item in r.json().get("results", [])[:3]:
                        title = str(item.get("title") or "").strip()
                        urls = item.get("sourceFulltextUrls") or []
                        url = str((urls[0] if urls else None) or item.get("downloadUrl") or "").strip()
                        if title and url:
                            docs.append(SimpleNamespace(
                                title=title,
                                url=url,
                                source="core",
                                priority=9,
                                doc_type="paper",
                            ))
                    logger.info("Learning refs CORE: %d", len(docs))
                    return docs
                except Exception as exc:
                    logger.warning("Learning refs CORE failed: %r", exc)
                    return []

            async def tavily():
                api_key = getattr(settings, "tavily_api_key", "") or ""
                if not api_key:
                    logger.info("Learning refs Tavily: skipped (TAVILY_API_KEY not configured)")
                    return []
                try:
                    r = await client.post(
                        "https://api.tavily.com/search",
                        json={
                            "api_key": api_key,
                            "query": search_query,
                            "search_depth": "basic",
                            "max_results": 3,
                            "include_raw_content": False,
                        },
                    )
                    r.raise_for_status()
                    docs = []
                    for item in r.json().get("results", [])[:3]:
                        title = str(item.get("title") or item.get("url") or "").strip()
                        url = str(item.get("url") or "").strip()
                        if title and url:
                            docs.append(SimpleNamespace(
                                title=title,
                                url=url,
                                source="tavily",
                                priority=6,
                                doc_type="documentation" if any(
                                    x in url.lower()
                                    for x in ("docs.", "/docs/", "readthedocs", "developer.", "/api/", "/reference/")
                                ) else "article",
                            ))
                    logger.info("Learning refs Tavily: %d", len(docs))
                    return docs
                except Exception as exc:
                    logger.warning("Learning refs Tavily failed: %r", exc)
                    return []

            async def github():
                token = getattr(settings, "github_token", "") or ""
                gh_headers = {"Accept": "application/vnd.github+json"}
                if token:
                    gh_headers["Authorization"] = f"Bearer {token}"
                try:
                    r = await client.get(
                        "https://api.github.com/search/repositories",
                        params={
                            "q": search_query,
                            "sort": "stars",
                            "per_page": 3,
                        },
                        headers=gh_headers,
                    )
                    r.raise_for_status()
                    docs = []
                    for item in r.json().get("items", [])[:3]:
                        title = str(item.get("full_name") or item.get("name") or "").strip()
                        url = str(item.get("html_url") or "").strip()
                        if title and url:
                            docs.append(SimpleNamespace(
                                title=title,
                                url=url,
                                source="github",
                                priority=4,
                                doc_type="repository",
                            ))
                    logger.info("Learning refs GitHub: %d", len(docs))
                    return docs
                except Exception as exc:
                    logger.warning("Learning refs GitHub failed: %r", exc)
                    return []

            results = await asyncio.gather(
                arxiv(),
                semantic_scholar(),
                crossref(),
                core(),
                tavily(),
                github(),
                return_exceptions=True,
            )

        documents: List[Any] = []
        seen_urls = set()

        for result in results:
            if isinstance(result, Exception):
                logger.warning("Learning-reference task failed: %r", result)
                continue
            for document in result or []:
                url = str(getattr(document, "url", "") or "").strip()
                if url and url not in seen_urls:
                    seen_urls.add(url)
                    documents.append(document)

        return documents

    try:
        # Run all six searches concurrently. 25 seconds is the outer budget;
        # individual HTTP calls are capped at 12 seconds.
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(lambda: asyncio.run(fetch_sources()))
            documents = future.result(timeout=25)

        logger.info(
            "Learning-reference search complete: %d unique links",
            len(documents),
        )
        return documents

    except Exception as exc:
        logger.warning("Learning-reference search failed/timeout: %r", exc)
        return []


SOURCE_TYPE_MAP = {
    "arxiv": ("Research Paper", "Advanced"),
    "semantic_scholar": ("Research Paper", "Advanced"),
    "crossref": ("Research Paper", "Intermediate"),
    "core": ("Research Paper", "Advanced"),
    "tavily": ("Guide", "Beginner"),
    "github": ("Guide", "Intermediate"),
}

DOC_DOMAIN_HINTS = (
    "docs.",
    "documentation",
    "readthedocs",
    "developer.",
    "/api/",
    "/reference/",
    "wiki.",
    "man.",
    "guide.",
    "learn.",
)


def _infer_ref_type(doc: Any) -> Tuple[str, str]:
    source = getattr(doc, "source", "") or ""
    ref_type, difficulty = SOURCE_TYPE_MAP.get(
        source,
        ("Guide", "Intermediate"),
    )

    doc_type = getattr(doc, "doc_type", "") or ""
    if source == "tavily" and doc_type == "documentation":
        return "Documentation", "Intermediate"

    url = str(getattr(doc, "url", "") or "").lower()
    if any(hint in url for hint in DOC_DOMAIN_HINTS):
        return "Documentation", "Intermediate"

    return ref_type, difficulty


def _sources_to_learning_references(
    sources: List[Any],
) -> List[LearningReference]:
    """
    Convert search results into the same LearningReference objects used by
    the existing API response.
    """
    seen = set()
    refs: List[LearningReference] = []

    sources = sorted(
        sources,
        key=lambda d: -int(getattr(d, "priority", 5) or 5),
    )

    for doc in sources:
        url = str(getattr(doc, "url", "") or "").strip()
        if not url or url in seen:
            continue

        seen.add(url)

        title = str(getattr(doc, "title", "") or "").strip()
        if not title:
            title = "Learning Resource"

        ref_type, difficulty = _infer_ref_type(doc)

        try:
            refs.append(
                LearningReference(
                    title=title[:300],
                    url=url,
                    type=ref_type,
                    difficulty=difficulty,
                )
            )
        except Exception:
            # If the model enum in models.py is stricter than the search
            # metadata, fall back to a guaranteed schema value.
            try:
                refs.append(
                    LearningReference(
                        title=title[:300],
                        url=url,
                        type="Guide",
                        difficulty=difficulty,
                    )
                )
            except Exception:
                continue

    return refs


def _attach_learning_references(
    blueprint: ProjectBlueprint,
    sources: List[Any],
) -> ProjectBlueprint:
    """
    Backend owns learning_references.
    The LLM is asked to return [] so it does not waste output tokens or invent
    links.
    """
    refs = _sources_to_learning_references(sources)

    try:
        blueprint.learning_references = refs
    except Exception:
        logger.warning("Could not replace learning_references on blueprint.")

    logger.info(
        "Learning references attached: %d (rag_used=False)",
        len(refs),
    )
    return blueprint


# ============================================================================
# PROMPT
# ============================================================================

SYSTEM_PROMPT = """
You are ArchiMind, an expert software system architect.

Return ONLY one valid JSON object matching this schema:

{
  "project_name": string,
  "description": string,
  "problem_statement": string,
  "system_architecture": [
    {
      "name": string,
      "type": "frontend|backend|database|external_api|infrastructure",
      "description": string,
      "responsibilities": [string],
      "technologies": [string]
    }
  ],
  "tech_stack": [
    {
      "name": string,
      "category": string,
      "reason": string,
      "version": string|null,
      "languages": [string],
      "frameworks": [string],
      "modules": [string]
    }
  ],
  "workflow": [
    {
      "step_number": number,
      "title": string,
      "description": string,
      "components_involved": [string],
      "key_actions": [string]
    }
  ],
  "prerequisites": [
    {
      "category": string,
      "items": [string]
    }
  ],
  "solution_approaches": [
    {
      "name": string,
      "description": string,
      "pros": [string],
      "cons": [string],
      "complexity": "Simple|Medium|Complex",
      "estimated_time": string,
      "best_for": string
    }
  ],
  "real_world_examples": [
    {
      "title": string,
      "description": string,
      "company": string,
      "link": string|null,
      "lessons_learned": [string]
    }
  ],
  "learning_references": [],
  "timeline": {
      "phase_name": "duration"
  },
  "estimated_budget": string|null,
  "next_steps": [string]
}

Rules:
- Output JSON only. No markdown and no explanation.
- Target beginners.
- Keep the answer medium-detail and implementation-ready.
- Workflow must contain 7-10 sequential steps.
- Each workflow step has 2+ components and 3-4 key actions.
- Tech stack should contain roughly 8-12 practical technologies.
- Include frontend, backend, database, APIs, infrastructure, testing,
  CI/CD and security when appropriate.
- Keep descriptions concise.
- learning_references MUST be [] because the backend adds real source links.
- Do not invent research links.
"""

# ============================================================================
# CENTRALIZED BLUEPRINT CREATION
# ============================================================================

def _build_user_message(
    problem_statement: str,
    context: Optional[str],
) -> str:
    extra = ""
    if context and context.strip() and context.strip().lower() != "string":
        extra = f"\nAdditional context:\n{context.strip()}"

    return (
        f"Project/problem request:\n{problem_statement.strip()}"
        f"{extra}\n\n"
        "Create the complete architecture blueprint now."
    )


def _get_or_create_blueprint(
    problem_statement: str,
    context: Optional[str] = None,
) -> ProjectBlueprint:
    """
    CENTRAL GATEWAY.

    Every public generation function goes through this function.

    Cache HIT:
        no LLM call.

    Cache MISS:
        exactly one LLM call, then cache the result.

    Concurrent identical requests:
        one request becomes the creator; others wait and then read the cache.
    """
    if not problem_statement or not problem_statement.strip():
        raise ValueError("Project/problem statement cannot be empty.")

    cached = _get_cached_blueprint(problem_statement, context)
    if cached is not None:
        return cached

    key = _cache_key(problem_statement, context)

    with _cache_lock:
        event = _inflight.get(key)

        if event is None:
            event = threading.Event()
            _inflight[key] = event
            is_creator = True
        else:
            is_creator = False

    if not is_creator:
        logger.info("Waiting for in-flight blueprint: %s", problem_statement[:60])

        # Wait for the request that owns this key.
        event.wait(timeout=120)

        cached = _get_cached_blueprint(problem_statement, context)
        if cached is not None:
            return cached

        raise RuntimeError(
            "Blueprint creation did not complete successfully."
        )

    try:
        logger.info(
            "Blueprint cache MISS: %s",
            problem_statement[:70],
        )

        # Only this path performs the LLM call.
        blueprint = _call_llm_for_blueprint(
            problem_statement,
            context,
        )

        # Search-only references are fetched after the blueprint is created.
        # They are NOT passed to the LLM.
        try:
            search_query = " ".join(
                [
                    _bp_text(getattr(blueprint, "project_name", "")),
                    _bp_text(getattr(blueprint, "description", "")),
                    _bp_text(getattr(blueprint, "problem_statement", "")),
                    " ".join(_tech_names(blueprint)[:6]),
                ]
            ).strip()
            sources = _search_sources_only(search_query or problem_statement)
            blueprint = _attach_learning_references(
                blueprint,
                sources,
            )
        except Exception as exc:
            logger.warning(
                "Learning-reference search failed; blueprint still valid: %s",
                exc,
            )

        _save_blueprint_to_cache(
            problem_statement,
            context,
            blueprint,
        )

        return _clone_blueprint(blueprint)

    finally:
        with _cache_lock:
            owner_event = _inflight.pop(key, None)
            if owner_event:
                owner_event.set()


# ============================================================================
# PUBLIC API #1 - KEEP EXISTING ENDPOINT FUNCTION
# ============================================================================

def generate_blueprint(
    problem_statement: str,
    context: Optional[str] = None,
) -> ProjectBlueprint:
    """
    Existing /api/generate entry point.

    Cache HIT -> no LLM.
    Cache MISS -> one LLM call.
    """
    return _get_or_create_blueprint(
        problem_statement,
        context,
    )


# ============================================================================
# PUBLIC API #2 - KEEP EXISTING STREAMING FUNCTION
# ============================================================================

def generate_streaming_blueprint(
    problem_statement: str,
    context: Optional[str] = None,
):
    """
    Existing streaming entry point.

    It does NOT make a separate streaming LLM call.

    On cache MISS:
        one normal blueprint LLM call -> cache.

    On cache HIT:
        zero LLM calls.

    The cached/created blueprint JSON is then yielded in chunks so the existing
    streaming endpoint can keep consuming an iterator.
    """
    blueprint = _get_or_create_blueprint(
        problem_statement,
        context,
    )

    try:
        payload = blueprint.model_dump_json()
    except AttributeError:
        payload = blueprint.json()

    for start in range(0, len(payload), STREAM_CHUNK_SIZE):
        yield payload[start : start + STREAM_CHUNK_SIZE]


# ============================================================================
# BLUEPRINT HELPERS
# ============================================================================

def _bp_list(blueprint: ProjectBlueprint, name: str) -> List[Any]:
    value = getattr(blueprint, name, None)
    return value if isinstance(value, list) else []


def _bp_text(value: Any, default: str = "") -> str:
    if value is None:
        return default
    return str(value).strip()


def _tech_names(blueprint: ProjectBlueprint) -> List[str]:
    names = []
    for item in _bp_list(blueprint, "tech_stack"):
        name = _bp_text(getattr(item, "name", None))
        if name:
            names.append(name)
    return names


def _architecture_items(blueprint: ProjectBlueprint) -> List[Any]:
    return _bp_list(blueprint, "system_architecture")


def _find_architecture(
    blueprint: ProjectBlueprint,
    type_name: str,
) -> List[Any]:
    return [
        item
        for item in _architecture_items(blueprint)
        if _bp_text(getattr(item, "type", None)).lower() == type_name
    ]


def _has_technology(
    blueprint: ProjectBlueprint,
    keywords: Tuple[str, ...],
) -> bool:
    text = " ".join(
        _tech_names(blueprint)
        + [
            _bp_text(getattr(item, "description", None))
            for item in _architecture_items(blueprint)
        ]
    ).lower()

    return any(keyword in text for keyword in keywords)


# ============================================================================
# PUBLIC API #3 - KEEP EXISTING RUNTIME-FLOW FUNCTION
# ============================================================================

def _blueprint_to_runtime_flow(
    blueprint: ProjectBlueprint,
) -> List[Dict[str, Any]]:
    """
    Build the runtime flow from the USER'S BLUEPRINT WORKFLOW.

    The old implementation created one generic application flow
    (User -> Frontend -> Backend -> DB -> Output) for every project.
    That is the reason different user prompts produced almost the same flow.

    This version uses the workflow steps produced for the actual project and
    only adds lane/arrow metadata required by the existing frontend.
    NO LLM call is made here.
    """
    workflow = _bp_list(blueprint, "workflow")
    architecture = _architecture_items(blueprint)
    tech_text = " ".join(_tech_names(blueprint)).lower()
    project_text = " ".join(
        [
            _bp_text(getattr(blueprint, "project_name", "")),
            _bp_text(getattr(blueprint, "description", "")),
            _bp_text(getattr(blueprint, "problem_statement", "")),
        ]
    ).lower()

    def lane_for_step(step: Any) -> str:
        text = " ".join(
            [
                _bp_text(getattr(step, "title", "")),
                _bp_text(getattr(step, "description", "")),
                " ".join(
                    str(x)
                    for x in (getattr(step, "key_actions", None) or [])
                ),
            ]
        ).lower()

        if any(k in text for k in ("user", "customer", "admin", "enter", "select", "upload", "submit")):
            return "user"
        if any(k in text for k in ("ui", "screen", "frontend", "flutter", "react", "mobile", "display", "render")):
            return "frontend"
        if any(k in text for k in ("database", "db", "postgres", "mysql", "mongodb", "firebase", "store", "save", "retrieve")):
            return "database"
        if any(k in text for k in ("model", "llm", "ai", "predict", "inference", "machine learning", "embedding", "generate")):
            return "ai"
        if any(k in text for k in ("external api", "third-party", "payment", "gateway", "api call", "service")):
            return "backend"
        if any(k in text for k in ("result", "response", "notification", "output", "success")):
            return "output"
        if any(k in text for k in ("api", "backend", "server", "authentication", "validate", "business logic")):
            return "backend"
        return "backend"

    def component_hint(lane: str) -> str:
        wanted = {
            "frontend": "frontend",
            "backend": "backend",
            "database": "database",
            "ai": "ai",
        }.get(lane)
        if not wanted:
            return ""
        for item in architecture:
            item_type = _bp_text(getattr(item, "type", "")).lower()
            if wanted in item_type:
                return _bp_text(getattr(item, "name", ""))
        return ""

    steps: List[Dict[str, Any]] = []

    # The Blueprint workflow is the source of truth. This keeps the flow
    # project-specific instead of inventing a fixed architecture pipeline.
    for index, step in enumerate(workflow, start=1):
        title = _bp_text(getattr(step, "title", ""), f"Step {index}")
        description = _bp_text(getattr(step, "description", ""))
        actions = getattr(step, "key_actions", None) or []
        if not isinstance(actions, list):
            actions = [actions]
        action_text = "; ".join(_bp_text(x) for x in actions if _bp_text(x))

        lane = lane_for_step(step)
        hint = component_hint(lane)
        detail_parts = [x for x in (description, action_text) if x]
        if hint and hint.lower() not in " ".join(detail_parts).lower():
            detail_parts.append(f"Component: {hint}")

        steps.append(
            {
                "lane": lane,
                "type": "start" if index == 1 else "process",
                "title": title[:60],
                "detail": " ".join(detail_parts)[:400],
                "arrowTo": None,
                "arrowLabel": None,
            }
        )

    # If a valid Blueprint has no workflow for some reason, create only a
    # minimal project-specific flow from its actual problem statement rather
    # than the old fixed whole-system flow.
    if not steps:
        project_name = _bp_text(getattr(blueprint, "project_name", "Project"))
        problem = _bp_text(getattr(blueprint, "problem_statement", ""))
        steps = [
            {
                "lane": "user",
                "type": "start",
                "title": f"Start {project_name}"[:60],
                "detail": problem[:400],
                "arrowTo": None,
                "arrowLabel": None,
            }
        ]

    # Sequential arrows follow the actual workflow order.
    for index in range(len(steps) - 1):
        steps[index]["arrowTo"] = steps[index + 1]["lane"]
        steps[index]["arrowLabel"] = "Next step"

    steps[-1]["type"] = "end"
    steps[-1]["arrowTo"] = None
    steps[-1]["arrowLabel"] = None

    return steps[:14]

# ============================================================================
# SPECIALIZED GENERATION CONTEXT + CACHE
# ============================================================================

RUNTIME_FLOW_SYSTEM_PROMPT = """You are a senior software architect. Generate the runtime execution flow for THIS exact application.
Return ONLY a JSON array. No markdown or explanation.
Each item: {"lane":"user|frontend|backend|ai|database|output","type":"start|process|decision|end","title":"max 6 words","detail":"one concise runtime sentence","arrowTo":"next lane or null","arrowLabel":"short protocol/action or null"}.
Rules: 8-12 real runtime steps; start with user/start and end with output/end; follow the application's actual user journey; use actual blueprint component and technology names; include important validation/auth/AI/database decisions only when they really occur; never describe development, deployment, or blueprint generation."""

UI_PREVIEW_SYSTEM_PROMPT = """You are a senior product UI/UX engineer. Build the ACTUAL user-facing application described in the project context.
Return ONLY one complete self-contained HTML document. No markdown fences, no explanation.
The UI must be a realistic interactive product for THIS domain, not an architecture dashboard and not an ArchiMind/admin screen.
Use the project's actual features, workflow, entities, terminology and platform from the context. Include useful interactions/buttons/forms appropriate to the domain, realistic sample state/data, responsive layout, accessible labels, and concise JavaScript.
No external libraries, CDN, network calls, backend calls, or dependencies. Put CSS in <style> and JS in <script>. Keep the HTML reasonably compact while still looking polished and functional."""


def _compact_blueprint_context(blueprint: ProjectBlueprint, original_context: Optional[str] = None, limit: int = 11000) -> str:
    """Send only high-value blueprint facts to specialized calls to reduce input tokens."""
    def txt(v: Any) -> str:
        return _bp_text(v)

    arch = []
    for item in (getattr(blueprint, "system_architecture", None) or [])[:8]:
        tech = ", ".join(txt(x) for x in (getattr(item, "technologies", None) or [])[:5] if txt(x))
        arch.append(f"{txt(getattr(item,'name',''))} [{txt(getattr(item,'type',''))}]: {txt(getattr(item,'description',''))[:220]}" + (f"; tech={tech}" if tech else ""))

    stack = []
    for item in (getattr(blueprint, "tech_stack", None) or [])[:12]:
        stack.append(f"{txt(getattr(item,'name',''))} ({txt(getattr(item,'category',''))})")

    workflow = []
    for item in (getattr(blueprint, "workflow", None) or [])[:10]:
        actions = ", ".join(txt(x) for x in (getattr(item,'key_actions',None) or [])[:4] if txt(x))
        workflow.append(f"{txt(getattr(item,'step_number',''))}. {txt(getattr(item,'title',''))}: {txt(getattr(item,'description',''))[:220]}" + (f"; actions={actions}" if actions else ""))

    prereq = []
    for item in (getattr(blueprint, "prerequisites", None) or [])[:5]:
        vals = ", ".join(txt(x) for x in (getattr(item,'items',None) or [])[:5] if txt(x))
        if vals: prereq.append(f"{txt(getattr(item,'category',''))}: {vals}")

    original = txt(original_context)[:1800] if original_context and txt(original_context).lower() != "string" else ""
    parts = [
        f"PROJECT: {txt(getattr(blueprint,'project_name',''))}",
        f"DESCRIPTION: {txt(getattr(blueprint,'description',''))[:700]}",
        f"PROBLEM: {txt(getattr(blueprint,'problem_statement',''))[:900]}",
        "ARCHITECTURE: " + " | ".join(arch),
        "TECH STACK: " + ", ".join(stack),
        "WORKFLOW: " + " | ".join(workflow),
        "PREREQUISITES: " + " | ".join(prereq),
    ]
    if original: parts.append("ORIGINAL CONTEXT: " + original)
    return "\n".join(parts)[:limit]


def _specialized_cache_get(key: str) -> Any:
    now = time.time()
    with _cache_lock:
        expired = [k for k,(t,_) in _specialized_cache.items() if now - t >= SPECIALIZED_CACHE_TTL_SECONDS]
        for k in expired: _specialized_cache.pop(k, None)
        item = _specialized_cache.get(key)
        if item is None: return None
        _specialized_cache.move_to_end(key)
        return copy.deepcopy(item[1])


def _specialized_cache_put(key: str, value: Any) -> None:
    with _cache_lock:
        _specialized_cache[key] = (time.time(), copy.deepcopy(value))
        _specialized_cache.move_to_end(key)
        while len(_specialized_cache) > SPECIALIZED_CACHE_MAX_ENTRIES:
            _specialized_cache.popitem(last=False)


def _specialized_call(
    cache_key: str,
    system_prompt: str,
    user_message: str,
    max_tokens: int,
    parser,
) -> Any:
    """One cached specialized generation; fallback uses only the two allowed models."""
    cached = _specialized_cache_get(cache_key)
    if cached is not None:
        logger.info("Specialized cache HIT: %s", cache_key[:80])
        return cached

    with _cache_lock:
        event = _specialized_inflight.get(cache_key)
        if event is None:
            event = threading.Event()
            _specialized_inflight[cache_key] = event
            creator = True
        else:
            creator = False

    if not creator:
        event.wait(timeout=120)
        cached = _specialized_cache_get(cache_key)
        if cached is not None: return cached
        raise RuntimeError("Specialized generation did not complete.")

    try:
        errors = []
        llms = []
        if getattr(settings, "groq_api_key", None):
            llms.append(("Groq", GROQ_PRIMARY_MODEL, _get_groq_llm(GROQ_PRIMARY_MODEL)))
        if getattr(settings, "openrouter_api_key", None):
            for model in PREFERRED_CHAT_MODELS:
                llms.append(("OpenRouter", model, _get_openrouter_llm(model)))

        for provider, model, llm in llms:
            try:
                logger.info("Specialized LLM: %s/%s", provider, model)
                raw = _invoke_specialized_llm(llm, system_prompt, user_message)
                value = parser(raw)
                _specialized_cache_put(cache_key, value)
                return copy.deepcopy(value)
            except Exception as exc:
                errors.append(f"{provider}/{model}: {exc}")
                logger.warning("Specialized model failed: %s", errors[-1])

        raise RuntimeError("All specialized models failed: " + " | ".join(errors[-3:]))
    finally:
        with _cache_lock:
            current = _specialized_inflight.pop(cache_key, None)
            if current: current.set()


def _parse_runtime_flow(raw: str) -> list:
    cleaned = raw.strip().replace("```json", "").replace("```", "").strip()
    try:
        steps = json.loads(cleaned)
    except json.JSONDecodeError:
        a, b = cleaned.find("["), cleaned.rfind("]")
        if a < 0 or b <= a: raise ValueError("Runtime flow JSON array not found")
        steps = json.loads(cleaned[a:b+1])
    if not isinstance(steps, list) or not steps: raise ValueError("Runtime flow is empty")
    valid_lanes = {"user","frontend","backend","ai","database","output"}
    valid_types = {"start","process","decision","end"}
    clean = []
    for item in steps[:12]:
        if not isinstance(item, dict): continue
        lane = item.get("lane") if item.get("lane") in valid_lanes else "backend"
        typ = item.get("type") if item.get("type") in valid_types else "process"
        clean.append({
            "lane": lane, "type": typ,
            "title": _bp_text(item.get("title", "Step"))[:70],
            "detail": _bp_text(item.get("detail", item.get("title", "")))[:420],
            "arrowTo": item.get("arrowTo") if item.get("arrowTo") in valid_lanes else None,
            "arrowLabel": _bp_text(item.get("arrowLabel", ""))[:80] or None,
        })
    if len(clean) < 2: raise ValueError("Runtime flow has too few valid steps")
    clean[0]["lane"], clean[0]["type"] = "user", "start"
    clean[-1]["lane"], clean[-1]["type"] = "output", "end"
    for i in range(len(clean)-1):
        clean[i]["arrowTo"] = clean[i+1]["lane"]
        if not clean[i]["arrowLabel"]: clean[i]["arrowLabel"] = "Next"
    clean[-1]["arrowTo"] = clean[-1]["arrowLabel"] = None
    return clean


def _parse_ui_html(raw: str) -> str:
    cleaned = raw.strip()
    if "```html" in cleaned: cleaned = cleaned.split("```html", 1)[1]
    cleaned = cleaned.replace("```", "").strip()
    lower = cleaned.lower()
    if "<html" not in lower or "</html>" not in lower or "<body" not in lower:
        raise ValueError("UI model did not return a complete HTML document")
    return cleaned


def generate_runtime_flow(
    project_name: str,
    context: Optional[str] = None,
) -> list:
    """Generate a project-specific runtime flow from the cached Blueprint."""
    blueprint = _get_or_create_blueprint(project_name, context)
    compact = _compact_blueprint_context(blueprint, context, limit=10000)
    key = "flow::" + _cache_key(project_name, context)
    user_message = (
        "Use this project context as the source of truth. Generate the runtime journey, "
        "not the development process. Every step must correspond to a real user action, "
        "system operation, decision, or output in this application.\n\n" + compact
    )
    flow = _specialized_call(key, RUNTIME_FLOW_SYSTEM_PROMPT, user_message, FLOW_MAX_TOKENS, _parse_runtime_flow)
    logger.info("Runtime flow generated: %d steps for %s", len(flow), getattr(blueprint, "project_name", project_name))
    return flow


# ============================================================================
# PUBLIC API #4 - KEEP EXISTING UI PREVIEW FUNCTION
# ============================================================================

def _html_escape(value: Any) -> str:
    return html.escape(_bp_text(value))


def _render_architecture_cards(
    blueprint: ProjectBlueprint,
) -> str:
    cards = []

    for item in _architecture_items(blueprint):
        name = _html_escape(getattr(item, "name", "Component"))
        type_name = _html_escape(getattr(item, "type", "component"))
        desc = _html_escape(getattr(item, "description", ""))

        technologies = getattr(item, "technologies", None) or []
        if not isinstance(technologies, list):
            technologies = [technologies]

        tech_html = "".join(
            f"<span class='tag'>{_html_escape(t)}</span>"
            for t in technologies
            if _bp_text(t)
        )

        responsibilities = getattr(item, "responsibilities", None) or []
        if not isinstance(responsibilities, list):
            responsibilities = [responsibilities]

        responsibility_html = "".join(
            f"<li>{_html_escape(x)}</li>"
            for x in responsibilities[:6]
            if _bp_text(x)
        )

        cards.append(
            f"""
            <article class="card searchable">
                <div class="card-head">
                    <h3>{name}</h3>
                    <span class="badge">{type_name}</span>
                </div>
                <p>{desc}</p>
                <div class="tags">{tech_html}</div>
                <ul>{responsibility_html}</ul>
            </article>
            """
        )

    return "".join(cards) or "<p class='muted'>No architecture components.</p>"


def _render_tech_cards(
    blueprint: ProjectBlueprint,
) -> str:
    cards = []

    for item in _bp_list(blueprint, "tech_stack"):
        name = _html_escape(getattr(item, "name", "Technology"))
        category = _html_escape(getattr(item, "category", "Technology"))
        reason = _html_escape(getattr(item, "reason", ""))

        version = _bp_text(getattr(item, "version", None))
        version_html = (
            f"<span class='version'>{_html_escape(version)}</span>"
            if version
            else ""
        )

        cards.append(
            f"""
            <article class="card searchable">
                <div class="card-head">
                    <h3>{name}</h3>
                    <span class="badge">{category}</span>
                </div>
                {version_html}
                <p>{reason}</p>
            </article>
            """
        )

    return "".join(cards) or "<p class='muted'>No technologies.</p>"


def _render_workflow(
    blueprint: ProjectBlueprint,
) -> str:
    rows = []

    for step in _bp_list(blueprint, "workflow"):
        number = _html_escape(getattr(step, "step_number", ""))
        title = _html_escape(getattr(step, "title", "Step"))
        description = _html_escape(getattr(step, "description", ""))

        actions = getattr(step, "key_actions", None) or []
        if not isinstance(actions, list):
            actions = [actions]

        actions_html = "".join(
            f"<li>{_html_escape(x)}</li>"
            for x in actions[:4]
            if _bp_text(x)
        )

        rows.append(
            f"""
            <div class="workflow-step searchable">
                <div class="step-number">{number}</div>
                <div>
                    <h3>{title}</h3>
                    <p>{description}</p>
                    <ul>{actions_html}</ul>
                </div>
            </div>
            """
        )

    return "".join(rows) or "<p class='muted'>No workflow steps.</p>"


def _build_ui_preview_from_blueprint(
    blueprint: ProjectBlueprint,
) -> str:
    """
    Build a project-specific APPLICATION preview from the cached Blueprint.

    Important:
    - This is not an ArchiMind/admin dashboard.
    - It uses the actual project name, problem, workflow, architecture and
      technologies from this user's blueprint.
    - No LLM call is made.
    """
    project_name_raw = _bp_text(getattr(blueprint, "project_name", "Project"))
    description_raw = _bp_text(getattr(blueprint, "description", ""))
    problem_raw = _bp_text(getattr(blueprint, "problem_statement", ""))

    project_name = _html_escape(project_name_raw)
    description = _html_escape(description_raw)
    problem = _html_escape(problem_raw)

    workflow = _bp_list(blueprint, "workflow")
    architecture = _architecture_items(blueprint)
    tech_stack = _bp_list(blueprint, "tech_stack")
    prerequisites = _bp_list(blueprint, "prerequisites")

    combined_text = " ".join(
        [
            project_name_raw,
            description_raw,
            problem_raw,
            " ".join(
                _bp_text(getattr(s, "title", ""))
                for s in workflow
            ),
        ]
    ).lower()

    # Select the main application interaction from the actual project request.
    if any(k in combined_text for k in ("chat", "messaging", "assistant", "conversation")):
        mode = "chat"
        primary_label = "Start Conversation"
    elif any(k in combined_text for k in ("parking", "slot", "vehicle parking")):
        mode = "parking"
        primary_label = "Check Parking"
    elif any(k in combined_text for k in ("resume", "job description", "candidate", "recruit")):
        mode = "resume"
        primary_label = "Analyze Match"
    elif any(k in combined_text for k in ("fitness", "workout", "health tracker", "calorie")):
        mode = "fitness"
        primary_label = "Track Activity"
    elif any(k in combined_text for k in ("ecommerce", "e-commerce", "shopping", "product catalog", "cart")):
        mode = "commerce"
        primary_label = "Browse Products"
    elif any(k in combined_text for k in ("booking", "appointment", "reservation")):
        mode = "booking"
        primary_label = "Make Booking"
    else:
        mode = "workspace"
        primary_label = (
            _bp_text(getattr(workflow[0], "title", "Start Project"))
            if workflow else "Start Project"
        )

    def list_html(items: Any, limit: int = 6) -> str:
        if not isinstance(items, list):
            items = [items] if items else []
        return "".join(
            f"<li>{_html_escape(x)}</li>"
            for x in items[:limit]
            if _bp_text(x)
        )

    # Actual project workflow cards.
    workflow_cards = ""
    for index, step in enumerate(workflow[:10], start=1):
        title = _html_escape(
            _bp_text(getattr(step, "title", ""), f"Step {index}")
        )
        desc = _html_escape(getattr(step, "description", ""))
        actions = getattr(step, "key_actions", None) or []
        if not isinstance(actions, list):
            actions = [actions]
        actions_html = list_html(actions, 3)
        workflow_cards += f"""
        <article class="flow-card">
            <span class="flow-no">{index}</span>
            <div>
                <h3>{title}</h3>
                <p>{desc}</p>
                <ul>{actions_html}</ul>
            </div>
        </article>
        """

    # Actual architecture modules.
    module_cards = ""
    for item in architecture[:8]:
        name = _html_escape(getattr(item, "name", "Module"))
        typ = _html_escape(getattr(item, "type", "component"))
        desc = _html_escape(getattr(item, "description", ""))
        module_cards += f"""
        <article class="module-card">
            <span class="module-type">{typ}</span>
            <h3>{name}</h3>
            <p>{desc}</p>
        </article>
        """

    tech_chips = ""
    for item in tech_stack[:12]:
        name = _html_escape(getattr(item, "name", ""))
        if name:
            tech_chips += f"<span class='chip'>{name}</span>"

    prerequisite_html = list_html(
        [
            _bp_text(getattr(p, "category", ""))
            + ": "
            + ", ".join(
                _bp_text(x)
                for x in (getattr(p, "items", None) or [])
                if _bp_text(x)
            )
            for p in prerequisites[:6]
        ],
        6,
    )

    # Project-specific central interaction area.
    if mode == "chat":
        interaction_html = """
        <div class="interaction">
            <div class="chat-box">
                <div class="bubble assistant">How can I help you with this project?</div>
                <div class="bubble user">I want to start a new request.</div>
            </div>
            <div class="input-row"><input placeholder="Type your request..." /><button>Send</button></div>
        </div>
        """
    elif mode == "parking":
        interaction_html = """
        <div class="interaction">
            <div class="metric-row">
                <div class="metric"><strong>Available</strong><span>Live slots</span></div>
                <div class="metric"><strong>Occupied</strong><span>Detected vehicles</span></div>
                <div class="metric"><strong>Status</strong><span>Real-time view</span></div>
            </div>
            <div class="slot-grid">
                <div class="slot free">A1<br><small>Free</small></div>
                <div class="slot occupied">A2<br><small>Occupied</small></div>
                <div class="slot free">A3<br><small>Free</small></div>
                <div class="slot partial">A4<br><small>Partial</small></div>
            </div>
        </div>
        """
    elif mode == "resume":
        interaction_html = """
        <div class="interaction">
            <div class="upload-box">Upload Resume</div>
            <div class="upload-box">Add Job Description</div>
            <button class="primary">Analyze Match</button>
        </div>
        """
    elif mode == "fitness":
        interaction_html = """
        <div class="interaction">
            <div class="metric-row">
                <div class="metric"><strong>Steps</strong><span>Today's activity</span></div>
                <div class="metric"><strong>Calories</strong><span>Energy tracked</span></div>
                <div class="metric"><strong>Workout</strong><span>Progress</span></div>
            </div>
            <div class="progress"><span></span></div>
            <button class="primary">Track Activity</button>
        </div>
        """
    elif mode == "commerce":
        interaction_html = """
        <div class="interaction product-grid">
            <div class="product"><div class="product-img"></div><strong>Product</strong><span>View details</span></div>
            <div class="product"><div class="product-img"></div><strong>Product</strong><span>Add to cart</span></div>
            <div class="product"><div class="product-img"></div><strong>Product</strong><span>View details</span></div>
        </div>
        """
    elif mode == "booking":
        interaction_html = """
        <div class="interaction">
            <div class="input-row"><input placeholder="Select date" /><input placeholder="Select time" /></div>
            <button class="primary">Make Booking</button>
        </div>
        """
    else:
        # For arbitrary projects, use the user's first actual workflow action
        # instead of inventing a generic dashboard.
        first_action = ""
        if workflow:
            actions = getattr(workflow[0], "key_actions", None) or []
            if isinstance(actions, list) and actions:
                first_action = _bp_text(actions[0])
        interaction_html = f"""
        <div class="interaction workspace">
            <div class="task-title">{_html_escape(primary_label)}</div>
            <p>{_html_escape(first_action or description_raw or problem_raw)}</p>
            <button class="primary">{_html_escape(primary_label)}</button>
        </div>
        """

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>{project_name} — App Preview</title>
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap');
:root {{
  --bg:#071018; --surface:#0e1924; --surface2:#132331; --border:#243746;
  --accent:#06b6d4; --text:#e8f6f5; --muted:#8fa5b7;
}}
* {{ box-sizing:border-box; }}
body {{ margin:0; background:var(--bg); color:var(--text); font-family:Inter,Arial,sans-serif; }}
button,input {{ font:inherit; }}
.app {{ min-height:100vh; }}
.top {{ border-bottom:1px solid var(--border); padding:18px 5%; display:flex; justify-content:space-between; gap:20px; align-items:center; background:#08121b; }}
.logo {{ font-weight:700; font-size:18px; }}
.logo span {{ color:var(--accent); }}
.nav {{ display:flex; gap:8px; }}
.nav button {{ background:transparent; color:var(--muted); border:0; padding:8px 11px; cursor:pointer; }}
.nav button.active {{ color:var(--text); }}
.container {{ width:min(1120px,92%); margin:auto; padding:34px 0 60px; }}
.hero {{ display:grid; grid-template-columns:1.5fr 1fr; gap:20px; margin-bottom:28px; }}
.panel {{ background:var(--surface); border:1px solid var(--border); border-radius:14px; padding:22px; }}
.eyebrow {{ color:var(--accent); text-transform:uppercase; font-size:11px; font-weight:700; letter-spacing:.08em; }}
h1 {{ font-size:clamp(28px,4vw,46px); margin:8px 0 10px; }}
h2 {{ font-size:21px; margin:0 0 14px; }}
h3 {{ margin:6px 0; font-size:16px; }}
p,li {{ color:var(--muted); line-height:1.6; }}
.primary {{ background:var(--accent); color:#041016; border:0; border-radius:8px; padding:11px 16px; font-weight:700; cursor:pointer; }}
.interaction {{ background:var(--surface); border:1px solid var(--border); border-radius:14px; padding:20px; min-height:180px; }}
.section {{ margin-top:28px; scroll-margin-top:75px; }}
.flow {{ display:grid; gap:10px; }}
.flow-card {{ display:grid; grid-template-columns:38px 1fr; gap:13px; padding:15px; border:1px solid var(--border); background:var(--surface); border-radius:12px; }}
.flow-no {{ width:32px; height:32px; border-radius:50%; display:grid; place-items:center; background:#10313b; color:var(--accent); font-weight:700; }}
.flow-card ul {{ margin-bottom:0; }}
.modules {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(210px,1fr)); gap:12px; }}
.module-card {{ background:var(--surface); border:1px solid var(--border); border-radius:12px; padding:17px; }}
.module-type {{ color:var(--accent); font-size:11px; text-transform:uppercase; }}
.chips {{ display:flex; flex-wrap:wrap; gap:8px; }}
.chip {{ padding:8px 10px; border:1px solid var(--border); border-radius:8px; background:var(--surface); }}
.metric-row {{ display:grid; grid-template-columns:repeat(3,1fr); gap:10px; }}
.metric {{ padding:15px; border:1px solid var(--border); border-radius:10px; background:var(--surface2); }}
.metric strong,.metric span {{ display:block; }}
.metric span {{ color:var(--muted); font-size:12px; margin-top:5px; }}
.slot-grid {{ display:grid; grid-template-columns:repeat(4,1fr); gap:9px; margin-top:16px; }}
.slot {{ padding:18px 8px; text-align:center; border-radius:9px; background:#16323b; }}
.slot.occupied {{ background:#3a2028; }}
.slot.partial {{ background:#3b3420; }}
.slot small {{ color:var(--muted); }}
.chat-box {{ display:grid; gap:9px; }}
.bubble {{ max-width:75%; padding:10px 12px; border-radius:10px; }}
.bubble.assistant {{ background:var(--surface2); }}
.bubble.user {{ background:#10313b; justify-self:end; }}
.input-row {{ display:flex; gap:8px; margin-top:14px; }}
.input-row input {{ flex:1; min-width:0; background:var(--surface2); border:1px solid var(--border); color:var(--text); padding:11px; border-radius:8px; }}
.upload-box {{ padding:22px; border:1px dashed #3a5364; border-radius:9px; color:var(--muted); margin-bottom:10px; }}
.product-grid {{ display:grid; grid-template-columns:repeat(3,1fr); gap:12px; }}
.product {{ padding:12px; border:1px solid var(--border); border-radius:10px; display:grid; gap:7px; }}
.product span {{ color:var(--muted); font-size:12px; }}
.product-img {{ height:90px; background:var(--surface2); border-radius:7px; }}
.progress {{ height:8px; background:var(--surface2); border-radius:10px; margin:20px 0; overflow:hidden; }}
.progress span {{ display:block; width:65%; height:100%; background:var(--accent); }}
.workspace {{ display:grid; gap:12px; align-content:center; }}
.task-title {{ font-size:20px; font-weight:700; }}
.hidden {{ display:none; }}
@media(max-width:700px) {{
  .hero {{ grid-template-columns:1fr; }}
  .nav {{ display:none; }}
  .metric-row,.product-grid {{ grid-template-columns:1fr; }}
  .slot-grid {{ grid-template-columns:repeat(2,1fr); }}
  .top {{ padding:15px 4%; }}
}}
</style>
</head>
<body>
<div class="app">
<header class="top">
  <div class="logo"><span>●</span> {project_name}</div>
  <nav class="nav">
    <button class="active" data-id="home">Home</button>
    <button data-id="flow">Flow</button>
    <button data-id="modules">Modules</button>
    <button data-id="tech">Technology</button>
  </nav>
</header>
<main class="container">
<section id="home" class="hero section">
  <div class="panel">
    <div class="eyebrow">Application</div>
    <h1>{project_name}</h1>
    <p>{description}</p>
    <p><strong>User need:</strong> {problem}</p>
  </div>
  {interaction_html}
</section>

<section id="flow" class="section">
  <h2>User workflow</h2>
  <div class="flow">{workflow_cards or '<div class="panel"><p>No workflow available.</p></div>'}</div>
</section>

<section id="modules" class="section">
  <h2>Application modules</h2>
  <div class="modules">{module_cards or '<div class="panel"><p>No modules available.</p></div>'}</div>
</section>

<section id="tech" class="section">
  <h2>Technology used</h2>
  <div class="chips">{tech_chips or '<span>Not specified</span>'}</div>
</section>

<section class="section">
  <h2>Prerequisites</h2>
  <div class="panel"><ul>{prerequisite_html or '<li>Configured according to the project requirements.</li>'}</ul></div>
</section>
</main>
</div>
<script>
document.querySelectorAll('.nav button').forEach(function(btn) {{
  btn.addEventListener('click', function() {{
    var target = document.getElementById(btn.dataset.id);
    if (target) target.scrollIntoView({{behavior:'smooth', block:'start'}});
    document.querySelectorAll('.nav button').forEach(function(b) {{
      b.classList.toggle('active', b === btn);
    }});
  }});
}});
</script>
</body>
</html>"""

def generate_ui_preview(
    project_name: str,
    context: Optional[str] = None,
) -> str:
    """Generate a project-specific interactive HTML preview from the cached Blueprint."""
    blueprint = _get_or_create_blueprint(project_name, context)
    compact = _compact_blueprint_context(blueprint, context, limit=10500)
    key = "ui::" + _cache_key(project_name, context)
    user_message = (
        "Design the actual end-user product represented by this project. "
        "Do not create an architecture/documentation dashboard. "
        "Use the workflow and domain features to decide the primary screen, "
        "controls, sample content and interactions.\n\n" + compact
    )
    html_output = _specialized_call(key, UI_PREVIEW_SYSTEM_PROMPT, user_message, UI_MAX_TOKENS, _parse_ui_html)
    logger.info("UI preview generated: %d chars for %s", len(html_output), getattr(blueprint, "project_name", project_name))
    return html_output


# ============================================================================
# OPTIONAL DEBUG HELPERS - DO NOT CHANGE ENDPOINTS
# ============================================================================

def clear_blueprint_cache() -> None:
    """
    Useful during development/testing.
    Do not call this from normal request handling.
    """
    with _cache_lock:
        _blueprint_cache.clear()
        _specialized_cache.clear()
        logger.info("Blueprint and specialized caches cleared.")


def blueprint_cache_stats() -> Dict[str, Any]:
    with _cache_lock:
        _remove_expired_cache_entries()
        return {
            "entries": len(_blueprint_cache),
            "max_entries": CACHE_MAX_ENTRIES,
            "ttl_seconds": CACHE_TTL_SECONDS,
            "groq_primary_model": GROQ_PRIMARY_MODEL,
            "openrouter_models": list(settings.openrouter_model),
            "specialized_cache_entries": len(_specialized_cache),
            "specialized_cache_ttl_seconds": SPECIALIZED_CACHE_TTL_SECONDS,
            "runtime_flow_llm": True,
            "ui_preview_llm": True,
            "streaming_extra_llm": False,
        }
