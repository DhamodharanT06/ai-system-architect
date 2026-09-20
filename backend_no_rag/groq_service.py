"""
groq_service.py
Optimized LLM service for ArchiMind.

Architecture:
    Blueprint generation = ONLY LLM operation.
    Runtime Flow          = Python from cached Blueprint.
    UI Preview            = Python from cached Blueprint.
    Streaming             = cached Blueprint streamed in chunks.

No Groq model candidates are used.
OpenRouter `openrouter/free` is the only LLM route.

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
from langchain_openai import ChatOpenAI

logger = logging.getLogger(__name__)

# ============================================================================
# CONFIG
# ============================================================================

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"

# Deliberately fixed: this prevents an old/paid model in .env from being used.
OPENROUTER_FREE_MODEL = "openrouter/free"

# Keep enough room for the complete blueprint, but do not reserve 8000 tokens.
MAX_RESPONSE_TOKENS = int(os.getenv("BLUEPRINT_MAX_TOKENS", "5500"))

CACHE_TTL_SECONDS = int(os.getenv("BLUEPRINT_CACHE_TTL", "1800"))  # 30 min
CACHE_MAX_ENTRIES = int(os.getenv("BLUEPRINT_CACHE_MAX", "32"))
STREAM_CHUNK_SIZE = 700

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
# OPENROUTER - ONLY LLM
# ============================================================================

def _get_openrouter_llm() -> ChatOpenAI:
    api_key = getattr(settings, "openrouter_api_key", None)

    if not api_key:
        raise RuntimeError(
            "OPENROUTER_API_KEY is not configured."
        )

    return ChatOpenAI(
        api_key=api_key,
        base_url=OPENROUTER_BASE_URL,
        model=OPENROUTER_FREE_MODEL,
        temperature=0.1,
        max_tokens=MAX_RESPONSE_TOKENS,
        default_headers={
            "HTTP-Referer": "https://ai-system-architect.vercel.app",
            "X-Title": "ArchiMind",
        },
    )


# ============================================================================
# SEARCH-ONLY REFERENCES
# ============================================================================

def _search_sources_only(query: str) -> List[Any]:
    """
    Search the existing six sources only to obtain links.

    IMPORTANT:
      - No embeddings.
      - No FAISS.
      - No SentenceTransformer.
      - No retrieved text/context sent to the LLM.
      - No RAG.
    """
    try:
        from rag_pipeline import (
            _SourceStore,
            _search_all,
        )
    except Exception as exc:
        logger.warning("Search-only imports unavailable: %s", exc)
        return []

    store = _SourceStore()

    def run() -> List[Any]:
        return asyncio.run(
            _search_all(
                query=query,
                store=store,
                tavily_api_key=getattr(settings, "tavily_api_key", "") or "",
                core_api_key=getattr(settings, "core_api_key", "") or "",
                github_token=getattr(settings, "github_token", "") or "",
            )
        )

    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(run)
            docs = future.result(timeout=SEARCH_TIMEOUT_SECONDS)

        logger.info("Search-only references collected: %d", len(docs))
        return docs or []

    except Exception as exc:
        # The store may contain partial results even if a source timed out.
        try:
            docs = store.get()
        except Exception:
            docs = []

        logger.warning(
            "Search-only collection failed/timeout: %s; partial=%d",
            exc,
            len(docs),
        )
        return docs


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


def _call_llm_for_blueprint(
    problem_statement: str,
    context: Optional[str],
) -> ProjectBlueprint:
    """
    THIS IS THE ONLY FUNCTION in this file that calls an LLM.
    """
    user_message = _build_user_message(problem_statement, context)

    logger.info(
        "LLM CALL: OpenRouter/%s for blueprint: %s",
        OPENROUTER_FREE_MODEL,
        problem_statement[:70],
    )

    llm = _get_openrouter_llm()

    # One and only one LLM invocation.
    result = llm.invoke(
        [
            ("system", SYSTEM_PROMPT),
            ("human", user_message),
        ]
    )

    raw = result.content if hasattr(result, "content") else str(result)

    if isinstance(raw, list):
        raw = "".join(
            item.get("text", str(item))
            if isinstance(item, dict)
            else str(item)
            for item in raw
        )

    raw = str(raw).strip()

    if not raw:
        raise ValueError("LLM returned an empty blueprint response.")

    return _parse_blueprint_response(raw, problem_statement)


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
            sources = _search_sources_only(
                getattr(blueprint, "problem_statement", None)
                or problem_statement
            )
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
    Deterministically create the runtime diagram from the Blueprint.

    NO LLM.
    NO JSON generated by a model.
    """
    architecture = _architecture_items(blueprint)
    tech = _tech_names(blueprint)

    frontend = _find_architecture(blueprint, "frontend")
    backend = _find_architecture(blueprint, "backend")
    database = _find_architecture(blueprint, "database")
    external_api = _find_architecture(blueprint, "external_api")

    frontend_name = (
        _bp_text(getattr(frontend[0], "name", None), "Frontend")
        if frontend
        else "Frontend"
    )
    backend_name = (
        _bp_text(getattr(backend[0], "name", None), "Backend")
        if backend
        else "Backend"
    )
    database_name = (
        _bp_text(getattr(database[0], "name", None), "Database")
        if database
        else "Database"
    )

    frontend_tech = ", ".join(
        _bp_text(getattr(x, "technologies", None))
        for x in frontend
    ).strip(", ")

    backend_tech = ", ".join(
        _bp_text(getattr(x, "technologies", None))
        for x in backend
    ).strip(", ")

    has_ai = _has_technology(
        blueprint,
        (
            "ai",
            "llm",
            "machine learning",
            "deep learning",
            "tensorflow",
            "pytorch",
            "groq",
            "openrouter",
            "model",
            "generative",
        ),
    )

    has_db = bool(database)
    has_external_api = bool(external_api)

    steps: List[Dict[str, Any]] = []

    def add(
        lane: str,
        step_type: str,
        title: str,
        detail: str,
        next_lane: Optional[str],
        label: Optional[str],
    ) -> None:
        steps.append(
            {
                "lane": lane,
                "type": step_type,
                "title": title[:60],
                "detail": detail[:400],
                "arrowTo": next_lane,
                "arrowLabel": label,
            }
        )

    add(
        "user",
        "start",
        "User Starts",
        "The user opens the application and submits the requested input.",
        "frontend",
        "User action",
    )

    add(
        "frontend",
        "process",
        "Capture Input",
        f"{frontend_name} collects and validates the basic client-side input"
        + (f" using {frontend_tech}." if frontend_tech else "."),
        "frontend",
        "Submit",
    )

    add(
        "frontend",
        "process",
        "Send Request",
        f"{frontend_name} sends the request to {backend_name} through the application's API.",
        "backend",
        "HTTP/API",
    )

    add(
        "backend",
        "decision",
        "Validate Request",
        f"{backend_name} validates the payload, required fields, authentication and business rules.",
        "backend",
        "Valid",
    )

    if has_external_api:
        add(
            "backend",
            "process",
            "Call External API",
            "The backend sends required data to an external service and receives the service response.",
            "backend",
            "External response",
        )

    if has_ai:
        add(
            "ai",
            "process",
            "Run AI Logic",
            "The AI/model layer processes the prepared input and returns the generated or predicted result.",
            "backend",
            "AI result",
        )

    if has_db:
        add(
            "database",
            "process",
            "Read or Write Data",
            f"{database_name} stores or retrieves the application state required for the request.",
            "backend",
            "DB result",
        )

    add(
        "backend",
        "process",
        "Build Response",
        f"{backend_name} combines the processed result and prepares the API response"
        + (f" using {backend_tech}." if backend_tech else "."),
        "frontend",
        "JSON response",
    )

    add(
        "frontend",
        "process",
        "Update UI",
        f"{frontend_name} receives the response and updates the visible application state.",
        "output",
        "Render",
    )

    add(
        "output",
        "end",
        "Show Result",
        "The user sees the final result produced by the application.",
        None,
        None,
    )

    # Keep the diagram compact and stable.
    return steps[:14]


def generate_runtime_flow(
    project_name: str,
    context: Optional[str] = None,
) -> list:
    """
    Existing /api/runtime-flow entry point.

    Cache HIT -> no LLM -> Python generates flow.
    Cache MISS -> one blueprint LLM call -> Python generates flow.
    """
    blueprint = _get_or_create_blueprint(
        project_name,
        context,
    )

    flow = _blueprint_to_runtime_flow(blueprint)

    logger.info(
        "Runtime flow generated from cached blueprint: %d steps",
        len(flow),
    )

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
    project_name = _html_escape(
        getattr(blueprint, "project_name", "Project")
    )
    description = _html_escape(
        getattr(blueprint, "description", "")
    )
    problem = _html_escape(
        getattr(blueprint, "problem_statement", "")
    )

    architecture_count = len(_architecture_items(blueprint))
    tech_count = len(_bp_list(blueprint, "tech_stack"))
    workflow_count = len(_bp_list(blueprint, "workflow"))
    references_count = len(_bp_list(blueprint, "learning_references"))

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{project_name} — ArchiMind Preview</title>
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap');

:root {{
    --bg:#0b1118;
    --surface:#0f1923;
    --surface2:#13212e;
    --border:#223344;
    --accent:#06b6d4;
    --text:#e6f7f5;
    --muted:#8da1b5;
}}

* {{ box-sizing:border-box; }}

html,body {{
    width:100%;
    height:100%;
    margin:0;
}}

body {{
    background:var(--bg);
    color:var(--text);
    font-family:Inter,Arial,sans-serif;
}}

button,input {{ font:inherit; }}

.shell {{
    min-height:100%;
    display:grid;
    grid-template-columns:240px 1fr;
}}

.sidebar {{
    background:#091019;
    border-right:1px solid var(--border);
    padding:22px 14px;
    position:sticky;
    top:0;
    height:100vh;
}}

.logo {{
    font-size:20px;
    font-weight:700;
    margin:0 8px 28px;
}}

.logo span {{ color:var(--accent); }}

.nav button {{
    width:100%;
    border:0;
    background:transparent;
    color:var(--muted);
    text-align:left;
    padding:11px 12px;
    border-radius:9px;
    cursor:pointer;
    margin-bottom:5px;
}}

.nav button:hover,
.nav button.active {{
    background:var(--surface2);
    color:var(--text);
}}

.main {{
    padding:28px;
    max-width:1500px;
    width:100%;
    margin:auto;
}}

.top {{
    display:flex;
    justify-content:space-between;
    align-items:flex-start;
    gap:20px;
    margin-bottom:22px;
}}

h1,h2,h3,p {{ margin-top:0; }}

h1 {{
    font-size:30px;
    margin-bottom:8px;
}}

h2 {{
    font-size:21px;
    margin-bottom:15px;
}}

h3 {{
    font-size:16px;
    margin-bottom:7px;
}}

.muted {{ color:var(--muted); }}

.search {{
    background:var(--surface);
    border:1px solid var(--border);
    color:var(--text);
    border-radius:9px;
    padding:11px 13px;
    min-width:250px;
    outline:none;
}}

.search:focus {{ border-color:var(--accent); }}

.stats {{
    display:grid;
    grid-template-columns:repeat(4,1fr);
    gap:12px;
    margin-bottom:25px;
}}

.stat {{
    background:var(--surface);
    border:1px solid var(--border);
    border-radius:12px;
    padding:17px;
}}

.stat strong {{
    display:block;
    font-size:25px;
    margin-bottom:4px;
}}

.section {{
    margin-bottom:32px;
    scroll-margin-top:20px;
}}

.grid {{
    display:grid;
    grid-template-columns:repeat(auto-fit,minmax(270px,1fr));
    gap:13px;
}}

.card {{
    background:var(--surface);
    border:1px solid var(--border);
    border-radius:12px;
    padding:17px;
    transition:transform .18s,border-color .18s;
}}

.card:hover {{
    transform:translateY(-2px);
    border-color:#315267;
}}

.card-head {{
    display:flex;
    justify-content:space-between;
    gap:10px;
    align-items:center;
}}

.badge,.tag,.version {{
    display:inline-block;
    font-size:11px;
    border-radius:999px;
    padding:4px 8px;
    background:#142735;
    color:#9debf4;
}}

.tags {{
    display:flex;
    flex-wrap:wrap;
    gap:6px;
    margin:12px 0;
}}

.card ul,.workflow-step ul {{
    color:var(--muted);
    padding-left:18px;
    line-height:1.6;
}}

.workflow-step {{
    display:grid;
    grid-template-columns:42px 1fr;
    gap:14px;
    background:var(--surface);
    border:1px solid var(--border);
    border-radius:12px;
    padding:16px;
    margin-bottom:10px;
}}

.step-number {{
    width:36px;
    height:36px;
    display:grid;
    place-items:center;
    border-radius:50%;
    background:#12313b;
    color:var(--accent);
    font-weight:700;
}}

.problem {{
    background:var(--surface);
    border:1px solid var(--border);
    border-left:3px solid var(--accent);
    border-radius:10px;
    padding:17px;
    line-height:1.6;
}}

.hidden {{ display:none !important; }}

@media(max-width:850px) {{
    .shell {{ grid-template-columns:1fr; }}
    .sidebar {{
        height:auto;
        position:static;
        border-right:0;
        border-bottom:1px solid var(--border);
    }}
    .nav {{ display:flex; gap:6px; overflow:auto; }}
    .nav button {{ white-space:nowrap; }}
    .stats {{ grid-template-columns:repeat(2,1fr); }}
    .top {{ flex-direction:column; }}
    .search {{ width:100%; }}
}}

@media(max-width:500px) {{
    .main {{ padding:18px; }}
    .stats {{ grid-template-columns:1fr 1fr; }}
}}
</style>
</head>
<body>

<div class="shell">
    <aside class="sidebar">
        <div class="logo">Archi<span>Mind</span></div>
        <div class="nav">
            <button class="active" data-target="overview">Overview</button>
            <button data-target="architecture">Architecture</button>
            <button data-target="workflow">Workflow</button>
            <button data-target="stack">Tech Stack</button>
        </div>
    </aside>

    <main class="main">
        <header class="top">
            <div>
                <h1>{project_name}</h1>
                <p class="muted">{description}</p>
            </div>
            <input id="search" class="search" placeholder="Search preview..." />
        </header>

        <section id="overview" class="section">
            <div class="stats">
                <div class="stat">
                    <strong>{architecture_count}</strong>
                    <span class="muted">Components</span>
                </div>
                <div class="stat">
                    <strong>{tech_count}</strong>
                    <span class="muted">Technologies</span>
                </div>
                <div class="stat">
                    <strong>{workflow_count}</strong>
                    <span class="muted">Workflow Steps</span>
                </div>
                <div class="stat">
                    <strong>{references_count}</strong>
                    <span class="muted">References</span>
                </div>
            </div>

            <h2>Problem</h2>
            <div class="problem">{problem}</div>
        </section>

        <section id="architecture" class="section">
            <h2>System Architecture</h2>
            <div class="grid">
                {_render_architecture_cards(blueprint)}
            </div>
        </section>

        <section id="workflow" class="section">
            <h2>Workflow</h2>
            {_render_workflow(blueprint)}
        </section>

        <section id="stack" class="section">
            <h2>Technology Stack</h2>
            <div class="grid">
                {_render_tech_cards(blueprint)}
            </div>
        </section>
    </main>
</div>

<script>
(function () {{
    const buttons = document.querySelectorAll(".nav button");
    const search = document.querySelector("#search");
    const items = document.querySelectorAll(".searchable");

    function activateSection(id) {{
        const section = document.getElementById(id);
        if (section) {{
            section.scrollIntoView({{ behavior: "smooth", block: "start" }});
        }}
        buttons.forEach(function (button) {{
            button.classList.toggle(
                "active",
                button.dataset.target === id
            );
        }});
    }}

    buttons.forEach(function (button) {{
        button.addEventListener("click", function () {{
            activateSection(button.dataset.target);
        }});
    }});

    search.addEventListener("input", function () {{
        const query = search.value.toLowerCase().trim();

        items.forEach(function (item) {{
            const visible =
                !query ||
                item.textContent.toLowerCase().includes(query);

            item.classList.toggle("hidden", !visible);
        }});
    }});
}})();
</script>

</body>
</html>"""


def generate_ui_preview(
    project_name: str,
    context: Optional[str] = None,
) -> str:
    """
    Existing /api/preview entry point.

    Cache HIT -> no LLM -> Python HTML generation.
    Cache MISS -> one blueprint LLM call -> Python HTML generation.

    There is deliberately NO HTML-generation LLM call anymore.
    """
    blueprint = _get_or_create_blueprint(
        project_name,
        context,
    )

    html_output = _build_ui_preview_from_blueprint(blueprint)

    logger.info(
        "UI preview generated from cached blueprint: project=%s chars=%d",
        getattr(blueprint, "project_name", project_name),
        len(html_output),
    )

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
        logger.info("Blueprint cache cleared.")


def blueprint_cache_stats() -> Dict[str, Any]:
    with _cache_lock:
        _remove_expired_cache_entries()
        return {
            "entries": len(_blueprint_cache),
            "max_entries": CACHE_MAX_ENTRIES,
            "ttl_seconds": CACHE_TTL_SECONDS,
            "llm_model": OPENROUTER_FREE_MODEL,
            "llm_calls_for_runtime_flow": 0,
            "llm_calls_for_ui_preview": 0,
            "llm_calls_for_streaming_after_cache_hit": 0,
        }
