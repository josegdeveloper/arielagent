"""
ariel.py — Central orchestrator for the ARIEL system.

This script is the single entry point that boots up all subsystems.
Unlike previous versions where each process had its own ARIELAgent,
the orchestrator now owns the ONLY agent instance and exposes it
via a local IPC socket. All other processes (GUI, Telegram, WhatsApp,
Scheduler) are thin clients that connect to this socket.

Architecture:
    ┌──────────────┐
    │  ariel.py    │  ← owns the single ARIELAgent
    │  IPC server  │  ← socket in tmp/ariel.sock (or TCP on Windows)
    └──────┬───────┘
           │
    ┌──────┼──────────────────────┐
    │      │      │               │
   GUI  Telegram WhatsApp  Scheduler
 (client) (client) (client)  (client)

Security is handled by the GUI (login screen and first-time setup).
The orchestrator optionally accepts the password as a CLI argument:
    python ariel.py              (GUI handles authentication)
    python ariel.py MyPassword   (password via argument — for scripts)
    (Press Ctrl+C to shut down all services)

────────────────────────────────────────────────────────────────────
VERSION HISTORY
────────────────────────────────────────────────────────────────────
v1.20.0  - Model-agnostic: LLM provider abstraction layer.
           New core/llm_provider.py (AnthropicProvider + OpenAIProvider).
           Supports LM Studio, Ollama, OpenAI, and any OpenAI-compatible API.
           Thinking tag cleanup for reasoning models (Qwen, DeepSeek, etc.).
           New send_whatsapp_message tool (proactive outbound via IPC).
           GUI: provider selector + base URL field in Settings.
v1.19.0  - Central orchestrator with IPC socket server.
           Single ARIELAgent instance shared by all subsystems.
           New core/ipc.py module (ArielServer + ArielClient).
           GUI, bots, and scheduler are now thin IPC clients.
           Eliminates 4x duplicated agent/API-client instances.
v1.18.0  - WhatsApp gateway via neonize (WhatsApp Web protocol).
           QR code pairing displayed in the GUI (Connectors modal).
           Dual-layer security: contact check + passphrase authorization.
v1.17.0  - Hybrid screen control: UI Automation (Accessibility Tree)
           as primary method + Computer Use (Anthropic vision) as
           optional fallback.
v1.16.0  - Initial public release.
────────────────────────────────────────────────────────────────────
"""

import subprocess
import sys
import time
import os
from pathlib import Path
from core.utils import load_json, get_translations
from core.agent import ARIELAgent
from core.ipc import ArielServer
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
        print(f"✅ {t.get('auth_ok', 'Authentication successful.')}\n")
    else:
        print(f"❌ {t.get('auth_fail', 'Incorrect password.')}")
        sys.exit(1)


# ═══════════════════════════════════════════════════════════════════
#  CREATE THE SINGLE ARIEL AGENT & START IPC SERVER
#  This is THE central instance — all clients talk to it via socket.
# ═══════════════════════════════════════════════════════════════════

cleanup_pids()

# Prepare environment for child processes
child_env = os.environ.copy()
if session_key:
    # Set for the orchestrator process itself (agent needs it to decrypt tokens)
    os.environ[SESSION_KEY_ENV] = session_key
    child_env[SESSION_KEY_ENV] = session_key

print(f"🧠 {t.get('initializing_agent', 'Initializing ARIEL agent...')}")

agent = ARIELAgent()

# Warn if API key is encrypted but no session key available yet.
# The GUI will send the session key after login via set_session_key IPC command.
_api_key = config.get("api", {}).get("api_key", "")
if _api_key.startswith("ENC:") and not session_key:
    print(f"⚠️  {t.get('api_key_encrypted', 'API Key encrypted. Agent will re-authenticate after GUI login.')}")

print(f"🔌 {t.get('starting_ipc', 'Starting IPC server...')}")

ipc_server = ArielServer(agent, BASE_DIR, logger=agent.logger)
ipc_server.start()


# ═══════════════════════════════════════════════════════════════════
#  LAUNCH SUBSYSTEMS (all are thin IPC clients now)
# ═══════════════════════════════════════════════════════════════════

processes = []

# On Windows, hide the console window for background subprocesses
kwargs = {}
if sys.platform == "win32":
    kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW

# ── 1. Start the Scheduler ──────────────────────────────────────────
print(f"⏰ [1/4] {t['starting_scheduler']}")
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
    print(f"📱 [2/4] {t['starting_telegram']}")
    p_tg = subprocess.Popen(
        [sys.executable, "gateways/telegram_bot.py"],
        cwd=str(BASE_DIR), env=child_env, **kwargs
    )
    write_pid("telegram", p_tg)
    processes.append(p_tg)
elif tg_enabled and needs_key:
    print(f"📱 [2/4] {t.get('tg_after_login', 'Telegram will start after GUI login.')}")
else:
    print(f"📱 [2/4] {t['telegram_disabled']}")

# ── 3. Start WhatsApp bot (only if enabled) ─────────────────────────
wa_config = config.get("integrations", {}).get("whatsapp", {})
wa_enabled = wa_config.get("enabled", False)

if wa_enabled and not needs_key:
    print(f"📱 [3/4] {t.get('starting_whatsapp', 'Starting WhatsApp...')}")
    p_wa = subprocess.Popen(
        [sys.executable, "gateways/whatsapp_bot.py"],
        cwd=str(BASE_DIR), env=child_env, **kwargs
    )
    write_pid("whatsapp", p_wa)
    processes.append(p_wa)
elif wa_enabled and needs_key:
    print(f"📱 [3/4] {t.get('wa_after_login', 'WhatsApp will start after GUI login.')}")
else:
    print(f"📱 [3/4] {t.get('whatsapp_disabled', 'WhatsApp disabled.')}")

# ── 4. Start the Web GUI (Streamlit) ────────────────────────────────
print(f"🖥️ [4/4] {t['starting_gui']}\n")
p_gui = subprocess.Popen([
    sys.executable, "-m", "streamlit", "run", "core/gui.py",
    "--server.fileWatcherType", "none"
], cwd=str(BASE_DIR), env=child_env)
write_pid("gui", p_gui)
processes.append(p_gui)

print(f"✅ {t['all_online']}")

# ── Keep the orchestrator alive until Ctrl+C ─────────────────────────
try:
    while True:
        time.sleep(1)
except KeyboardInterrupt:
    print(f"\n🛑 {t['shutting_down']}")
    ipc_server.stop()
    for p in processes:
        p.terminate()
    cleanup_pids()
    print(f"✅ {t['shutdown_complete']}")
