import json
import logging
from config import settings
from models import ProjectBlueprint
from typing import Optional, List
from groq import Groq

logger = logging.getLogger(__name__)

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

    blueprint_data = json.loads(json_str)
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
        raise RuntimeError("Groq client not initialized in this environment")

      logger.info("Making API call to Groq with model: %s", model)
      message = client.chat.completions.create(
        model=model,
        max_tokens=4000,
        temperature=0.2,
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
        max_tokens=4000,
        temperature=0.2,
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

SYSTEM_PROMPT = """You are an expert AI System Architect and technical mentor. Your role is to help developers, startups, and hackathon participants generate comprehensive project blueprints.

When given a problem statement or project idea, you must generate a detailed JSON response that includes:

1. **Project Name**: A catchy, memorable name
2. **Description**: Clear overview of the project
3. **System Architecture**: Components (Frontend, Backend, Database, External APIs, Infrastructure)
4. **Tech Stack**: Technology choices with reasons
5. **Workflow**: Step-by-step process flows
6. **Prerequisites**: Knowledge, tools, and infrastructure needed
7. **Solution Approaches**: Multiple ways to solve the problem (at least 2-3)
8. **Real-World Examples**: Similar projects or implementations
9. **Learning References**: Tutorials, docs, courses
10. **Timeline**: Estimated development phases
11. **Budget**: Estimated costs (if applicable)
12. **Next Steps**: Action items to get started

Return ONLY valid JSON in this exact format:
{
  "project_name": "string",
  "description": "string",
  "problem_statement": "string",
  "system_architecture": [
    {
      "name": "string",
      "type": "frontend|backend|database|external_api|infrastructure",
      "description": "string",
      "responsibilities": ["string"],
      "technologies": ["string"]
    }
  ],
  "tech_stack": [
    {
      "name": "string",
      "category": "string",
      "reason": "string",
      "version": "string or null"
    }
  ],
  "workflow": [
    {
      "step_number": 1,
      "title": "string",
      "description": "string",
      "components_involved": ["string"],
      "key_actions": ["string"]
    }
  ],
  "prerequisites": [
    {
      "category": "string",
      "items": ["string"]
    }
  ],
  "solution_approaches": [
    {
      "name": "string",
      "description": "string",
      "pros": ["string"],
      "cons": ["string"],
      "complexity": "Simple|Medium|Complex",
      "estimated_time": "string",
      "best_for": "string"
    }
  ],
  "real_world_examples": [
    {
      "title": "string",
      "description": "string",
      "company": "string",
      "link": "string or null",
      "lessons_learned": ["string"]
    }
  ],
  "learning_references": [
    {
      "title": "string",
      "url": "string",
      "type": "Tutorial|Documentation|Guide|Course|Blog",
      "difficulty": "Beginner|Intermediate|Advanced"
    }
  ],
  "timeline": {
    "phase_name": "duration"
  },
  "estimated_budget": "string or null",
  "next_steps": ["string"]
}

Be comprehensive, practical, and consider production-ready solutions. Think like an experienced architect."""


def generate_blueprint(problem_statement: str, context: Optional[str] = None) -> ProjectBlueprint:
    """Generate a project blueprint using Groq API"""
    
    try:
        user_message = f"""Problem Statement: {problem_statement}"""
        
        if context:
            user_message += f"\n\nAdditional Context: {context}"
        
        message = _run_non_stream_completion(user_message)
        response_text = message.choices[0].message.content or ""
        if not response_text:
          raise ValueError("Model returned an empty response")

        return _parse_blueprint_response(response_text, problem_statement)
            
    except Exception as e:
        logger.error(f"Error generating blueprint: {str(e)}")
        raise


def generate_streaming_blueprint(problem_statement: str, context: Optional[str] = None):
    """Generate blueprint with streaming for real-time frontend updates"""
    
    user_message = f"""Problem Statement: {problem_statement}"""
    
    if context:
        user_message += f"\n\nAdditional Context: {context}"
    
    try:
        stream = _run_stream_completion(user_message)
        
        for chunk in stream:
          if chunk.choices and chunk.choices[0].delta.content is not None:
            yield chunk.choices[0].delta.content
                
    except Exception as e:
        logger.error(f"Error in streaming blueprint: {str(e)}")
        raise
