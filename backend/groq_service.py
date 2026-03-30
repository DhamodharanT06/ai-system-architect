import json
import logging
from config import settings
from models import ProjectBlueprint
from typing import Optional, List
from urllib import request as urllib_request
from urllib import error as urllib_error

try:
  from groq import Groq  # pyright: ignore[reportMissingImports]
except Exception:
  Groq = None

logger = logging.getLogger(__name__)

MAX_RESPONSE_TOKENS = 2200

# Groq client instance
_client = None


def get_groq_client():
  global _client
  if _client is not None:
    return _client

  if not settings.groq_api_key or settings.groq_api_key.strip() == "":
    logger.error("GROQ_API_KEY environment variable is not set or empty.")
    return None

  try:
    if Groq is None:
      logger.warning("Groq SDK import unavailable in this environment")
      return None
    _client = Groq(api_key=settings.groq_api_key)
    logger.info("Groq client initialized successfully")
    return _client
  except Exception as e:
    logger.exception("Error initializing Groq client: %s", str(e))
    return None

# Preferred models are attempted in order when auto-discovering a working model.
# These are actual Groq-hosted models (free tier available)
PREFERRED_CHAT_MODELS = [
  "llama-3.3-70b-versatile",
  "llama-3.1-8b-instant",
  "mixtral-8x7b-32768",
  "llama-2-70b-chat",
]

_cached_available_models: Optional[List[str]] = None


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


def _run_non_stream_completion_http(model: str, user_message: str) -> str:
  """Fallback: call Groq chat completions directly via HTTP."""
  if not settings.groq_api_key or settings.groq_api_key.strip() == "":
    raise RuntimeError("GROQ_API_KEY environment variable is not set or empty")

  payload = {
    "model": model,
    "max_tokens": MAX_RESPONSE_TOKENS,
    "temperature": 0.15,
    "messages": [
      {"role": "system", "content": SYSTEM_PROMPT},
      {"role": "user", "content": user_message},
    ],
  }

  req = urllib_request.Request(
    url="https://api.groq.com/openai/v1/chat/completions",
    data=json.dumps(payload).encode("utf-8"),
    headers={
      "Authorization": f"Bearer {settings.groq_api_key}",
      "Content-Type": "application/json",
    },
    method="POST",
  )

  try:
    with urllib_request.urlopen(req, timeout=60) as response:
      response_body = response.read().decode("utf-8")
      data = json.loads(response_body)
      content = data.get("choices", [{}])[0].get("message", {}).get("content", "")
      if not content:
        raise RuntimeError("Groq HTTP fallback returned empty content")
      return content
  except urllib_error.HTTPError as e:
    body = e.read().decode("utf-8", errors="ignore") if hasattr(e, "read") else ""
    raise RuntimeError(f"Groq HTTP fallback failed: {e.code} {body}") from e
  except urllib_error.URLError as e:
    raise RuntimeError(f"Groq HTTP fallback network error: {str(e)}") from e


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


def _get_available_models() -> List[str]:
  """Fetch available Groq model IDs once and cache them."""
  global _cached_available_models

  if _cached_available_models is not None:
    return _cached_available_models

  try:
    client = get_groq_client()
    if client is None:
      logger.warning("Groq client unavailable; skipping model discovery")
      _cached_available_models = []
      return _cached_available_models

    models_response = client.models.list()
    _cached_available_models = [m.id for m in models_response.data if getattr(m, "id", None)]
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

  # If API discovery succeeded, prefer only models that actually exist.
  if available_models:
    filtered = [m for m in candidates if m in available_models]
    if filtered:
      return filtered

    # Fallback to any available model as a last resort.
    return available_models

  return candidates


def _parse_blueprint_response(response_text: str, problem_statement: str) -> ProjectBlueprint:
  """Parse and validate JSON blueprint payload returned by the model."""
  try:
    if "```json" in response_text:
      json_str = response_text.split("```json", 1)[1].split("```", 1)[0].strip()
    elif "```" in response_text:
      json_str = response_text.split("```", 1)[1].split("```", 1)[0].strip()
    else:
      json_str = response_text

    try:
      blueprint_data = json.loads(json_str)
    except json.JSONDecodeError:
      extracted = _extract_first_json_object(response_text)
      if not extracted:
        raise
      blueprint_data = json.loads(extracted)

    blueprint = ProjectBlueprint(**blueprint_data)
    logger.info("Successfully generated blueprint for: %s", problem_statement[:50])
    return blueprint

  except json.JSONDecodeError as e:
    logger.error("JSON parsing error: %s", str(e))
    logger.error("Response text: %s", response_text)
    raise ValueError("Failed to parse blueprint JSON from Groq response")


def _run_non_stream_completion(user_message: str):
  """Run a non-streaming completion with automatic model fallback."""
  last_error: Optional[Exception] = None
  
  model_candidates = _get_model_candidates()
  logger.info("Model candidates to try: %s", model_candidates)

  for model in model_candidates:
    try:
      logger.info("Trying Groq model: %s", model)
      client = get_groq_client()
      if client is None:
        logger.warning("Groq SDK client unavailable; using direct HTTP fallback for model: %s", model)
        return _run_non_stream_completion_http(model, user_message)

      logger.info("Making API call to Groq with model: %s", model)
      message = client.chat.completions.create(
        model=model,
        max_tokens=MAX_RESPONSE_TOKENS,
        temperature=0.15,
        messages=[
          {"role": "system", "content": SYSTEM_PROMPT},
          {"role": "user", "content": user_message},
        ],
      )
      logger.info("API call succeeded with model: %s", model)
      return message
    except Exception as e:
      last_error = e
      logger.warning("Model %s failed: %s", model, str(e), exc_info=True)
      if not _is_retryable_model_error(e):
        raise

  raise RuntimeError(
    f"No working Groq model found. Last error: {str(last_error) if last_error else 'Unknown error'}"
  )


def _run_stream_completion(user_message: str):
  """Run a streaming completion with automatic model fallback."""
  last_error: Optional[Exception] = None

  for model in _get_model_candidates():
    try:
      logger.info("Trying Groq streaming model: %s", model)
      client = get_groq_client()
      if client is None:
        raise RuntimeError("Groq client not initialized in this environment")

      return client.chat.completions.create(
        model=model,
        max_tokens=MAX_RESPONSE_TOKENS,
        temperature=0.15,
        messages=[
          {"role": "system", "content": SYSTEM_PROMPT},
          {"role": "user", "content": user_message},
        ],
        stream=True,
      )
    except Exception as e:
      last_error = e
      logger.warning("Streaming model %s failed: %s", model, str(e))
      if not _is_retryable_model_error(e):
        raise

  raise RuntimeError(
    f"No working Groq streaming model found. Last error: {str(last_error) if last_error else 'Unknown error'}"
  )

SYSTEM_PROMPT = """You are an expert AI system architect. Output ONLY valid JSON (no markdown) matching this schema exactly:
{
  "project_name": string,
  "description": string,
  "problem_statement": string,
  "system_architecture": [{"name": string, "type": "frontend|backend|database|external_api|infrastructure", "description": string, "responsibilities": [string], "technologies": [string]}],
  "tech_stack": [{"name": string, "category": string, "reason": string, "version": string|null, "languages": [string], "frameworks": [string], "modules": [string]}],
  "workflow": [{"step_number": number, "title": string, "description": string, "components_involved": [string], "key_actions": [string]}],
  "prerequisites": [{"category": string, "items": [string]}],
  "solution_approaches": [{"name": string, "description": string, "pros": [string], "cons": [string], "complexity": "Simple|Medium|Complex", "estimated_time": string, "best_for": string}],
  "real_world_examples": [{"title": string, "description": string, "company": string, "link": string|null, "lessons_learned": [string]}],
  "learning_references": [{"title": string, "url": string, "type": "Tutorial|Documentation|Guide|Course|Blog", "difficulty": "Beginner|Intermediate|Advanced"}],
  "timeline": {"phase_name": "duration"},
  "estimated_budget": string|null,
  "next_steps": [string]
}
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


def generate_blueprint(problem_statement: str, context: Optional[str] = None) -> ProjectBlueprint:
    """Generate a project blueprint using Groq API"""
    
    try:
        user_message = f"Problem Statement: {problem_statement}\nAudience: Beginner developer who needs step-by-step implementation clarity."

        if context and context.strip() and context.strip().lower() != "string":
          user_message += f"\nAdditional Context: {context.strip()}"
        
        message = _run_non_stream_completion(user_message)
        if isinstance(message, str):
          response_text = message
        else:
          response_text = message.choices[0].message.content or ""
        if not response_text:
          raise ValueError("Model returned an empty response")

        try:
          return _parse_blueprint_response(response_text, problem_statement)
        except ValueError:
          # One auto-retry with stricter formatting constraints for malformed JSON.
          retry_message = (
            user_message
            + "\n\nIMPORTANT: Return ONLY valid JSON object. "
              "No markdown, no explanations, no trailing text. "
              "If content is long, shorten sentences but keep schema complete."
          )
          retry = _run_non_stream_completion(retry_message)
          retry_text = retry if isinstance(retry, str) else (retry.choices[0].message.content or "")
          if not retry_text:
            raise ValueError("Model returned an empty response on retry")
          return _parse_blueprint_response(retry_text, problem_statement)
            
    except Exception as e:
        logger.error(f"Error generating blueprint: {str(e)}")
        raise


def generate_streaming_blueprint(problem_statement: str, context: Optional[str] = None):
    """Generate blueprint with streaming for real-time frontend updates"""
    
    user_message = f"Problem Statement: {problem_statement}\nAudience: Beginner developer who needs step-by-step implementation clarity."

    if context and context.strip() and context.strip().lower() != "string":
      user_message += f"\nAdditional Context: {context.strip()}"
    
    try:
        stream = _run_stream_completion(user_message)
        
        for chunk in stream:
          if chunk.choices and chunk.choices[0].delta.content is not None:
            yield chunk.choices[0].delta.content
                
    except Exception as e:
        logger.error(f"Error in streaming blueprint: {str(e)}")
        raise
