# Architecture Documentation

## System Components

### 1. Frontend Layer (React)

**Purpose**: User interface for blueprint generation

**Components**:
- `App.jsx` - Main application container
- `ChatMessage.jsx` - Individual message display
- `BlueprintDisplay.jsx` - Blueprint presentation with collapsible sections
- `Sidebar.jsx` - Navigation and quick access

**Features**:
- Real-time chat interface
- Collapsible sections for better readability
- Dark theme with gradient accents
- Smooth animations with Framer Motion
- Copy-to-clipboard functionality
- Responsive mobile design

**Technology Stack**:
- React 18 for UI rendering
- Vite for fast development
- Axios for HTTP requests
- Framer Motion for animations
- React Icons for icon library

### 2. Backend API Layer (FastAPI)

**Purpose**: REST API for blueprint generation

**Key Endpoints**:
```
POST   /api/generate           - Generate blueprint from problem statement
GET    /api/stream-generate    - Stream generation with SSE
GET    /api/health             - Health check
GET    /api/examples           - Get example projects
GET    /docs                   - Swagger API documentation
```

**Features**:
- CORS enabled for frontend communication
- Request validation with Pydantic
- Comprehensive error handling
- Logging for debugging
- Health checks for monitoring

### 3. AI Service Layer (Groq Integration)

**Purpose**: LLM-powered blueprint generation

**Process**:
1. User sends problem statement
2. System crafts optimized prompt with context
3. Groq mixtral-8x7b model processes request
4. Response parsed and validated
5. Blueprint returned to frontend

**Key Functions**:
- `generate_blueprint()` - Single request generation
- `generate_streaming_blueprint()` - Streaming generation for real-time updates

**Model**: mixtral-8x7b-32768
- Fast inference
- 32K token context window
- Excellent reasoning capabilities

### 4. Data Models

**Core Models** (Pydantic):
```python
ProjectBlueprint       - Complete project blueprint
ArchitectureComponent  - System component details
TechStackItem          - Technology choice with reasoning
WorkflowStep           - Process step with actions
PrerequisiteItem       - Requirement/prerequisite
SolutionApproach       - Alternative solution with pros/cons
RealWorldExample       - Case study and lessons
LearningReference      - Tutorial and documentation links
```

## Data Flow

```
User Input
    ↓
Frontend Validation
    ↓
API Request (POST /api/generate)
    ↓
Backend Routing
    ↓
Prompt Engineering
    ↓
Groq API Request
    ↓
Response Parsing
    ↓
JSON Validation
    ↓
Blueprint Model Instantiation
    ↓
API Response
    ↓
Frontend Rendering
    ↓
Display to User
```

## Security Considerations

1. **API Key Management**: Groq key stored in environment variables
2. **CORS Protection**: Only configured origins can access API
3. **Input Validation**: All user inputs validated via Pydantic
4. **Error Handling**: Sensitive errors not exposed to frontend
5. **Rate Limiting**: Can be added per Groq API tier

## Performance Optimizations

1. **LLM Selection**: mixtral-8x7b chosen for speed/quality ratio
2. **Streaming**: Real-time response streaming to user
3. **Frontend Caching**: Messages cached in React state
4. **Code Splitting**: Vite automatically handles bundling
5. **CSS Optimization**: Inline critical styles

## Scalability Strategy

### Horizontal Scaling
- Containerize with Docker
- Deploy multiple backend instances
- Use load balancer (nginx, AWS ALB)
- Groq API handles request queuing

### Database Addition (Future)
```python
# Store blueprints for user history
- PostgreSQL for relational data
- Redis for caching
- S3 for file storage
```

### Monitoring & Logging
```
- Request/response timing
- Error tracking
- API usage metrics
- User engagement analytics
```

## Technology Decision Matrix

| Component | Technology | Why |
|-----------|------------|-----|
| Backend | FastAPI | Fast, modern, async support |
| Frontend | React | Component reusability |
| LLM | Groq | Speed and cost-effective |
| Styling | CSS | Lightweight, flexible |
| Animation | Framer Motion | Production-ready |
| Validation | Pydantic | Type safety, validation |

## Key Features Implementation

### 1. Blueprint Generation
- Optimized prompt engineering
- JSON parsing from LLM output
- Fallback error handling
- Input context preservation

### 2. Real-time Streaming
- Server-Sent Events (SSE)
- Async generator pattern
- Frontend event listener
- Progressive UI updates

### 3. Dark Theme
- Gradient overlays
- Color palette: Indigo/Purple
- Accessibility considerations
- High contrast ratios

### 4. Responsive Design
- Mobile-first approach
- Flexbox layouts
- Media query breakpoints
- Touch-friendly interface

## Configuration Management

**Environment Variables**:
```
GROQ_API_KEY        - API authentication
BACKEND_PORT        - Server port
FRONTEND_URL        - CORS origin
DEBUG               - Development mode
```

**Runtime Configuration**:
- Loaded from .env file
- Validated on startup
- Type-safe with Pydantic

## Error Handling Strategy

```
User Input Error
    → 400 Bad Request
    → Specific error message
    
API Error
    → 500 Internal Server Error
    → Logged for debugging
    → Generic message to user
    
LLM Error
    → Retry logic
    → Fallback templates
    → User notification
```

## Future Enhancement Areas

1. **Database Integration**: Store blueprints and user history
2. **Authentication**: User accounts and project management
3. **Customization**: Template library and customization options
4. **Collaboration**: Team sharing and feedback
5. **Export**: PDF, Word, Markdown export options
6. **Version Control**: Track blueprint iterations
7. **Analytics**: Usage tracking and recommendations

---

**Version**: 1.0.0  
**Last Updated**: March 2026  
**Architecture Pattern**: MVC (Model-View-Controller) with API Gateway
