# AI System Architect - Project Blueprint Generator

An intelligent web application that generates comprehensive project blueprints using Groq AI. Perfect for developers, startups, and hackathon participants.

## 🚀 Features

- **AI-Powered Blueprint Generation**: Describe your problem, get a complete system architecture
- **Comprehensive Output**:
  - System Architecture with component design
  - Technology Stack recommendations
  - Detailed Workflow & Process flows
  - Prerequisites & Requirements
  - Multiple Solution Approaches
  - Real-World Examples & Case Studies
  - Learning References & Tutorials
  - Development Timeline
  - Actionable Next Steps

- **Beautiful Dark Theme UI**: Modern, attractive design with smooth animations
- **Real-time Processing**: Stream responses for interactive experience
- **Responsive Design**: Works on desktop, tablet, and mobile
- **Easy Integration**: Simple REST API with FastAPI backend

## 📋 Tech Stack

### Backend
- **FastAPI** - Modern Python web framework
- **Groq API** - Fast LLM inference
- **Pydantic** - Data validation
- **Python 3.8+**

### Frontend
- **React 18** - UI library
- **Vite** - Fast build tool
- **Framer Motion** - Animations
- **Axios** - HTTP client
- **React Icons** - Icon library

## 🏗️ System Architecture

```
┌─────────────────────────────────────────────────────┐
│                    Frontend (React)                  │
│  ┌──────────────────────────────────────────────┐  │
│  │         Chat Interface + Dark Theme          │  │
│  │  - Input Message                             │  │
│  │  - Chat History                              │  │
│  │  - Blueprint Display                         │  │
│  └──────────────────────────────────────────────┘  │
└─────────────────┬──────────────────────────────────┘
                  │ HTTP/REST API
                  │
┌─────────────────▼──────────────────────────────────┐
│                  Backend (FastAPI)                  │
│  ┌──────────────────────────────────────────────┐  │
│  │           API Routes                         │  │
│  │  - /api/generate (Blueprint generation)      │  │
│  │  - /api/stream-generate (Streaming)          │  │
│  │  - /api/health (Health check)                │  │
│  └──────────────────────────────────────────────┘  │
│  ┌──────────────────────────────────────────────┐  │
│  │        Groq Service                          │  │
│  │  - LLM prompt engineering                    │  │
│  │  - Response parsing & validation             │  │
│  └──────────────────────────────────────────────┘  │
└─────────────────┬──────────────────────────────────┘
                  │ API Call
                  │
                  ▼
          ┌──────────────────┐
          │   Groq API       │
          │ (mixtral-8x7b)   │
          └──────────────────┘
```

## 📦 Project Structure

```
flinders-ai/
├── backend/
│   ├── main.py                 # FastAPI app entry point
│   ├── config.py               # Configuration management
│   ├── models.py               # Pydantic models
│   ├── groq_service.py         # Groq AI integration
│   ├── requirements.txt        # Python dependencies
│   └── .env.example            # Environment variables template
│
├── frontend/
│   ├── index.html              # HTML entry point
│   ├── src/
│   │   ├── main.jsx            # React entry point
│   │   ├── App.jsx             # Main app component
│   │   ├── App.css             # Main styling
│   │   ├── index.css           # Global styles
│   │   ├── services/
│   │   │   └── api.js          # API client
│   │   └── components/
│   │       ├── ChatMessage.jsx  # Chat message component
│   │       ├── ChatMessage.css
│   │       ├── BlueprintDisplay.jsx  # Blueprint display
│   │       ├── BlueprintDisplay.css
│   │       ├── Sidebar.jsx      # Sidebar navigation
│   │       └── Sidebar.css
│   ├── vite.config.js          # Vite configuration
│   ├── package.json            # NPM dependencies
│   └── .gitignore
│
├── docs/
│   └── README.md               # This file
│
└── .gitignore                  # Git ignore rules
```

## 🔧 Prerequisites

- Python 3.8 or higher
- Node.js 16+ and npm
- Groq API Key (get it from [console.groq.com](https://console.groq.com))
- Git (optional)

## ⚙️ Installation & Setup

### 1. Clone Repository (or extract if zipped)
```bash
cd flinders-ai
```

### 2. Backend Setup

#### Step 1: Create Python Virtual Environment
```bash
# Windows
python -m venv venv
venv\Scripts\activate

# macOS/Linux
python3 -m venv venv
source venv/bin/activate
```

#### Step 2: Install Dependencies
```bash
cd backend
pip install -r requirements.txt
```

#### Step 3: Configure Environment
```bash
# Create .env file from example
cp .env.example .env

# Edit .env and add your Groq API Key
# GROQ_API_KEY=your_actual_groq_api_key_here
```

#### Step 4: Start Backend Server
```bash
python main.py

# Or use uvicorn directly:
uvicorn main:app --reload --port 8000
```

Backend will be running at `http://localhost:8000`

### 3. Frontend Setup

#### Step 1: Navigate to Frontend Directory
```bash
cd frontend
```

#### Step 2: Install Dependencies
```bash
npm install
```

#### Step 3: Start Development Server
```bash
npm run dev
```

Frontend will be running at `http://localhost:3000`

## 🎯 Usage

1. **Open the Application**: Navigate to `http://localhost:3000` in your browser
2. **Enter Problem Statement**: Type your project idea or problem you want to solve
3. **Add Context (Optional)**: Provide additional details like budget, timeline, team size
4. **Generate Blueprint**: Click Send and watch the AI generate your blueprint
5. **Explore Results**: 
   - System architecture with components
   - Recommended tech stack
   - Complete workflow documentation
   - Solution approaches with pros/cons
   - Real-world examples
   - Learning resources

## 📚 Example Prompts

```
"Build an e-commerce platform with React frontend, Node backend, PostgreSQL database. Include payment processing, inventory management, and real-time notifications."

"Create a real-time collaborative document editor like Google Docs. Multiple users should be able to edit simultaneously with live updates and version history."

"Develop a fitness tracking mobile app that monitors workout sessions, calories, and provides personalized recommendations. Should work offline."

"Build an AI-powered content generation SaaS tool with subscription management, credit system, and multiple content types."
```

## 🔌 API Documentation

### Endpoint: POST /api/generate

**Request:**
```json
{
  "problem_statement": "Build a real-time chat application",
  "context": "Small team, startup budget, 3 month timeline"
}
```

**Response:**
```json
{
  "message": {
    "role": "assistant",
    "content": "Blueprint generated successfully!"
  },
  "blueprint": {
    "project_name": "RealChat",
    "description": "...",
    "system_architecture": [...],
    "tech_stack": [...],
    "workflow": [...],
    "prerequisites": [...],
    "solution_approaches": [...],
    "real_world_examples": [...],
    "learning_references": [...],
    "timeline": {...},
    "estimated_budget": "...",
    "next_steps": [...]
  }
}
```

### Endpoint: GET /api/stream-generate

Streams blueprint generation in real-time using Server-Sent Events (SSE).

## ⚡ Performance Optimization

- **Groq API**: Using mixtral-8x7b model for fast inference
- **Frontend**: Vite for optimized bundling
- **Caching**: Responses are shown in real-time as they stream
- **Lazy Loading**: Components load on demand

## 🚀 Deployment

### Backend Deployment (Render, Railway, Heroku)

```bash
# Install gunicorn
pip install gunicorn

# Create Procfile
echo "web: gunicorn -w 4 main:app" > Procfile

# Deploy your backend to your hosting platform
# Environment variables to set:
# - GROQ_API_KEY
# - BACKEND_PORT (optional, defaults to 8000)
# - FRONTEND_URL (production URL)
```

### Frontend Deployment (Vercel, Netlify, GitHub Pages)

```bash
# Build for production
npm run build

# Deploy the 'dist' folder to your hosting platform
```

## 🛠️ Troubleshooting

### Issue: "GROQ_API_KEY not found"
**Solution**: Make sure `.env` file exists in `backend/` directory with your API key

### Issue: CORS errors in frontend
**Solution**: Backend CORS is configured for localhost. Update in `main.py` for production URLs

### Issue: Port 8000 already in use
**Solution**: `uvicorn main:app --reload --port 8001`

### Issue: npm install fails
**Solution**: Clear cache and try again
```bash
npm cache clean --force
npm install
```

## 📝 License

This project is open source and available for educational and commercial use.

## 🤝 Contributing

Contributions are welcome! Feel free to:
- Report bugs
- Suggest improvements
- Submit pull requests

## 📞 Support

For issues and questions:
1. Check the troubleshooting section above
2. Review code comments
3. Create an issue with detailed information

## 🎓 Learning Resources

- [FastAPI Documentation](https://fastapi.tiangolo.com/)
- [React Documentation](https://react.dev/)
- [Groq API Docs](https://console.groq.com/docs)
- [Vite Guide](https://vitejs.dev/)

## 📊 Key Features Explained

### 1. System Architecture
Breaks down your project into manageable components:
- Frontend (UI/UX)
- Backend (APIs, Business Logic)
- Database (Data Storage)
- External APIs (Third-party services)
- Infrastructure (Hosting, DevOps)

### 2. Tech Stack
Provides reasoned recommendations for:
- Programming languages
- Frameworks & libraries
- Databases
- Development tools
- Deployment platforms

### 3. Workflow
Step-by-step process flows showing:
- How components interact
- Data flow between systems
- User journey
- System behavior

### 4. Solution Approaches
Multiple ways to solve the problem with:
- Pros and cons analysis
- Complexity levels
- Time estimates
- Suitable use cases

### 5. Real-World Examples
Case studies of similar projects showing:
- How companies solved similar problems
- Key lessons learned
- Links to case studies

### 6. Learning References
Curated resources for skill development:
- Tutorials for each technology
- Official documentation
- Courses and guides
- Difficulty levels

---

**Version**: 1.0.0  
**Last Updated**: March 2026  
**Next Version**: Coming soon with database persistence, team collaboration features
