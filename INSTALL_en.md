# ARIEL — Installation Guide

## Prerequisites

- **Windows 10/11** (64-bit)
- **Internet connection**
- **Anthropic API Key** (https://console.anthropic.com)

---

## Step 1 — Install Python 3.11+

1. Download from https://www.python.org/downloads/
2. During installation, **check the "Add Python to PATH" box** (VERY IMPORTANT)
3. Click "Install Now"
4. Verify by opening CMD or PowerShell:
   ```
   python --version
   ```
   Should display `Python 3.11.x` or higher.

---

## Step 2 — Install Git (optional but recommended)

1. Download from https://git-scm.com/download/win
2. Install with default options
3. If using Git, clone the repository. Otherwise, copy the project folder manually.

---

## Step 3 — Copy the project

Copy the entire `ARIEL/` folder to your desired location, for example:
```
C:\Users\YourUser\Desktop\ARIEL\
```

The structure should look like this:
```
ARIEL/
├── core/
│   ├── agent.py
│   ├── executor.py
│   ├── logger.py
│   ├── memory.py
│   ├── security.py
│   └── utils.py
├── gateways/
│   ├── scheduler.py
│   └── telegram_bot.py
├── languages/
│   ├── en.json
│   └── es.json
├── laws/
│   └── laws.json
├── logs/
├── memory/
├── profiles/
│   ├── agent.json
│   ├── user.json
│   ├── ariel-logo.png
├── settings/
│   ├── config.json
│   └── tasks.json
├── tmp/
├── tools/
│   ├── tools.json
│   └── toolindex.json
├── uploads/
├── gui.py
├── main.py
├── start.py
├── requirements.txt
├── setup.bat
├── INSTALL_es.md
└── INSTALL_en.md
```

---

## Step 4 — Run setup.bat

Double-click `setup.bat` inside the ARIEL folder, or from CMD:
```
cd C:\Users\YourUser\Desktop\ARIEL
setup.bat
```

This script will:
1. Verify that Python is installed
2. Update pip
3. Install all dependencies
4. Create required folders (tmp/, logs/, uploads/, memory/)
5. Verify all packages were installed correctly

---

## Step 5 — Configure API Key

When you run ARIEL for the first time, the interface will ask for your Anthropic API Key in the Settings screen (⚙️).

Alternatively, you can edit `settings/config.json` manually:
```json
{
  "api": {
    "api_key": "sk-ant-api03-YOUR_KEY_HERE"
  }
}
```

---

## Step 6 — Run ARIEL

```
cd C:\Users\YourUser\Desktop\ARIEL
python start.py
```

Your browser will open automatically with the ARIEL interface at http://localhost:8501.

---

## Troubleshooting

| Issue | Solution |
|---|---|
| `python` is not recognized | Reinstall Python and check "Add to PATH" |
| `pip install` errors | Run CMD as Administrator |
| `sentence-transformers` takes very long | Normal — downloads large models (~400MB on first install) |
| Streamlit doesn't open the browser | Open http://localhost:8501 manually |
| `pyautogui` screenshot errors | Verify Pillow is installed: `pip install Pillow` |
| Port 8501 already in use | Close other Streamlit instances or use: `streamlit run gui.py --server.port 8502` |
