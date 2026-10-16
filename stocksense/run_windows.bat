@echo off
setlocal
cd /d "%~dp0"
title STOCKSENSE

where py >nul 2>nul
if %errorlevel%==0 (set "PY=py -3") else (set "PY=python")

if not exist ".venv\Scripts\python.exe" (
    %PY% -c "import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)" >nul 2>nul
    if errorlevel 1 (
        echo [error] Python 3.11 or newer is required. Install it from python.org and tick
        echo         "Add python.exe to PATH", then run this file again.
        pause
        exit /b 1
    )
    echo [setup] Creating virtual environment in .venv ...
    %PY% -m venv .venv
    if errorlevel 1 (
        echo [error] Could not create a virtual environment.
        pause
        exit /b 1
    )
)

call ".venv\Scripts\activate.bat"

python -c "import streamlit, sqlalchemy, pydantic, pandas, plotly, dotenv, google.genai" >nul 2>nul
if errorlevel 1 (
    echo [setup] Installing dependencies once from requirements.txt ...
    python -m pip install --upgrade pip
    python -m pip install -r requirements.txt
    if errorlevel 1 (
        echo [error] Dependency installation failed. Check your internet connection and run:
        echo         .venv\Scripts\python -m pip install -r requirements.txt
        pause
        exit /b 1
    )
)

python scripts\check_setup.py
if errorlevel 1 (
    echo [error] Setup check failed - see the lines marked !! above.
    pause
    exit /b 1
)

echo.
echo [run] Opening STOCKSENSE at http://localhost:8501   (close this window or press Ctrl+C to stop)
python -m streamlit run app.py --server.headless false
echo [info] STOCKSENSE has stopped.
endlocal
