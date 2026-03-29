# Quick Start Guide

Get AI System Architect running in 5 minutes!

## ⚡ TL;DR Setup

### 1. Get Groq API Key
- Visit [console.groq.com](https://console.groq.com)
- Sign up for free
- Generate an API key

### 2. Backend (3 steps)

```bash
# Navigate to backend directory
cd backend

# Create virtual environment and activate
python -m venv venv
venv\Scripts\activate  # Windows
# or
source venv/bin/activate  # macOS/Linux

# Install & run
pip install -r requirements.txt

# Create .env file with your Groq Key
echo GROQ_API_KEY=your_key_here > .env
echo BACKEND_PORT=8000 >> .env
echo FRONTEND_URL=http://localhost:3000 >> .env

# Start the server
python main.py
```

Backend running on `http://localhost:8000` ✓

### 3. Frontend (2 steps + Run)

```bash
# Navigate to frontend directory
cd frontend

# Install dependencies
npm install

# Start development server
npm run dev
```

Frontend running on `http://localhost:3000` ✓

## 🎯 Test It Out

1. Open `http://localhost:3000` in your browser
2. Try this prompt: 
   ```
   "Build a real-time chat application with React frontend and Node.js backend"
   ```
3. Watch the AI generate your blueprint! 🚀

## 📋 What You'll Get

For any project idea, AI generates:
- ✅ System Architecture diagram
- ✅ Tech Stack recommendations
- ✅ Workflow & process flows
- ✅ Prerequisites checklist
- ✅ Multiple solution approaches
- ✅ Real-world examples
- ✅ Learning resources
- ✅ Development timeline
- ✅ Next steps action items

## 🔧 Environment Variables

**backend/.env**
```
GROQ_API_KEY=your_groq_api_key
BACKEND_PORT=8000
FRONTEND_URL=http://localhost:3000
DEBUG=True
```

## 🎨 Features

- 🌙 Beautiful dark theme UI
- ⚡ Fast AI-powered generation
- 📱 Responsive design
- 🎯 Professional blueprint output
- 💬 Chat-like interface
- 🔄 Streaming responses

## 📚 Example Use Cases

- **Startups**: Plan your MVP (Minimum Viable Product)
- **Hackathons**: Get architecture in minutes
- **Developers**: Learn best practices
- **Teams**: Align on technical decisions
- **Learning**: Understand system design

## ⚠️ Common Issues & Fixes

| Issue | Solution |
|-------|----------|
| `GROQ_API_KEY not found` | Add .env file with your key |
| Port 8000 in use | `uvicorn main:app --port 8001` |
| npm install fails | `npm cache clean --force && npm install` |
| CORS errors | Check FRONTEND_URL in .env |

## 🚀 Production Deployment

**Backend** (Render/Railway):
```bash
pip install gunicorn
gunicorn -w 4 main:app
```

**Frontend** (Vercel/Netlify):
```bash
npm run build
# Deploy 'dist' folder
```

## 📞 Need Help?

1. Check the main [README.md](../README.md)
2. Review error messages in browser console
3. Verify .env file is configured correctly
4. Ensure ports 3000 and 8000 are available

## 🎓 Next Steps

After setup:
1. Explore different project ideas
2. Compare multiple solution approaches
3. Use learning references to dive deeper
4. Build your project! 💪

---

Happy building! 🎉
