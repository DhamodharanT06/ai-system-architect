# API Reference

Complete API documentation for AI System Architect.

## Base URL

```
http://localhost:8000
```

## Authentication

Currently, no authentication is required. Groq API key is managed server-side.

## Endpoints

### 1. Generate Blueprint

Generate a comprehensive project blueprint from a problem statement.

#### Request

```http
POST /api/generate
Content-Type: application/json

{
  "problem_statement": "Build a real-time chat application",
  "context": "Small team, startup budget, 3 month timeline"
}
```

**Parameters**:
- `problem_statement` (string, required): Describe your project or problem
- `context` (string, optional): Additional context about the project

#### Response (200 OK)

```json
{
  "message": {
    "role": "assistant",
    "content": "Blueprint generated successfully!"
  },
  "blueprint": {
    "project_name": "RealChat",
    "description": "A modern real-time chat application...",
    "problem_statement": "Build a real-time chat application",
    "system_architecture": [
      {
        "name": "Frontend",
        "type": "frontend",
        "description": "React-based user interface...",
        "responsibilities": [
          "Handle user interactions",
          "Display messages in real-time",
          "Manage connection state"
        ],
        "technologies": ["React", "Socket.IO", "Tailwind CSS"]
      }
    ],
    "tech_stack": [
      {
        "name": "React",
        "category": "Frontend",
        "reason": "Component-based, large ecosystem, great for real-time UIs",
        "version": "18"
      }
    ],
    "workflow": [
      {
        "step_number": 1,
        "title": "User Authentication",
        "description": "Users sign up or log in...",
        "components_involved": ["Frontend", "Backend", "Database"],
        "key_actions": ["Validate credentials", "Create session", "Return JWT token"]
      }
    ],
    "prerequisites": [
      {
        "category": "Knowledge",
        "items": [
          "JavaScript/TypeScript fundamentals",
          "React basics",
          "REST API concepts",
          "Real-time communication patterns"
        ]
      }
    ],
    "solution_approaches": [
      {
        "name": "WebSocket + Node.js Backend",
        "description": "Using WebSockets for real-time communication...",
        "pros": [
          "True real-time bidirectional communication",
          "Lower latency than polling",
          "Good for live features"
        ],
        "cons": [
          "More complex server setup",
          "Requires sticky sessions in load balancing",
          "Higher resource usage"
        ],
        "complexity": "Medium",
        "estimated_time": "4-6 weeks",
        "best_for": "Applications requiring real-time features"
      }
    ],
    "real_world_examples": [
      {
        "title": "Slack Engineering Architecture",
        "description": "How Slack built their chat platform...",
        "company": "Slack",
        "link": "https://slack.engineering/...",
        "lessons_learned": [
          "Scalability is crucial from day one",
          "Message ordering and consistency matters",
          "Use microservices for different concerns"
        ]
      }
    ],
    "learning_references": [
      {
        "title": "Socket.IO Documentation",
        "url": "https://socket.io/docs/",
        "type": "Documentation",
        "difficulty": "Intermediate"
      }
    ],
    "timeline": {
      "Phase 1 - Basic Setup": "1 week",
      "Phase 2 - Core Features": "2-3 weeks",
      "Phase 3 - Real-time Features": "2-3 weeks",
      "Phase 4 - Testing & Deployment": "1 week"
    },
    "estimated_budget": "$15,000 - $30,000 for MVP",
    "next_steps": [
      "Set up development environment",
      "Design database schema",
      "Create UI mockups",
      "Start backend API development"
    ]
  }
}
```

#### Error Response (400 Bad Request)

```json
{
  "detail": "Problem statement cannot be empty"
}
```

#### Error Response (500 Internal Server Error)

```json
{
  "detail": "Error generating blueprint: [error details]"
}
```

---

### 2. Stream Generate Blueprint

Generate blueprint with streaming response for real-time updates.

#### Request

```http
GET /api/stream-generate?problem_statement=Build%20a%20chat%20app&context=Startup%20budget
Accept: text/event-stream
```

**Parameters**:
- `problem_statement` (string, required): Problem statement (URL encoded)
- `context` (string, optional): Additional context (URL encoded)

#### Response (200 OK - Server-Sent Events)

```
data: {"content": "{\n"}
data: {"content": "\"project_name\": \"RealChat\",\n"}
data: {"content": "\"description\": \"...\"\n"}
...
```

The response is streamed as Server-Sent Events (SSE). Each event contains a `content` chunk of the JSON blueprint being generated.

**Processing the stream in JavaScript**:

```javascript
const eventSource = new EventSource(
  '/api/stream-generate?problem_statement=Build%20a%20chat%20app'
);

let fullResponse = '';

eventSource.onmessage = (event) => {
  const data = JSON.parse(event.data);
  fullResponse += data.content;
};

eventSource.onerror = (error) => {
  console.error('Stream error:', error);
  eventSource.close();
};
```

---

### 3. Health Check

Check if the API is running and healthy.

#### Request

```http
GET /api/health
```

#### Response (200 OK)

```json
{
  "status": "healthy",
  "debug": false
}
```

---

### 4. Get Examples

Retrieve example project blueprints to get started.

#### Request

```http
GET /api/examples
```

#### Response (200 OK)

```json
{
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
```

---

### 5. Auto Generated API Docs

Interactive Swagger documentation for all endpoints.

#### Request

```http
GET /docs
```

**Opens**: Interactive API documentation at `http://localhost:8000/docs`

---

## Data Models

### ProjectBlueprint

Complete project blueprint with all components.

```json
{
  "project_name": "string",
  "description": "string",
  "problem_statement": "string",
  "system_architecture": [
    {
      "name": "string",
      "type": "frontend | backend | database | external_api | infrastructure",
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
      "complexity": "Simple | Medium | Complex",
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
      "type": "Tutorial | Documentation | Guide | Course | Blog",
      "difficulty": "Beginner | Intermediate | Advanced"
    }
  ],
  "timeline": {
    "phase_name": "duration"
  },
  "estimated_budget": "string or null",
  "next_steps": ["string"]
}
```

---

## Rate Limiting

Currently, there is no rate limiting implemented. For production use, it's recommended to add:

```python
from slowapi import Limiter
from slowapi.util import get_remote_address

limiter = Limiter(key_func=get_remote_address)

@app.post("/api/generate")
@limiter.limit("10/minute")
async def generate_blueprint():
    ...
```

---

## CORS Configuration

The API is configured to accept requests from:
- `http://localhost:3000` (development)
- `http://localhost:5173` (Vite)
- Frontend URL from environment variable

To change for production, update `main.py`:

```python
app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://yourdomain.com"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
```

---

## Error Handling

All errors follow this format:

```json
{
  "detail": "Error message describing what went wrong"
}
```

**Common Status Codes**:
- `200 OK` - Successful request
- `400 Bad Request` - Invalid input
- `422 Unprocessable Entity` - Validation error
- `500 Internal Server Error` - Server error

---

## Request/Response Examples

### Example 1: Simple Blueprint Request

```bash
curl -X POST http://localhost:8000/api/generate \
  -H "Content-Type: application/json" \
  -d '{
    "problem_statement": "Build a todo app"
  }'
```

### Example 2: Detailed Blueprint Request

```bash
curl -X POST http://localhost:8000/api/generate \
  -H "Content-Type: application/json" \
  -d '{
    "problem_statement": "Build an e-commerce platform",
    "context": "Target: SME businesses, Budget: $50k, Timeline: 6 months, Team: 5 developers"
  }'
```

### Example 3: Stream Generation with JavaScript

```javascript
async function generateBlueprintStream(problemStatement) {
  const response = await fetch(
    `/api/stream-generate?problem_statement=${encodeURIComponent(problemStatement)}`,
    {
      headers: {
        'Accept': 'text/event-stream'
      }
    }
  );

  const reader = response.body.getReader();
  let blueprint = '';

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;

    const text = new TextDecoder().decode(value);
    const lines = text.split('\n');

    for (const line of lines) {
      if (line.startsWith('data: ')) {
        try {
          const data = JSON.parse(line.slice(6));
          blueprint += data.content;
          console.log('Received:', data.content);
        } catch (e) {
          // Ignore parse errors
        }
      }
    }
  }

  return JSON.parse(blueprint);
}
```

---

## Pagination

Not applicable for current implementation. All responses return complete blueprints.

## Filtering

Not applicable for current implementation. 

---

## Version

**API Version**: 1.0.0  
**Last Updated**: March 2026  
**Stability**: Stable
