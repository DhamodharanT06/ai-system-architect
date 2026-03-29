#!/bin/bash

# AI System Architect - Startup Script
# This script sets up and starts both backend and frontend using Python venv

set -e

echo "🚀 Starting AI System Architect..."
echo ""

# Colors for output
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m' # No Color

# Check if running on Windows/Git Bash
if [[ "$OSTYPE" == "msys" || "$OSTYPE" == "cygwin" ]]; then
    IS_WINDOWS=true
else
    IS_WINDOWS=false
fi

# Function to print section headers
print_section() {
    echo -e "\n${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo -e "${BLUE}➜ $1${NC}"
    echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}\n"
}

# Function to handle errors
error_exit() {
    echo -e "${RED}❌ Error: $1${NC}"
    exit 1
}

# Backend setup
print_section "Backend Setup (with Python venv)"

cd backend

# Check if venv exists
if [ ! -d "venv" ]; then
    echo "📦 Creating Python virtual environment..."
    python3 -m venv venv || python -m venv venv || error_exit "Failed to create virtual environment"
    echo "✓ Virtual environment created"
fi

# Activate venv
echo "🔌 Activating Python virtual environment..."
if [ "$IS_WINDOWS" = true ]; then
    source venv/Scripts/activate
else
    source venv/bin/activate
fi

# Verify venv is active
if [[ -z "$VIRTUAL_ENV" ]]; then
    error_exit "Failed to activate virtual environment"
fi

# Install dependencies using venv python
echo "📥 Installing Python dependencies (using venv)..."
python -m pip install --upgrade pip -q
python -m pip install -r requirements.txt -q || error_exit "Failed to install dependencies"
echo "✓ Dependencies installed"

# Check .env file
if [ ! -f ".env" ]; then
    echo -e "${YELLOW}⚠️  .env file not found!${NC}"
    echo "Creating .env from template..."
    cp .env.example .env || error_exit "Failed to copy .env.example"
    echo -e "${YELLOW}Please edit backend/.env and add your GROQ_API_KEY${NC}"
    echo "Get your key from: https://console.groq.com"
    read -p "Press enter when ready..."
fi

# Start backend
print_section "Starting Backend Server"
echo -e "${GREEN}✓ Backend starting on http://localhost:8000${NC}"
echo "📚 API Docs: http://localhost:8000/docs"
echo ""

python main.py &
BACKEND_PID=$!

# Give backend time to start
sleep 3

# Frontend setup
print_section "Frontend Setup"

cd ../frontend

# Check Node modules
if [ ! -d "node_modules" ]; then
    echo "📥 Installing npm dependencies..."
    npm install
fi

# Start frontend
print_section "Starting Frontend Server"
echo -e "${GREEN}✓ Frontend starting on http://localhost:3000${NC}"
echo ""

npm run dev &
FRONTEND_PID=$!

# Display startup summary
print_section "🎉 Startup Complete!"
echo -e "${GREEN}✓ Backend API: http://localhost:8000${NC}"
echo -e "${GREEN}✓ Frontend: http://localhost:3000${NC}"
echo -e "${GREEN}✓ API Docs: http://localhost:8000/docs${NC}"
echo ""
echo -e "${GREEN}✅ Virtual Environment Active${NC}"
echo -e "${GREEN}✅ All dependencies installed${NC}"
echo ""
echo "📝 Next steps:"
echo "1. Open http://localhost:3000 in your browser"
echo "2. Try a sample prompt:"
echo "   'Build a real-time chat application'"
echo ""
echo "Press Ctrl+C to stop servers"
echo ""

# Keep script running
wait
