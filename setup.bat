@echo off
chcp 65001 >nul 2>&1
title ARIEL — Setup

echo.
echo  ======================================
echo    ARIEL — Automatic Setup
echo  ======================================
echo.

REM -- CHECK THAT PYTHON IS INSTALLED --
python --version >nul 2>&1
if errorlevel 1 (
    echo  [ERROR] Python not found.
    echo  Download Python 3.11+ from https://www.python.org/downloads/
    echo  IMPORTANT: Check "Add Python to PATH" during installation.
    echo.
    pause
    exit /b 1
)

REM -- SHOW PYTHON VERSION --
echo  [OK] Python found:
python --version
echo.

REM -- UPDATE PIP --
echo  [1/4] Updating pip...
python -m pip install --upgrade pip --quiet
if errorlevel 1 (
    echo  [WARNING] Could not update pip. Continuing...
) else (
    echo  [OK] pip updated.
)
echo.

REM -- CREATE REQUIRED FOLDERS --
echo  [2/4] Creating project folders...
if not exist "tmp" mkdir tmp
if not exist "logs" mkdir logs
if not exist "uploads" mkdir uploads
if not exist "memory" mkdir memory
if not exist "profiles" mkdir profiles
if not exist "settings" mkdir settings
echo  [OK] Folders created: tmp, logs, uploads, memory, profiles, settings
echo.

REM -- INSTALL DEPENDENCIES FROM REQUIREMENTS.TXT --
echo  [3/4] Installing Python dependencies...
echo  (This may take several minutes the first time)
echo.

if exist "requirements.txt" (
    python -m pip install -r requirements.txt
    if errorlevel 1 (
        echo.
        echo  [ERROR] There were errors installing dependencies.
        echo  Try running this script as Administrator.
        echo.
        pause
        exit /b 1
    )
) else (
    echo  [WARNING] requirements.txt not found.
    echo  Installing dependencies manually...
    echo.
    python -m pip install anthropic>=0.40.0
    python -m pip install pyautogui>=0.9.54
    python -m pip install Pillow>=10.0.0
    python -m pip install numpy>=1.24.0
    python -m pip install streamlit
    python -m pip install python-telegram-bot
    python -m pip install sentence-transformers
    python -m pip install cryptography
    python -m pip install ddgs
    python -m pip install beautifulsoup4
    python -m pip install requests
    python -m pip install pypdf
)

echo.
echo  [OK] Dependencies installed.
echo.

REM -- VERIFY INSTALLATION --
echo  [4/4] Verifying installation...
echo.

python -c "import anthropic; print('  [OK] anthropic', anthropic.__version__)" 2>nul || echo  [MISSING] anthropic
python -c "import pyautogui; print('  [OK] pyautogui')" 2>nul || echo  [MISSING] pyautogui
python -c "import PIL; print('  [OK] Pillow')" 2>nul || echo  [MISSING] Pillow
python -c "import numpy; print('  [OK] numpy', numpy.__version__)" 2>nul || echo  [MISSING] numpy
python -c "import streamlit; print('  [OK] streamlit', streamlit.__version__)" 2>nul || echo  [MISSING] streamlit
python -c "import telegram; print('  [OK] python-telegram-bot')" 2>nul || echo  [MISSING] python-telegram-bot
python -c "import sentence_transformers; print('  [OK] sentence-transformers')" 2>nul || echo  [MISSING] sentence-transformers
python -c "import cryptography; print('  [OK] cryptography')" 2>nul || echo  [MISSING] cryptography
python -c "import duckduckgo_search; print('  [OK] ddgs')" 2>nul || echo  [MISSING] ddgs
python -c "import bs4; print('  [OK] beautifulsoup4')" 2>nul || echo  [MISSING] beautifulsoup4
python -c "import requests; print('  [OK] requests')" 2>nul || echo  [MISSING] requests
python -c "import pypdf; print('  [OK] pypdf')" 2>nul || echo  [MISSING] pypdf

echo.
echo  ======================================
echo    SETUP COMPLETED
echo  ======================================
echo.
echo  To launch ARIEL:
echo    python ariel.py
echo.
echo  Your browser will open at http://localhost:8501
echo.
pause
