import json
import logging
from config import settings
from models import ProjectBlueprint, LearningReference
from typing import Optional, List

from langchain_groq import ChatGroq
from langchain_openai import ChatOpenAI          # used for OpenRouter (OpenAI-compatible)
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import StrOutputParser
from rag_pipeline import (
    run_rag_pipeline_sync,
    RAGResult,
    SourceDoc,
)
# Individual searchers imported for the search-only fallback
from rag_pipeline import (
    _search_arxiv,
    _search_semantic_scholar,
    _search_crossref,
    _search_core,
    _search_tavily,
    _search_github,
)

# json-repair: recovers truncated/malformed JSON (trailing commas, missing braces, etc.)
try:
    from json_repair import repair_json
    _JSON_REPAIR_AVAILABLE = True
except ImportError:
    _JSON_REPAIR_AVAILABLE = False
    logger.warning("json-repair not installed — install with: pip install json-repair")

logger = logging.getLogger(__name__)

MAX_RESPONSE_TOKENS = 8000  # raised from 2200 — blueprints can exceed 4k tokens easily

PREFERRED_CHAT_MODELS = [
    "llama-3.3-70b-versatile",
    "llama-3.1-8b-instant",
    "mixtral-8x7b-32768",
    "llama-2-70b-chat",
]

_cached_available_models: Optional[List[str]] = None

# OpenRouter endpoint (OpenAI-compatible)
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"

# Error substrings that specifically mean Groq is rate-limited / quota-exhausted.
# These trigger OpenRouter fallback rather than retrying another Groq model.
_GROQ_RATE_LIMIT_MARKERS = (
    "rate_limit_exceeded",
    "rate limit",
    "quota exceeded",
    "too many requests",
    "tokens per minute",
    "requests per minute",
    "tokens per day",
    "context_length_exceeded",     # Groq sometimes raises this on overload
    "503",
    "529",                          # Groq overload status
    "service unavailable",
    "overloaded",
)


# -------------------------------------------------------------------------------------
# Helpers (unchanged from original)
# -------------------------------------------------------------------------------------

def _extract_first_json_object(text: str) -> Optional[str]:
    """Best-effort extraction of the first complete JSON object from text."""
    start = text.find("{")
    if start == -1:
        return None

    depth = 0
    in_string = False
    escaped = False

    for idx in range(start, len(text)):
        ch = text[idx]

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
                return text[start:idx + 1]

    return None


def _is_retryable_model_error(error: Exception) -> bool:
    """Return True when we should retry with another model."""
    error_text = str(error).lower()
    retry_markers = [
        "model_decommissioned",
        "no longer supported",
        "decommissioned",
        "model not found",
        "does not exist",
        "invalid_request_error",
    ]
    return any(marker in error_text for marker in retry_markers)


def _is_groq_rate_limited(error: Exception) -> bool:
    """Return True when Groq is rate-limiting or quota-exhausted — triggers OpenRouter fallback."""
    error_text = str(error).lower()
    return any(marker in error_text for marker in _GROQ_RATE_LIMIT_MARKERS)


def _get_available_models() -> List[str]:
    """Fetch available Groq model IDs once and cache them via LangChain."""
    global _cached_available_models

    if _cached_available_models is not None:
        return _cached_available_models

    try:
        # Use a lightweight model just to query the model list via the Groq HTTP API
        import urllib.request as urllib_request
        import urllib.error as urllib_error

        req = urllib_request.Request(
            url="https://api.groq.com/openai/v1/models",
            headers={
                "Authorization": f"Bearer {settings.groq_api_key}",
                "Content-Type": "application/json",
            },
            method="GET",
        )
        with urllib_request.urlopen(req, timeout=10) as response:
            data = json.loads(response.read().decode("utf-8"))
            _cached_available_models = [m["id"] for m in data.get("data", []) if m.get("id")]
            logger.info("Detected %s available Groq models", len(_cached_available_models))
    except Exception as e:
        logger.warning("Could not fetch model list from Groq: %s", str(e))
        _cached_available_models = []

    return _cached_available_models


def _get_model_candidates() -> List[str]:
    """Build ordered model candidates from env + preferred defaults + discovered models."""
    configured_model = settings.groq_model.strip() if settings.groq_model else ""
    available_models = _get_available_models()

    candidates: List[str] = []

    if configured_model:
        candidates.append(configured_model)

    for model in PREFERRED_CHAT_MODELS:
        if model not in candidates:
            candidates.append(model)

    if available_models:
        filtered = [m for m in candidates if m in available_models]
        if filtered:
            return filtered
        return available_models

    return candidates


def _clean_json_str(raw: str) -> str:
    """Strip markdown fences and leading/trailing noise from a raw model response."""
    s = raw.strip()
    # Remove ```json ... ``` or ``` ... ``` fences
    if "```json" in s:
        s = s.split("```json", 1)[1]
        s = s.split("```", 1)[0] if "```" in s else s
    elif s.startswith("```"):
        s = s[3:]
        s = s.split("```", 1)[0] if "```" in s else s
    # Trim anything before the opening brace
    brace = s.find("{")
    if brace > 0:
        s = s[brace:]
    return s.strip()


def _parse_blueprint_response(response_text: str, problem_statement: str) -> ProjectBlueprint:
    """
    Parse and validate JSON blueprint from model output.
    Attempt order:
      1. Direct json.loads after fence-stripping
      2. _extract_first_json_object (brace-depth scan)
      3. json-repair (recovers truncated / malformed JSON)
    """
    json_str = _clean_json_str(response_text)

    # Attempt 1 — clean parse
    try:
        blueprint_data = json.loads(json_str)
        blueprint = ProjectBlueprint(**blueprint_data)
        logger.info("Blueprint parsed (direct) for: %s", problem_statement[:50])
        return blueprint
    except (json.JSONDecodeError, Exception):
        pass

    # Attempt 2 — brace-depth extraction
    extracted = _extract_first_json_object(response_text)
    if extracted:
        try:
            blueprint_data = json.loads(extracted)
            blueprint = ProjectBlueprint(**blueprint_data)
            logger.info("Blueprint parsed (extracted) for: %s", problem_statement[:50])
            return blueprint
        except (json.JSONDecodeError, Exception):
            pass

    # Attempt 3 — json-repair (handles truncation, trailing commas, missing braces)
    if _JSON_REPAIR_AVAILABLE:
        try:
            repaired = repair_json(json_str, return_objects=False)
            blueprint_data = json.loads(repaired)
            blueprint = ProjectBlueprint(**blueprint_data)
            logger.info("Blueprint parsed (repaired) for: %s", problem_statement[:50])
            return blueprint
        except Exception as e:
            logger.error("json-repair also failed: %s", e)

    logger.error("JSON parsing error — all 3 attempts failed")
    logger.error("Response text (first 500 chars): %s", response_text[:500])
    raise ValueError("Failed to parse blueprint JSON from Groq response")


# -------------------------------------------------------------------------------------
# LangChain chain factory
# -------------------------------------------------------------------------------------

def _get_chain(model: str, system_prompt: str = None):
    """Build a LangChain chain: ChatPromptTemplate | ChatGroq | StrOutputParser."""
    llm = ChatGroq(
        api_key=settings.groq_api_key,
        model_name=model,
        temperature=0.15,
        max_tokens=MAX_RESPONSE_TOKENS,
    )
    prompt = ChatPromptTemplate.from_messages([
        ("system", system_prompt or SYSTEM_PROMPT),
        ("human", "{user_message}"),
    ])
    return prompt | llm | StrOutputParser()


def _get_rag_chain(model: str):
    """Chain that uses the RAG-augmented system prompt."""
    llm = ChatGroq(
        api_key=settings.groq_api_key,
        model_name=model,
        temperature=0.15,
        max_tokens=MAX_RESPONSE_TOKENS,
    )
    prompt = ChatPromptTemplate.from_messages([
        ("system", RAG_SYSTEM_PROMPT),
        ("human", "{user_message}"),
    ])
    return prompt | llm | StrOutputParser()


# -------------------------------------------------------------------------------------
# OpenRouter chain factories  (identical interface to Groq chains)
# -------------------------------------------------------------------------------------

def _get_openrouter_chain(system_prompt: str = None):
    """
    Build a LangChain chain using OpenRouter as the backend.
    OpenRouter exposes an OpenAI-compatible API so we use ChatOpenAI.
    The model is set via settings.openrouter_model (default: free Llama tier).
    """
    if not settings.openrouter_api_key:
        raise RuntimeError("OPENROUTER_API_KEY not configured — cannot use OpenRouter fallback")

    llm = ChatOpenAI(
        api_key=settings.openrouter_api_key,
        base_url=OPENROUTER_BASE_URL,
        model=settings.openrouter_model,
        temperature=0.15,
        max_tokens=MAX_RESPONSE_TOKENS,
        default_headers={
            # Required by OpenRouter for analytics / rate-limit attribution
            "HTTP-Referer": "https://ai-system-architect.app",
            "X-Title":      "AI System Architect",
        },
    )
    prompt = ChatPromptTemplate.from_messages([
        ("system", system_prompt or SYSTEM_PROMPT),
        ("human", "{user_message}"),
    ])
    return prompt | llm | StrOutputParser()


def _get_openrouter_rag_chain():
    """OpenRouter chain with RAG system prompt."""
    return _get_openrouter_chain(system_prompt=RAG_SYSTEM_PROMPT)


def _invoke_openrouter(user_message: str, use_rag_prompt: bool = False) -> str:
    """Run a single completion via OpenRouter. Raises on any error."""
    logger.info("Falling back to OpenRouter model: %s", settings.openrouter_model)
    chain = _get_openrouter_rag_chain() if use_rag_prompt else _get_openrouter_chain()
    result = chain.invoke({"user_message": user_message})
    logger.info("OpenRouter completion succeeded")
    return result


# -------------------------------------------------------------------------------------
# Internal completion runners
# -------------------------------------------------------------------------------------

def _run_non_stream_completion(user_message: str, use_rag_prompt: bool = False) -> str:
    """
    Run a non-streaming completion with two-stage fallback:
      Stage 1 — Try each Groq model in candidate order.
      Stage 2 — If ALL Groq models fail with rate-limit / quota / overload errors,
                fall back to OpenRouter (if configured and enabled).
    Any non-rate-limit error (bad request, auth error, etc.) raises immediately.
    """
    last_error:          Optional[Exception] = None
    groq_rate_limited                        = False   # True once any Groq model hits rate-limit

    model_candidates = _get_model_candidates()
    logger.info("Groq model candidates: %s", model_candidates)

    # ── Stage 1: Groq ────────────────────────────────────────────────────────
    for model in model_candidates:
        try:
            logger.info("Trying Groq model: %s", model)
            chain  = _get_rag_chain(model) if use_rag_prompt else _get_chain(model)
            result = chain.invoke({"user_message": user_message})
            logger.info("Groq succeeded with model: %s", model)
            return result

        except Exception as e:
            last_error = e
            if _is_groq_rate_limited(e):
                logger.warning(
                    "Groq model %s rate-limited/quota: %s — trying next Groq model",
                    model, str(e),
                )
                groq_rate_limited = True
                continue   # try next Groq model before giving up on Groq
            elif _is_retryable_model_error(e):
                logger.warning("Groq model %s decommissioned: %s", model, str(e))
                continue
            else:
                # Hard error (auth, bad request, etc.) — do not retry
                logger.error("Groq model %s hard error: %s", model, str(e))
                raise

    # ── Stage 2: OpenRouter fallback ─────────────────────────────────────────
    if groq_rate_limited and settings.openrouter_enabled and settings.openrouter_api_key:
        logger.warning(
            "All Groq models exhausted (rate-limit/quota). "
            "Falling back to OpenRouter: %s", settings.openrouter_model,
        )
        try:
            return _invoke_openrouter(user_message, use_rag_prompt=use_rag_prompt)
        except Exception as e:
            logger.error("OpenRouter fallback also failed: %s", str(e))
            raise RuntimeError(
                f"Both Groq (rate-limited) and OpenRouter failed. "
                f"OpenRouter error: {e}"
            ) from e

    raise RuntimeError(
        f"No working Groq model found. Last error: {str(last_error) if last_error else 'Unknown error'}"
    )


def _run_stream_completion(user_message: str):
    """
    Run a streaming completion with Groq→OpenRouter fallback.
    If all Groq models are rate-limited, OpenRouter is called non-streaming
    and its full response is yielded as a single chunk (transparent to callers).
    """
    last_error:       Optional[Exception] = None
    groq_rate_limited                     = False

    # ── Stage 1: Groq streaming ──────────────────────────────────────────────
    for model in _get_model_candidates():
        try:
            logger.info("Trying Groq streaming model: %s", model)
            chain = _get_chain(model)
            return chain.stream({"user_message": user_message})
        except Exception as e:
            last_error = e
            if _is_groq_rate_limited(e):
                logger.warning("Groq stream model %s rate-limited: %s", model, str(e))
                groq_rate_limited = True
                continue
            elif _is_retryable_model_error(e):
                continue
            else:
                raise

    # ── Stage 2: OpenRouter non-streaming → yield as single chunk ────────────
    if groq_rate_limited and settings.openrouter_enabled and settings.openrouter_api_key:
        logger.warning("Groq streaming exhausted — falling back to OpenRouter (non-streaming)")
        try:
            full_text = _invoke_openrouter(user_message, use_rag_prompt=False)
            # Yield as an iterable with a single chunk — callers iterate over us
            return iter([full_text])
        except Exception as e:
            logger.error("OpenRouter stream fallback failed: %s", e)
            raise RuntimeError(f"Groq rate-limited and OpenRouter failed: {e}") from e

    raise RuntimeError(
        f"No working streaming model found. Last error: {str(last_error) if last_error else 'Unknown error'}"
    )


# -------------------------------------------------------------------------------------
# RAG helpers
# -------------------------------------------------------------------------------------

# Maps source → (ref_type, difficulty) for learning_references
SOURCE_TYPE_MAP = {
    "arxiv":            ("Research Paper",  "Advanced"),
    "semantic_scholar": ("Research Paper",  "Advanced"),
    "crossref":         ("Research Paper",  "Intermediate"),
    "core":             ("Research Paper",  "Advanced"),
    "tavily":           ("Guide",           "Beginner"),
    "github":           ("Guide",           "Intermediate"),
}

# Official/documentation domains get bumped to Documentation type
DOC_DOMAIN_HINTS = [
    "docs.", "documentation", "readthedocs", "developer.", "/api/",
    "/reference/", "wiki.", "man.", "guide.", "learn.",
]


def _infer_ref_type(doc: SourceDoc) -> tuple:
    """Return (ref_type, difficulty) with doc_type + URL heuristics applied."""
    base_type, difficulty = SOURCE_TYPE_MAP.get(doc.source, ("Guide", "Intermediate"))

    # Tavily results that look like official docs → Documentation
    if doc.source == "tavily" and hasattr(doc, "doc_type"):
        if doc.doc_type == "documentation":
            return ("Documentation", "Intermediate")

    # URL-based hint for any source
    url_lower = (doc.url or "").lower()
    if any(hint in url_lower for hint in DOC_DOMAIN_HINTS):
        return ("Documentation", "Intermediate")

    return (base_type, difficulty)


def _sources_to_learning_references(sources: List[SourceDoc]) -> List[dict]:
    """
    Convert ALL RAG SourceDoc objects → learning_references dicts.
    Sources are sorted: papers first, then docs, then repos/web.
    All sources are always included regardless of whether they were sent to the LLM.
    """
    # Sort: priority descending (papers=10, docs bumped, github=4)
    sorted_sources = sorted(sources, key=lambda d: -getattr(d, "priority", 5))

    refs = []
    seen: set = set()
    for doc in sorted_sources:
        if not doc.url or doc.url in seen:
            continue
        seen.add(doc.url)

        ref_type, difficulty = _infer_ref_type(doc)

        # Build a rich title: include authors + year if available
        title = doc.title or doc.url
        meta_parts = []
        if hasattr(doc, "authors") and doc.authors:
            meta_parts.append(doc.authors[0] + (" et al." if len(doc.authors) > 1 else ""))
        if hasattr(doc, "year") and doc.year:
            meta_parts.append(doc.year)
        if meta_parts:
            title = f"{title} ({', '.join(meta_parts)})"

        refs.append({
            "title":      title,
            "url":        doc.url,
            "type":       ref_type,
            "difficulty": difficulty,
        })
    return refs


def _build_user_message(problem_statement: str, context: Optional[str], rag_context: str = "") -> str:
    parts = [f"Problem Statement: {problem_statement}"]
    if context and context.strip() and context.strip().lower() != "string":
        parts.append(f"Additional Context: {context.strip()}")
    if rag_context:
        parts.append(
            "\n--- RESEARCH CONTEXT ---\n"
            "The following excerpts are from real research papers, documentation, and repositories "
            "(Semantic Scholar, arXiv, CrossRef, CORE, Tavily, GitHub). "
            "Each excerpt begins with [SOURCE] Title (Year) — Author.\n\n"
            f"{rag_context}\n--- END RESEARCH CONTEXT ---"
        )
    parts.append("Audience: Beginner developer who needs step-by-step implementation clarity.")
    return "\n".join(parts)


# -------------------------------------------------------------------------------------
# Search-only fallback  (no embed/FAISS — just collect source links)
# -------------------------------------------------------------------------------------

def _search_only_fallback(
    query: str,
    tavily_api_key: str = "",
    core_api_key:   str = "",
    github_token:   str = "",
) -> RAGResult:
    """
    Lightweight fallback: run all 6 searchers concurrently, return their links
    as sources without embedding or retrieval.  Used when the full RAG pipeline
    crashes so learning_references are always populated.
    """
    import asyncio
    import concurrent.futures
    import httpx

    async def _run():
        async with httpx.AsyncClient(
            headers={"User-Agent": "AISystemArchitect/2.0 search-fallback"},
            follow_redirects=True,
        ) as client:
            results = await asyncio.gather(
                _search_arxiv(query, client),
                _search_semantic_scholar(query, client),
                _search_crossref(query, client),
                _search_core(query, client, core_api_key),
                _search_tavily(query, client, tavily_api_key),
                _search_github(query, client, github_token),
                return_exceptions=True,
            )
        docs: List[SourceDoc] = []
        seen: set = set()
        for r in results:
            if isinstance(r, list):
                for doc in r:
                    if doc.url and doc.url not in seen:
                        seen.add(doc.url)
                        docs.append(doc)
        return docs

    def _run_in_thread():
        return asyncio.run(_run())

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        sources = pool.submit(_run_in_thread).result(timeout=30)

    return RAGResult(context="", sources=sources, used_rag=False)


# -------------------------------------------------------------------------------------
# Public API (same signatures as original — main.py stays untouched)
# -------------------------------------------------------------------------------------

def generate_blueprint(problem_statement: str, context: Optional[str] = None) -> ProjectBlueprint:
    """
    Generate a project blueprint using RAG pipeline + LangChain + Groq.

    Pipeline:
      1. Run RAG (Semantic Scholar, arXiv, CrossRef, CORE, Tavily, GitHub)
      2. If RAG found reliable sources → inject top-k chunks into prompt
      3. If RAG found < MIN_RELIABLE_SOURCES → fall back to prompt-only
      4. Merge RAG source URLs into learning_references of the returned blueprint
    """
    # ── Step 1: RAG ──────────────────────────────────────────────────────
    rag_result = RAGResult(context="", sources=[], used_rag=False)
    if settings.rag_enabled:
        try:
            logger.info("Running RAG pipeline for: %s", problem_statement[:60])
            rag_result = run_rag_pipeline_sync(
                query=problem_statement,
                tavily_api_key=settings.tavily_api_key,
                core_api_key=settings.core_api_key,
                github_token=settings.github_token,
            )
            if rag_result.used_rag:
                logger.info(
                    "RAG succeeded: %d sources, %d chunks",
                    rag_result.source_count, rag_result.chunk_count,
                )
            else:
                logger.info("RAG fell back to prompt-only (insufficient sources)")
        except Exception as e:
            logger.warning("RAG pipeline error (continuing without RAG): %s", e)
            # Full pipeline crashed — run lightweight search-only fallback
            # so we still collect source links for learning_references
            try:
                rag_result = _search_only_fallback(
                    problem_statement,
                    tavily_api_key=settings.tavily_api_key,
                    core_api_key=settings.core_api_key,
                    github_token=settings.github_token,
                )
                logger.info(
                    "Search-only fallback collected %d source links",
                    len(rag_result.sources),
                )
            except Exception as e2:
                logger.warning("Search-only fallback also failed: %s", e2)

    # ── Step 2: build prompt ─────────────────────────────────────────────
    try:
        user_message = _build_user_message(
            problem_statement, context,
            rag_context=rag_result.context if rag_result.used_rag else "",
        )

        response_text = _run_non_stream_completion(
            user_message,
            use_rag_prompt=rag_result.used_rag,
        )
        if not response_text:
            raise ValueError("Model returned an empty response")

        try:
            blueprint = _parse_blueprint_response(response_text, problem_statement)
        except ValueError:
            retry_message = (
                user_message
                + "\n\nIMPORTANT: Return ONLY valid JSON object. "
                  "No markdown, no explanations, no trailing text. "
                  "If content is long, shorten sentences but keep schema complete."
            )
            retry_text = _run_non_stream_completion(
                retry_message,
                use_rag_prompt=rag_result.used_rag,
            )
            if not retry_text:
                raise ValueError("Model returned an empty response on retry")
            blueprint = _parse_blueprint_response(retry_text, problem_statement)

        # ── Step 3: always merge ALL discovered sources into learning_references
        # This runs whether RAG was used for context or not — every fetched link
        # (arXiv, Semantic Scholar, CrossRef, CORE, Tavily, GitHub) is surfaced.
        if rag_result.sources:
            rag_refs = _sources_to_learning_references(rag_result.sources)
            existing_urls = {r.url for r in (blueprint.learning_references or [])}
            new_refs_data = [r for r in rag_refs if r["url"] not in existing_urls]
            if new_refs_data:
                from models import LearningReference
                new_refs = [LearningReference(**r) for r in new_refs_data]
                # Place RAG sources FIRST so they appear at the top of the section
                blueprint.learning_references = new_refs + list(blueprint.learning_references or [])
                logger.info(
                    "Merged %d source links into learning_references (rag_used=%s)",
                    len(new_refs), rag_result.used_rag,
                )
        else:
            logger.info("No RAG sources collected — learning_references unchanged")

        return blueprint

    except Exception as e:
        logger.error("Error generating blueprint: %s", str(e))
        raise


def generate_streaming_blueprint(problem_statement: str, context: Optional[str] = None):
    """Generate blueprint with streaming (prompt-only — RAG runs separately in /api/generate)."""
    user_message = _build_user_message(problem_statement, context)
    try:
        stream = _run_stream_completion(user_message)
        for chunk in stream:
            if chunk:
                yield chunk
    except Exception as e:
        logger.error("Error in streaming blueprint: %s", str(e))
        raise


# -------------------------------------------------------------------------------------
# System prompt (unchanged)
# -------------------------------------------------------------------------------------

SYSTEM_PROMPT = """You are an expert AI system architect. Output ONLY valid JSON (no markdown) matching this schema exactly:
{{
  "project_name": string,
  "description": string,
  "problem_statement": string,
  "system_architecture": [{{"name": string, "type": "frontend|backend|database|external_api|infrastructure", "description": string, "responsibilities": [string], "technologies": [string]}}],
  "tech_stack": [{{"name": string, "category": string, "reason": string, "version": string|null, "languages": [string], "frameworks": [string], "modules": [string]}}],
  "workflow": [{{"step_number": number, "title": string, "description": string, "components_involved": [string], "key_actions": [string]}}],
  "prerequisites": [{{"category": string, "items": [string]}}],
  "solution_approaches": [{{"name": string, "description": string, "pros": [string], "cons": [string], "complexity": "Simple|Medium|Complex", "estimated_time": string, "best_for": string}}],
  "real_world_examples": [{{"title": string, "description": string, "company": string, "link": string|null, "lessons_learned": [string]}}],
  "learning_references": [{{"title": string, "url": string, "type": "Tutorial|Documentation|Guide|Course|Blog", "difficulty": "Beginner|Intermediate|Advanced"}}],
  "timeline": {{"phase_name": "duration"}},
  "estimated_budget": string|null,
  "next_steps": [string]
}}
Rules:
- Target audience is beginners. Use simple language and practical explanations.
- Keep medium detail level: complete but concise.
- Workflow must be end-to-end with 7-10 steps, step_number strictly sequential from 1.
- Each workflow step must include:
  - 2+ components_involved
  - 3-4 key_actions
  - clear plain-language phase outcome.
- Tech stack should be practical (8-12 items) and include frontend, backend, database, infrastructure, APIs, testing, CI/CD, and security.
- For every tech_stack item, include languages, frameworks, and modules arrays with concrete names.
- timeline should represent realistic phases from setup to launch.
- next_steps must be actionable and beginner-friendly.
- Keep output concise, implementation-ready, and easy to read."""


# -------------------------------------------------------------------------------------
# RAG-augmented system prompt (used when research context is available)
# -------------------------------------------------------------------------------------

RAG_SYSTEM_PROMPT = """You are an expert AI system architect with access to real research papers, official documentation, and open-source repositories retrieved via MMR search. Output ONLY valid JSON (no markdown) matching this schema exactly:
{{
  "project_name": string,
  "description": string,
  "problem_statement": string,
  "system_architecture": [{{"name": string, "type": "frontend|backend|database|external_api|infrastructure", "description": string, "responsibilities": [string], "technologies": [string]}}],
  "tech_stack": [{{"name": string, "category": string, "reason": string, "version": string|null, "languages": [string], "frameworks": [string], "modules": [string]}}],
  "workflow": [{{"step_number": number, "title": string, "description": string, "components_involved": [string], "key_actions": [string]}}],
  "prerequisites": [{{"category": string, "items": [string]}}],
  "solution_approaches": [{{"name": string, "description": string, "pros": [string], "cons": [string], "complexity": "Simple|Medium|Complex", "estimated_time": string, "best_for": string}}],
  "real_world_examples": [{{"title": string, "description": string, "company": string, "link": string|null, "lessons_learned": [string]}}],
  "learning_references": [{{"title": string, "url": string, "type": "Tutorial|Documentation|Guide|Course|Blog|Research Paper", "difficulty": "Beginner|Intermediate|Advanced"}}],
  "timeline": {{"phase_name": "duration"}},
  "estimated_budget": string|null,
  "next_steps": [string]
}}
Research context rules:
- The RESEARCH CONTEXT in the user message contains MMR-selected excerpts from real papers, docs, and repos.
- Each excerpt is prefixed with [SOURCE] Title (Year) — Author.
- Prioritise insights from [ARXIV], [SEMANTIC_SCHOLAR], [CORE] papers over other sources.
- Use [TAVILY] documentation excerpts for official API/framework guidance.
- Use [GITHUB] README excerpts for real implementation patterns.
- Ground tech stack "reason" fields in what the research context says, not general knowledge.
- If a solution approach is supported by a paper, mention its key finding in the "description".

Blueprint rules:
- Target audience is beginners. Use simple language and practical explanations.
- Workflow must be end-to-end with 7-10 steps, step_number strictly sequential from 1.
- Each step: 2+ components_involved, 3-4 key_actions, clear phase outcome.
- Tech stack: 8-12 items covering frontend, backend, database, infra, APIs, testing, CI/CD, security.
- For every tech_stack item include languages, frameworks, and modules arrays with concrete names.
- timeline: realistic phases from setup to launch.
- next_steps: actionable and beginner-friendly.
- Keep output concise, implementation-ready, and easy to read."""


# -------------------------------------------------------------------------------------
# UI Preview prompt & function
# -------------------------------------------------------------------------------------

UI_PREVIEW_PROMPT = """You are a senior UI/UX engineer. Generate a FULLY INTERACTIVE, production-quality web app for this project.

Project: {project_name}
{extra_context}

Output ONLY raw HTML — no markdown, no triple-backtick fences, no explanation. A single complete HTML file.

INTERACTIVITY REQUIREMENTS (mandatory):
- The app must be fully functional and interactive using vanilla JS inside a <script> tag.
- All buttons, inputs, forms, and controls must actually work — clicking, adding, deleting, editing, toggling, filtering, etc.
- Use realistic in-memory data (arrays/objects in JS) to power the UI. Pre-populate with 4-6 sample records relevant to the project.
- Implement the core user flows of the app: e.g. for a todo app — add task, mark complete, delete, filter; for a dashboard — clickable nav sections, live stats; for a chat app — send message, receive reply, etc.
- All state changes must visually update the DOM immediately with no page reload.

UI/DESIGN REQUIREMENTS:
- Dark theme: background #0b1118, surface #0f1923, accent #06b6d4, text #e6f7f5, muted #64748b.
- Clean sidebar or top nav with active state highlighting on click.
- Main content area with cards, lists, or tables showing real data.
- Smooth CSS transitions on interactions (hover, active, open/close).
- Import one Google Font via @import in the <style> tag.
- Responsive layout using CSS flexbox or grid.
- html and body fill 100% height/width, overflow hidden on outer shell.

TECHNICAL REQUIREMENTS:
- All CSS in a single <style> block, all JS in a single <script> block at the bottom of <body>.
- No external libraries (no jQuery, no React, no CDN imports).
- Use document.querySelector / addEventListener / createElement for all DOM manipulation.
- Keep JS clean and well-structured with named functions."""


def generate_ui_preview(project_name: str, context: Optional[str] = None) -> str:
    """Generate a self-contained HTML UI mockup for the given project using LangChain + Groq."""

    extra_context = ""
    if context and context.strip() and context.strip().lower() != "string":
        extra_context = f"Additional context: {context.strip()}"

    user_message = UI_PREVIEW_PROMPT.format(
        project_name=project_name,
        extra_context=extra_context,
    )

    last_error:       Optional[Exception] = None
    groq_rate_limited                     = False

    def _clean_html(raw: str) -> str:
        cleaned = raw
        for fence in ("```html", "```"):
            if fence in cleaned:
                cleaned = cleaned.split(fence, 1)[-1]
        return cleaned.replace("```", "").strip()

    # ── Stage 1: Groq ────────────────────────────────────────────────────────
    for model in _get_model_candidates():
        try:
            logger.info("Generating UI preview with Groq model: %s", model)
            llm = ChatGroq(
                api_key=settings.groq_api_key,
                model_name=model,
                temperature=0.4,
                max_tokens=4000,
            )
            result  = llm.invoke(user_message)
            raw     = result.content if hasattr(result, "content") else str(result)
            cleaned = _clean_html(raw)
            if not cleaned or "<" not in cleaned:
                raise ValueError("Model returned non-HTML output")
            logger.info("UI preview generated with Groq model: %s", model)
            return cleaned
        except Exception as e:
            last_error = e
            if _is_groq_rate_limited(e):
                logger.warning("Groq UI preview rate-limited (%s): %s", model, e)
                groq_rate_limited = True
                continue
            elif _is_retryable_model_error(e):
                continue
            else:
                raise

    # ── Stage 2: OpenRouter fallback ─────────────────────────────────────────
    if groq_rate_limited and settings.openrouter_enabled and settings.openrouter_api_key:
        try:
            logger.warning("UI preview falling back to OpenRouter: %s", settings.openrouter_model)
            llm = ChatOpenAI(
                api_key=settings.openrouter_api_key,
                base_url=OPENROUTER_BASE_URL,
                model=settings.openrouter_model,
                temperature=0.4,
                max_tokens=4000,
                default_headers={
                    "HTTP-Referer": "https://ai-system-architect.app",
                    "X-Title":      "AI System Architect",
                },
            )
            result  = llm.invoke(user_message)
            raw     = result.content if hasattr(result, "content") else str(result)
            cleaned = _clean_html(raw)
            if not cleaned or "<" not in cleaned:
                raise ValueError("OpenRouter returned non-HTML output")
            logger.info("UI preview generated via OpenRouter")
            return cleaned
        except Exception as e:
            logger.error("OpenRouter UI preview fallback failed: %s", e)
            raise RuntimeError(f"UI preview failed on both Groq and OpenRouter: {e}") from e

    raise RuntimeError(
        f"No working model for UI preview. Last error: {str(last_error) if last_error else 'Unknown'}"
    )


# -------------------------------------------------------------------------------------
# Runtime Flow prompt & function
# -------------------------------------------------------------------------------------

RUNTIME_FLOW_PROMPT = """You are a software architect. Generate a runtime execution flow for this application.

Project: {project_name}
{extra_context}

CRITICAL: Show how the app RUNS at runtime — the journey from a user action to the final output.
Do NOT describe the development process, SDLC, deployment steps, or how the blueprint was generated.

Example for a Todo App: User clicks "Add Task" → Frontend captures input → API call to backend → Backend validates → DB insert → Response to frontend → UI updates with new task.

Output ONLY a valid JSON array (no markdown, no fences). Each element:
{{
  "lane": "user|frontend|backend|ai|database|output",
  "type": "start|process|decision|end",
  "title": "short node label (max 6 words)",
  "detail": "one sentence explaining what happens at this step in runtime",
  "arrowTo": "lane id this step passes control to next (or null)",
  "arrowLabel": "short label for the arrow e.g. HTTP POST /api/todos (or null)"
}}

Rules:
- Always start with lane "user", type "start".
- Always end with lane "output", type "end".
- Include 8 to 14 steps total.
- Use "decision" type for conditional logic (auth check, validation, cache hit, etc.).
- Each step must be a real runtime event, not a development task.
- arrowTo should be the lane of the NEXT step (helps draw cross-lane arrows).
- Keep "title" very short — it appears inside a diagram node.
- "detail" should explain the actual runtime mechanics: what data moves, what function runs, what protocol is used.
- Use the actual tech stack of the project in the detail fields."""


def generate_runtime_flow(project_name: str, context: Optional[str] = None) -> list:
    """Generate runtime execution flow steps using LangChain + Groq."""

    extra_context = ""
    if context and context.strip() and context.strip().lower() != "string":
        extra_context = f"Tech/Context: {context.strip()}"

    user_message = RUNTIME_FLOW_PROMPT.format(
        project_name=project_name,
        extra_context=extra_context,
    )

    last_error:       Optional[Exception] = None
    groq_rate_limited                     = False

    valid_lanes = {"user", "frontend", "backend", "ai", "database", "output"}
    valid_types = {"start", "process", "decision", "end"}

    def _parse_and_sanitise(raw: str) -> list:
        cleaned = raw
        for fence in ("```json", "```"):
            if fence in cleaned:
                cleaned = cleaned.split(fence, 1)[-1]
        cleaned = cleaned.replace("```", "").strip()
        steps   = json.loads(cleaned)
        if not isinstance(steps, list) or not steps:
            raise ValueError("Expected a non-empty JSON array")
        for s in steps:
            s["lane"] = s.get("lane", "backend") if s.get("lane") in valid_lanes else "backend"
            s["type"] = s.get("type", "process") if s.get("type") in valid_types else "process"
            s.setdefault("arrowTo",    None)
            s.setdefault("arrowLabel", None)
            s.setdefault("detail",     s.get("title", ""))
        return steps

    def _make_groq_llm(model: str):
        return ChatGroq(
            api_key=settings.groq_api_key,
            model_name=model,
            temperature=0.2,
            max_tokens=2000,
        )

    # ── Stage 1: Groq ────────────────────────────────────────────────────────
    for model in _get_model_candidates():
        try:
            logger.info("Generating runtime flow with Groq model: %s", model)
            result = _make_groq_llm(model).invoke(user_message)
            raw    = result.content if hasattr(result, "content") else str(result)
            steps  = _parse_and_sanitise(raw)
            logger.info("Runtime flow generated: %d steps with Groq %s", len(steps), model)
            return steps
        except Exception as e:
            last_error = e
            if _is_groq_rate_limited(e):
                logger.warning("Groq flow rate-limited (%s): %s", model, e)
                groq_rate_limited = True
                continue
            elif _is_retryable_model_error(e):
                continue
            else:
                raise

    # ── Stage 2: OpenRouter fallback ─────────────────────────────────────────
    if groq_rate_limited and settings.openrouter_enabled and settings.openrouter_api_key:
        try:
            logger.warning("Runtime flow falling back to OpenRouter: %s", settings.openrouter_model)
            llm = ChatOpenAI(
                api_key=settings.openrouter_api_key,
                base_url=OPENROUTER_BASE_URL,
                model=settings.openrouter_model,
                temperature=0.2,
                max_tokens=2000,
                default_headers={
                    "HTTP-Referer": "https://ai-system-architect.app",
                    "X-Title":      "AI System Architect",
                },
            )
            result = llm.invoke(user_message)
            raw    = result.content if hasattr(result, "content") else str(result)
            steps  = _parse_and_sanitise(raw)
            logger.info("Runtime flow generated via OpenRouter: %d steps", len(steps))
            return steps
        except Exception as e:
            logger.error("OpenRouter flow fallback failed: %s", e)
            raise RuntimeError(f"Runtime flow failed on both Groq and OpenRouter: {e}") from e

    raise RuntimeError(
        f"No working model for runtime flow. Last error: {str(last_error) if last_error else 'Unknown'}"
    )