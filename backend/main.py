import logging
from fastapi import FastAPI, HTTPException, WebSocket
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
import json
from typing import Optional
import asyncio

from config import settings
from models import UserMessage, ChatResponse, ChatMessage, ProjectBlueprint
from groq_service import generate_blueprint, generate_streaming_blueprint

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

app = FastAPI(
    title="AI System Architect",
    description="Generate comprehensive project blueprints with AI",
    version="1.0.0"
)

# Configure CORS
allowed_origins = [
    settings.frontend_url,
    "https://ai-system-architect-ruby.vercel.app",
    "http://localhost:3000",
    "http://localhost:5173",
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

logger.info("CORS allowed origins: %s", allowed_origins)


@app.get("/")
async def root():
    """Root endpoint - API info"""
    return {
        "status": "running",
        "message": "AI System Architect API",
        "docs": "/docs",
        "endpoints": {
            "generate": "/api/generate",
            "health": "/api/health"
        }
    }


@app.get("/api/health")
async def health_check():
    """Health check endpoint"""
    return {
        "status": "healthy",
        "debug": settings.debug
    }


@app.get("/api/debug")
async def debug_settings():
    """Debug endpoint - check if environment variables are set correctly"""
    return {
        "frontend_url": settings.frontend_url,
        "backend_port": settings.backend_port,
        "debug": settings.debug,
        "groq_api_key_set": bool(settings.groq_api_key and settings.groq_api_key.strip() != ""),
        "groq_api_key_length": len(settings.groq_api_key) if settings.groq_api_key else 0,
        "groq_model": settings.groq_model if settings.groq_model else "(not configured)",
        "cors_allowed_origins": allowed_origins,
    }


@app.post("/api/generate")
async def generate_project_blueprint(request: UserMessage) -> ChatResponse:
    """
    Generate a comprehensive project blueprint from a problem statement.
    
    Returns a complete project blueprint including:
    - System architecture
    - Tech stack recommendations
    - Workflow documentation
    - Prerequisites
    - Multiple solution approaches
    - Real-world examples
    - Learning references
    """
    
    if not request.problem_statement or len(request.problem_statement.strip()) == 0:
        raise HTTPException(
            status_code=400,
            detail="Problem statement cannot be empty"
        )
    
    try:
        logger.info(f"Generating blueprint for: {request.problem_statement[:100]}")
        
        blueprint = generate_blueprint(
            problem_statement=request.problem_statement,
            context=request.context
        )
        
        response = ChatResponse(
            message=ChatMessage(
                role="assistant",
                content="Blueprint generated successfully!"
            ),
            blueprint=blueprint
        )
        
        logger.info(f"Blueprint generated successfully for project: {blueprint.project_name}")
        return response
        
    except ValueError as e:
        logger.error(f"Validation error: {str(e)}")
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.exception(f"ERROR generating blueprint (full traceback above): {str(e)}")
        raise HTTPException(
            status_code=500,
            detail=f"Error generating blueprint: {str(e)}"
        )


@app.get("/api/stream-generate")
async def stream_project_blueprint(
    problem_statement: str,
    context: Optional[str] = None
):
    """
    Generate a project blueprint with streaming response for real-time updates.
    
    This endpoint streams the JSON response as it's being generated,
    allowing the frontend to display content in real-time.
    """
    
    if not problem_statement or len(problem_statement.strip()) == 0:
        raise HTTPException(
            status_code=400,
            detail="Problem statement cannot be empty"
        )
    
    async def event_generator():
        try:
            logger.info(f"Starting streaming generation for: {problem_statement[:100]}")
            
            for chunk in generate_streaming_blueprint(
                problem_statement=problem_statement,
                context=context
            ):
                # Format as Server-Sent Events
                if chunk:
                    yield f"data: {json.dumps({'content': chunk})}\n\n"
                    await asyncio.sleep(0.01)  # Small delay to allow frontend updates
            
            logger.info("Streaming generation completed")
            
        except Exception as e:
            logger.error(f"Error in stream generator: {str(e)}")
            yield f"data: {json.dumps({'error': str(e)})}\n\n"
    
    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream"
    )


@app.get("/api/examples")
async def get_examples():
    """Get example project blueprints"""
    return {
        "examples": [
            {
                "title": "Build an E-commerce Platform",
                "description": "Create a complete online store with products, cart, and payments"
            },
            {
                "title": "Real-time Chat Application",
                "description": "Build a messaging app with real-time notifications"
            },
            {
                "title": "Fitness Tracking App",
                "description": "Create an app to track workouts and health metrics"
            }
        ]
    }


# Note: explicit OPTIONS route removed so CORSMiddleware handles preflight requests.


if __name__ == "__main__":
    import uvicorn
    
    logger.info(f"Starting AI System Architect API on port {settings.backend_port}")
    logger.info(f"Debug mode: {settings.debug}")
    
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=settings.backend_port,
        reload=settings.debug
    )
