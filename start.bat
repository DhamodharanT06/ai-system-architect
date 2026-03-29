@echo off
REM AI System Architect - Startup Script for Windows
REM Uses Python Virtual Environment (venv) for isolated dependencies

setlocal enabledelayedexpansion

echo.
echo 🚀 Starting AI System Architect...
echo.

REM Backend setup
echo ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
echo ➜ Backend Setup (with venv)
echo ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
echo.

cd backend

REM Check if venv exists, create if not
if not exist "venv" (
    echo 📦 Creating Python virtual environment...
    python -m venv venv
    if !errorlevel! neq 0 (
        echo ❌ Failed to create virtual environment
        pause
        exit /b 1
    )
    echo ✓ Virtual environment created
)

REM Activate venv
echo 🔌 Activating Python virtual environment...
call venv\Scripts\activate.bat
if !errorlevel! neq 0 (
    echo ❌ Failed to activate virtual environment
    pause
    exit /b 1
)

REM Install dependencies using venv python
echo 📥 Installing Python dependencies (using venv)...
python -m pip install --upgrade pip -q
python -m pip install -r requirements.txt -q
if !errorlevel! neq 0 (
    echo ❌ Failed to install dependencies
    pause
    exit /b 1
)
echo ✓ Dependencies installed

REM Check .env file
if not exist ".env" (
    echo.
    echo ⚠️  .env file not found!
    echo Creating .env from template...
    copy .env.example .env
    echo.
    echo Please edit backend\.env and add your GROQ_API_KEY
    echo Get your key from: https://console.groq.com
    pause
)

REM Start backend in new window (using venv python)
echo.
echo ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
echo ➜ Starting Backend Server (venv)
echo ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
echo.
echo ✓ Backend starting on http://localhost:8000
echo 📚 API Docs: http://localhost:8000/docs
echo.

REM Use venv python to run the app
start "AI Architect Backend" cmd /k "venv\Scripts\activate.bat && python main.py"

REM Wait for backend to start
timeout /t 4 /nobreak

REM Frontend setup
cd ..\frontend

REM Check and install npm dependencies
if not exist "node_modules" (
    echo 📥 Installing npm dependencies...
    call npm install
    if !errorlevel! neq 0 (
        echo ❌ Failed to install npm dependencies
        pause
        exit /b 1
    )
)

REM Start frontend in new window
echo.
echo ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
echo ➜ Starting Frontend Server
echo ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
echo.
echo ✓ Frontend starting on http://localhost:3000
echo.

start "AI Architect Frontend" cmd /k "npm run dev"

REM Display startup summary
echo.
echo ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
echo 🎉 Startup Complete!
echo ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
echo.
echo ✓ Backend API: http://localhost:8000
echo ✓ Frontend: http://localhost:3000
echo ✓ API Docs: http://localhost:8000/docs
echo.
echo ✅ Virtual Environment Active
echo ✅ All dependencies installed
echo.
echo 📝 Next steps:
echo 1. Open http://localhost:3000 in your browser
echo 2. Try a sample prompt:
echo    "Build a real-time chat application"
echo.
echo Both servers are running in new command windows.
echo Close those windows to stop the servers.
echo.
echo Happy coding! 🚀
echo.

pause
