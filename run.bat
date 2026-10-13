@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
    where python >nul 2>nul
    if errorlevel 1 goto no_python
    python -c "import sys; sys.exit(0 if sys.version_info >= (3,12) else 1)"
    if errorlevel 1 goto no_python
    python -m venv .venv
    if errorlevel 1 goto failed
)
".venv\Scripts\python.exe" -c "import fastapi, uvicorn, sqlalchemy, openai, dotenv, multipart, pypdf, httpx, pytest, ddgs" >nul 2>nul
if errorlevel 1 (
    echo Installing project dependencies...
    if exist requirements.lock.txt (
        ".venv\Scripts\python.exe" -m pip install -r requirements.lock.txt
    ) else (
        ".venv\Scripts\python.exe" -m pip install -r requirements.txt
    )
    if errorlevel 1 goto failed
)
if not exist ".env" copy /y ".env.example" ".env" >nul
echo Starting AI Interview Practice. Configure your own API in the local page.
echo Close this window or press Ctrl+C to stop the server.
".venv\Scripts\python.exe" run.py %*
if errorlevel 1 goto failed
exit /b 0
:no_python
echo Python 3.12 or newer is required. Install Python and add it to PATH.
pause
exit /b 1
:failed
echo Startup failed. Please check the error above and README.md.
pause
exit /b 1
