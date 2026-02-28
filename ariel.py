"""
start.py — Main launcher for the ARIEL system.

This script is the single entry point that boots up all subsystems
as independent processes (scheduler, telegram bot, web GUI).

Security is handled entirely by the GUI (login screen and first-time
password setup). However, start.py optionally accepts the password as
a command-line argument for advanced users or scripted launches:
    python start.py MyPassword

Usage:
    python start.py              (GUI handles authentication)
    python start.py MyPassword   (password via argument — for scripts)
    (Press Ctrl+C to shut down all services)
"""

import subprocess
import sys
import time
import os
from pathlib import Path
from core.utils import load_json, get_translations
from core.agent import ARIELAgent
from core.security import (
    is_password_set, verify_password,
    get_session_key, SESSION_KEY_ENV
)

# ── Project root directory ──────────────────────────────────────────
BASE_DIR = Path(__file__).parent
TMP_DIR = BASE_DIR / "tmp"
TMP_DIR.mkdir(exist_ok=True)
PID_DIR = TMP_DIR

# ── Load configuration and translations ─────────────────────────────
config = load_json(BASE_DIR / "settings" / "config.json")
lang_code = config.get("agent", {}).get("language", "en")
t = get_translations(lang_code)["start"]

# ── Read the canonical version from the agent class ─────────────────
VERSION = ARIELAgent.VERSION


def write_pid(name: str, process: subprocess.Popen):
    """Write a .pid file containing the PID of a launched process."""
    (PID_DIR / f"{name}.pid").write_text(str(process.pid))


def cleanup_pids():
    """Remove all .pid files from the tmp/ directory."""
    for pid_file in PID_DIR.glob("*.pid"):
        pid_file.unlink(missing_ok=True)


# ── Banner ──────────────────────────────────────────────────────────
print("========================================")
print(f" 🤖 {t['header']} v{VERSION}")
print("========================================\n")


# ═══════════════════════════════════════════════════════════════════
#  OPTIONAL PASSWORD (command-line argument only)
#  If no argument is given, the GUI handles login visually.
# ═══════════════════════════════════════════════════════════════════

session_key = None

if len(sys.argv) > 1 and is_password_set():
    password = sys.argv[1]
    if verify_password(password):
        session_key = get_session_key(password)
        if lang_code == "es":
            print("✅ Autenticación correcta.\n")
        else:
            print("✅ Authentication successful.\n")
    else:
        if lang_code == "es":
            print("❌ Contraseña incorrecta.")
        else:
            print("❌ Incorrect password.")
        sys.exit(1)


# ═══════════════════════════════════════════════════════════════════
#  LAUNCH SUBSYSTEMS
# ═══════════════════════════════════════════════════════════════════

cleanup_pids()

# Prepare environment for child processes
child_env = os.environ.copy()
if session_key:
    child_env[SESSION_KEY_ENV] = session_key

processes = []

# On Windows, hide the console window for background subprocesses
kwargs = {}
if sys.platform == "win32":
    kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW

# ── 1. Start the Scheduler ──────────────────────────────────────────
print(f"⏰ [1/3] {t['starting_scheduler']}")
p_sch = subprocess.Popen(
    [sys.executable, "gateways/scheduler.py"],
    cwd=str(BASE_DIR), env=child_env, **kwargs
)
write_pid("scheduler", p_sch)
processes.append(p_sch)

# ── 2. Start Telegram bot (only if enabled) ─────────────────────────
tg_config = config.get("integrations", {}).get("telegram", {})
tg_has_token = bool(tg_config.get("bot_token"))
tg_enabled = tg_config.get("enabled", False)
# If a password is configured but we don't have the session key yet
# (user didn't pass it via CLI), Telegram can't decrypt its token.
# The GUI will auto-start the bot after the user logs in visually.
needs_key = is_password_set() and not session_key

if tg_enabled and tg_has_token and not needs_key:
    print(f"📱 [2/3] {t['starting_telegram']}")
    p_tg = subprocess.Popen(
        [sys.executable, "gateways/telegram_bot.py"],
        cwd=str(BASE_DIR), env=child_env, **kwargs
    )
    write_pid("telegram", p_tg)
    processes.append(p_tg)
elif tg_enabled and needs_key:
    if lang_code == "es":
        print(f"📱 [2/3] Telegram se iniciará tras el login en la GUI.")
    else:
        print(f"📱 [2/3] Telegram will start after GUI login.")
else:
    print(f"📱 [2/3] {t['telegram_disabled']}")

# ── 3. Start the Web GUI (Streamlit) ────────────────────────────────
print(f"🖥️ [3/3] {t['starting_gui']}\n")
p_gui = subprocess.Popen([
    sys.executable, "-m", "streamlit", "run", "core/gui.py",
    "--server.fileWatcherType", "none"
], cwd=str(BASE_DIR), env=child_env)
write_pid("gui", p_gui)
processes.append(p_gui)

print(f"✅ {t['all_online']}")

# ── Keep the launcher alive until Ctrl+C ────────────────────────────
try:
    while True:
        time.sleep(1)
except KeyboardInterrupt:
    print(f"\n🛑 {t['shutting_down']}")
    for p in processes:
        p.terminate()
    cleanup_pids()
    print(f"✅ {t['shutdown_complete']}")