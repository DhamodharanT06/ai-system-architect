# Installation Checklist

Use this checklist to verify your AI System Architect setup.

## Prerequisites ✓

- [ ] Python 3.8+ installed
- [ ] Node.js 16+ installed
- [ ] npm installed
- [ ] Groq API Key created (from console.groq.com)
- [ ] Git installed (optional)

## Backend Setup ✓

- [ ] Navigate to `backend/` directory
- [ ] Create virtual environment (`python -m venv venv`)
- [ ] Activate virtual environment
  - [ ] Windows: `venv\Scripts\activate`
  - [ ] macOS/Linux: `source venv/bin/activate`
- [ ] Install dependencies (`pip install -r requirements.txt`)
- [ ] Create `.env` file from `.env.example`
- [ ] Add Groq API key to `.env`
  ```
  GROQ_API_KEY=your_actual_key_here
  ```
- [ ] Verify backend starts (`python main.py`)
- [ ] Check API is running (open http://localhost:8000)
- [ ] Verify docs (open http://localhost:8000/docs)

## Frontend Setup ✓

- [ ] Navigate to `frontend/` directory
- [ ] Install dependencies (`npm install`)
- [ ] Start dev server (`npm run dev`)
- [ ] Verify frontend is running (open http://localhost:3000)
- [ ] Check backend connection (should see "AI System Architect" title)

## Configuration Verification ✓

### Backend Configuration
- [ ] `.env` file created in `backend/` folder
- [ ] `GROQ_API_KEY` is set correctly
- [ ] `BACKEND_PORT` is 8000 (or changed intentionally)
- [ ] `FRONTEND_URL` matches your frontend URL
- [ ] `DEBUG` is True for development

### Frontend Configuration
- [ ] API URL points to `http://localhost:8000`
- [ ] All React components load without errors
- [ ] Sidebar opens and closes smoothly
- [ ] Dark theme is applied

## Functionality Testing ✓

### Backend
- [ ] Health check endpoint works (`GET /api/health`)
- [ ] Examples endpoint works (`GET /api/examples`)
- [ ] API documentation loads (`GET /docs`)
- [ ] Can make POST request to `/api/generate`

### Frontend
- [ ] Page loads without console errors
- [ ] Can type in the input field
- [ ] Send button is clickable
- [ ] Chat history displays messages

### Integration
- [ ] Type a message and click send
- [ ] Backend receives the request (check logs)
- [ ] Loading indicator appears
- [ ] Blueprint is generated and displayed
- [ ] Can see Architecture, Tech Stack, Workflow sections

## Sample Prompts to Test ✓

Try these prompts to verify everything works:

1. **Short Prompt**
   ```
   "Build a todo app"
   ```
   Expected: Simple blueprint in ~30 seconds

2. **Detailed Prompt**
   ```
   "Create an e-commerce platform with React frontend, 
    Node.js backend, and PostgreSQL database. 
    Include product search, shopping cart, and payments."
   ```
   Expected: Detailed blueprint in ~1 minute

3. **Complex Prompt**
   ```
   "Build a real-time collaborative document editor similar to Google Docs.
    Multiple users should edit simultaneously with live updates.
    Include version history and comments.
    Tech: React, Node.js, WebSocket, MongoDB"
   ```
   Expected: Comprehensive blueprint in ~1-2 minutes

## Performance Checks ✓

- [ ] Backend starts in < 5 seconds
- [ ] Frontend loads in < 3 seconds
- [ ] Blueprint generates in < 2 minutes
- [ ] UI remains responsive during generation
- [ ] No browser console errors
- [ ] No timeout errors

## Docker Setup (Optional) ✓

- [ ] Docker installed (`docker --version`)
- [ ] Docker Compose installed (`docker-compose --version`)
- [ ] Build backend image (`docker build -t ai-architect-backend ./backend`)
- [ ] Build frontend image (`docker build -t ai-architect-frontend ./frontend`)
- [ ] Run with compose (`docker-compose up`)
- [ ] Verify services start (check logs)

## Troubleshooting ✓

### If backend doesn't start:
- [ ] Check Python version (3.8+)
- [ ] Verify venv is activated
- [ ] Check for port 8000 conflicts
- [ ] Verify Groq API key is valid

### If frontend doesn't load:
- [ ] Check Node.js version (16+)
- [ ] Clear node_modules and reinstall
- [ ] Check for port 3000 conflicts
- [ ] Verify API URL in services/api.js

### If blueprints don't generate:
- [ ] Verify Groq API key in .env
- [ ] Check backend logs for errors
- [ ] Verify network connection
- [ ] Try with simpler prompt first

## Documentation Review ✓

- [ ] Read README.md (main overview)
- [ ] Read QUICKSTART.md (quick setup)
- [ ] Skim ARCHITECTURE.md (understand design)
- [ ] Check API.md (understand endpoints)

## Deployment Preparation ✓

- [ ] .env file is configured
- [ ] Docker files are present
- [ ] docker-compose.yml is ready
- [ ] All dependencies listed in requirements.txt
- [ ] npm packages listed in package.json

## Project Structure Verification ✓

```
flinders-ai/
├── backend/
│   ├── main.py ✓
│   ├── config.py ✓
│   ├── models.py ✓
│   ├── groq_service.py ✓
│   ├── requirements.txt ✓
│   ├── .env ✓
│   └── Dockerfile ✓
├── frontend/
│   ├── src/
│   │   ├── main.jsx ✓
│   │   ├── App.jsx ✓
│   │   ├── components/ ✓
│   │   └── services/ ✓
│   ├── package.json ✓
│   └── Dockerfile ✓
├── docs/
│   ├── README.md ✓
│   ├── QUICKSTART.md ✓
│   ├── ARCHITECTURE.md ✓
│   ├── API.md ✓
│   └── DEVELOPER_GUIDE.md ✓
├── docker-compose.yml ✓
└── .gitignore ✓
```

## Ready to Deploy ✓

- [ ] All tests pass
- [ ] Environment variables configured
- [ ] Docker images built (optional)
- [ ] Documentation reviewed
- [ ] Code committed to Git (optional)
- [ ] Ready for demo/presentation

---

## Next Steps

1. **Explore Features**
   - Try different prompts
   - Expand/collapse blueprint sections
   - Copy content to clipboard

2. **Customize**
   - Change colors in CSS files
   - Modify prompt in groq_service.py
   - Add new components

3. **Deploy**
   - Follow DEPLOYMENT.md
   - Choose hosting platform
   - Set up CI/CD pipeline

4. **Share**
   - Share project with others
   - Get feedback
   - Iterate on improvements

**Status**: _____ (Mark which checks you've completed)

**Date Completed**: _______________

**Notes**: _____________________________________________________

---

✅ **Setup Complete!** Ready to generate blueprints. Happy coding! 🚀
