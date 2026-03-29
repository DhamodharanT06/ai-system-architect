# Developer Guide

Guide for extending and customizing AI System Architect.

## Project Structure Deep Dive

### Backend Structure

```
backend/
├── main.py              # FastAPI application and routes
├── config.py            # Configuration management
├── models.py            # Pydantic data models
├── groq_service.py      # Groq API integration
├── requirements.txt     # Python dependencies
├── Dockerfile           # Docker image definition
└── .env.example         # Environment template
```

### Frontend Structure

```
frontend/
├── src/
│   ├── main.jsx         # React entry point
│   ├── App.jsx          # Main App component
│   ├── index.css        # Global styles
│   ├── services/
│   │   └── api.js       # API client service
│   └── components/
│       ├── ChatMessage.jsx       # Message component
│       ├── ChatMessage.css
│       ├── BlueprintDisplay.jsx  # Blueprint display
│       ├── BlueprintDisplay.css
│       ├── Sidebar.jsx           # Navigation sidebar
│       └── Sidebar.css
├── index.html           # HTML template
├── vite.config.js       # Vite build config
├── package.json         # NPM packages
└── Dockerfile           # Docker image
```

## Adding New Features

### 1. Adding a New API Endpoint

**File**: `backend/main.py`

```python
@app.post("/api/refine-blueprint")
async def refine_blueprint(request: RefinementRequest):
    """Refine existing blueprint based on feedback"""
    try:
        # Your implementation
        refined_blueprint = refine_with_groq(
            original_blueprint=request.blueprint,
            feedback=request.feedback
        )
        return ChatResponse(
            message=ChatMessage(
                role="assistant",
                content="Blueprint refined successfully!"
            ),
            blueprint=refined_blueprint
        )
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Error refining blueprint: {str(e)}"
        )
```

### 2. Adding a New React Component

**File**: `frontend/src/components/NewComponent.jsx`

```jsx
import React from 'react';
import { motion } from 'framer-motion';
import './NewComponent.css';

function NewComponent({ data }) {
  return (
    <motion.div
      className="new-component"
      initial={{ opacity: 0, y: 10 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.3 }}
    >
      {/* Component content */}
    </motion.div>
  );
}

export default NewComponent;
```

**Usage in App.jsx**:

```jsx
import NewComponent from './components/NewComponent';

function App() {
  return (
    <div>
      <NewComponent data={yourData} />
    </div>
  );
}
```

### 3. Adding Database Support

**Install dependencies**:

```bash
pip install sqlalchemy psycopg2-binary alembic
```

**Create models** (`backend/database.py`):

```python
from sqlalchemy import create_engine, Column, String, DateTime
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
import os
from datetime import datetime

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql://user:password@localhost/ai_architect"
)

engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

class SavedBlueprint(Base):
    __tablename__ = "saved_blueprints"

    id = Column(String, primary_key=True)
    user_id = Column(String)
    project_name = Column(String)
    blueprint_json = Column(String)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

Base.metadata.create_all(bind=engine)
```

**Add API endpoint** (`backend/main.py`):

```python
from database import SessionLocal, SavedBlueprint
import json
from uuid import uuid4

@app.post("/api/save-blueprint")
async def save_blueprint(request: ProjectBlueprint):
    """Save blueprint to database"""
    db = SessionLocal()
    try:
        saved = SavedBlueprint(
            id=str(uuid4()),
            project_name=request.project_name,
            blueprint_json=request.json(),
            user_id="default"  # Add user auth later
        )
        db.add(saved)
        db.commit()
        return {"success": True, "id": saved.id}
    finally:
        db.close()
```

### 4. Adding Authentication

**Install dependencies**:

```bash
pip install python-jose[cryptography] passlib[bcrypt]
```

**Create authentication** (`backend/auth.py`):

```python
from datetime import datetime, timedelta
from typing import Optional
from jose import JWTError, jwt
from passlib.context import CryptContext

SECRET_KEY = "your-secret-key-change-in-production"
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 30

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

def create_access_token(data: dict, expires_delta: Optional[timedelta] = None):
    to_encode = data.copy()
    if expires_delta:
        expire = datetime.utcnow() + expires_delta
    else:
        expire = datetime.utcnow() + timedelta(minutes=15)
    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
    return encoded_jwt

def verify_token(token: str):
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        return payload
    except JWTError:
        return None
```

## Environment Variables

### Backend Environment Variables

```bash
# Required
GROQ_API_KEY              # Groq API key for LLM

# Optional
BACKEND_PORT=8000         # Server port
FRONTEND_URL=http://localhost:3000  # Frontend origin for CORS
DEBUG=True                # Debug mode
DATABASE_URL              # Database connection string
```

## Configuration Management

Update `backend/config.py` to add new settings:

```python
class Settings(BaseSettings):
    # Existing settings...
    
    # New settings
    max_tokens: int = os.getenv("MAX_TOKENS", 4000)
    request_timeout: int = os.getenv("REQUEST_TIMEOUT", 30)
    cache_enabled: bool = os.getenv("CACHE_ENABLED", "true").lower() == "true"
    
    class Config:
        env_file = ".env"
        case_sensitive = False
```

## Styling Customization

### Theme Colors

**File**: `frontend/src/App.css`

Current color palette:
- Primary: `#6366f1` (Indigo)
- Secondary: `#8b5cf6` (Purple)
- Background: `#0a0e27` (Dark Navy)
- Text: `#e0e0e0` (Light Gray)

To change theme:

```css
:root {
  --color-primary: #6366f1;
  --color-secondary: #8b5cf6;
  --color-background: #0a0e27;
  --color-text: #e0e0e0;
}
```

### Custom Dark Theme

Create `frontend/src/themes/darkTheme.css`:

```css
/* Custom dark theme */
.app.dark-theme {
  --primary: #3b82f6;
  --secondary: #2563eb;
  --background: #111827;
  --text: #f3f4f6;
}
```

## API Enhancement

### Adding Response Caching

```python
from functools import lru_cache
from typing import Tuple

@lru_cache(maxsize=128)
def generate_cached_blueprint(problem_statement: str) -> ProjectBlueprint:
    """Cached blueprint generation"""
    return generate_blueprint(problem_statement)
```

### Adding Webhooks

```python
import httpx

@app.post("/api/generate")
async def generate_with_webhook(request: UserMessage):
    blueprint = generate_blueprint(
        request.problem_statement,
        request.context
    )
    
    # Send webhook
    async with httpx.AsyncClient() as client:
        await client.post(
            "https://your-webhook-url.com/blueprint-generated",
            json=blueprint.dict()
        )
    
    return ChatResponse(message=..., blueprint=blueprint)
```

## Testing

### Backend Testing

**Install pytest**:

```bash
pip install pytest pytest-asyncio httpx
```

**Create test** (`backend/test_main.py`):

```python
import pytest
from fastapi.testclient import TestClient
from main import app

client = TestClient(app)

def test_health_check():
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json()["status"] == "healthy"

def test_generate_blueprint():
    response = client.post(
        "/api/generate",
        json={
            "problem_statement": "Build a todo app",
            "context": "Simple project"
        }
    )
    assert response.status_code == 200
    assert "blueprint" in response.json()

def test_invalid_input():
    response = client.post(
        "/api/generate",
        json={"problem_statement": ""}
    )
    assert response.status_code == 400
```

**Run tests**:

```bash
pytest backend/
```

### Frontend Testing

**Install testing libraries**:

```bash
npm install --save-dev @testing-library/react @testing-library/jest-dom vitest
```

**Create test** (`frontend/src/App.test.jsx`):

```javascript
import { render, screen } from '@testing-library/react';
import App from './App';

test('renders AI Architect title', () => {
  render(<App />);
  const titleElement = screen.getByText(/AI System Architect/i);
  expect(titleElement).toBeInTheDocument();
});
```

## Performance Optimization

### Backend Optimization

1. **Use connection pooling**:
```python
from sqlalchemy.pool import QueuePool

engine = create_engine(
    DATABASE_URL,
    poolclass=QueuePool,
    pool_size=20,
    max_overflow=0,
)
```

2. **Enable async operations**:
```python
from fastapi import BackgroundTasks

@app.post("/api/generate-async")
async def generate_async(request: UserMessage, background_tasks: BackgroundTasks):
    background_tasks.add_task(generate_blueprint, request.problem_statement)
    return {"status": "processing"}
```

### Frontend Optimization

1. **Code splitting**:
```javascript
import { lazy, Suspense } from 'react';

const BlueprintDisplay = lazy(() => import('./components/BlueprintDisplay'));

function App() {
  return (
    <Suspense fallback={<div>Loading...</div>}>
      <BlueprintDisplay />
    </Suspense>
  );
}
```

2. **Memoization**:
```javascript
import { memo } from 'react';

const ChatMessage = memo(({ message }) => {
  return <div>{message.content}</div>;
});
```

## Logging & Debugging

### Backend Logging

```python
import logging

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

logger.info(f"Generating blueprint for: {problem_statement}")
logger.debug(f"Groq response: {response}")
logger.error(f"Error: {str(exception)}")
```

### Frontend Debugging

```javascript
// Enable debug mode
const DEBUG = process.env.NODE_ENV === 'development';

const log = (...args) => DEBUG && console.log(...args);
const error = (...args) => console.error(...args);
```

## Deployment for Developers

### Local Development

```bash
# Terminal 1: Backend
cd backend
source venv/bin/activate
python main.py

# Terminal 2: Frontend
cd frontend
npm run dev
```

### Staging Deployment

```bash
# Deploy to staging environment
docker-compose -f docker-compose.staging.yml up -d
```

### Production Deployment

```bash
# Build and push images
docker build -t ai-architect-backend:1.0.0 ./backend
docker build -t ai-architect-frontend:1.0.0 ./frontend

# Push to registry
docker push your-registry/ai-architect-backend:1.0.0
docker push your-registry/ai-architect-frontend:1.0.0
```

## Best Practices

1. **Code Organization**: Keep related code in same files
2. **Error Handling**: Always catch and log errors
3. **Type Safety**: Use Pydantic for validation, TypeScript for frontend
4. **Testing**: Write tests for critical paths
5. **Documentation**: Comment complex logic
6. **Security**: Don't expose sensitive info in logs
7. **Performance**: Monitor and optimize slow queries
8. **Version Control**: Use meaningful commit messages

## Contributing

1. Fork the repository
2. Create feature branch: `git checkout -b feature/amazing-feature`
3. Commit changes: `git commit -m 'Add amazing feature'`
4. Push to branch: `git push origin feature/amazing-feature`
5. Open Pull Request

## Troubleshooting Development

| Issue | Solution |
|-------|----------|
| Import errors | Ensure virtual env is activated |
| npm package issues | Delete node_modules, run npm install |
| CORS errors | Check FRONTEND_URL in .env |
| Port conflicts | Change port in config |
| Module not found | Check Python path and imports |

---

**Version**: 1.0.0  
**Last Updated**: March 2026
