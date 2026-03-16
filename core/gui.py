"""
core/gui.py — Streamlit-based web dashboard for the ARIEL agent.

This is the main user interface. It provides:
  - A chat tab where the user interacts with the AI agent.
  - Short-term and long-term memory viewers.
  - A debug/logs tab for monitoring sessions.
  - Sidebar modals for settings, profiles, connectors, tools, and tasks.

All user-facing text is loaded from language JSON files (en.json / es.json)
via the translation dict 't', ensuring full multi-language support.

Launched automatically by start.py, or manually with:
    streamlit run core/gui.py --server.fileWatcherType none
"""

import streamlit as st
import json
import os
import subprocess
import sys
import uuid
import datetime
import base64
from pathlib import Path
from core.agent import ARIELAgent
from core.ipc import ArielClient
from core.utils import get_translations, load_json
from core.security import (
    is_password_set, verify_password, get_session_key,
    setup_password, encrypt_token, decrypt_if_needed,
    encrypt_existing_tokens, change_password,
    SESSION_KEY_ENV
)

# ── Load configuration and translation strings ──────────────────────
BASE_DIR = Path(__file__).parent.parent
TMP_DIR = BASE_DIR / "tmp"
TMP_DIR.mkdir(exist_ok=True)
_tmp_config = load_json(BASE_DIR / "settings" / "config.json")
lang_code = _tmp_config.get("agent", {}).get("language", "en")
t = get_translations(lang_code)["gui"]


# ═══════════════════════════════════════════════════════════════════
#  HELPER FUNCTIONS
# ═══════════════════════════════════════════════════════════════════

def kill_previous_process(pid_name: str):
    """Kill a process identified by a .pid file in tmp/, then remove the file."""
    pid_file = TMP_DIR / pid_name
    if pid_file.exists():
        try:
            old_pid = int(pid_file.read_text().strip())
            if sys.platform == "win32":
                subprocess.run(["taskkill", "/F", "/T", "/PID", str(old_pid)], capture_output=True)
            else:
                os.kill(old_pid, 9)
        except Exception:
            pass
        finally:
            pid_file.unlink(missing_ok=True)


def is_process_running(pid_name: str) -> bool:
    """Check if the process recorded in a .pid file (in tmp/) is still alive."""
    pid_file = TMP_DIR / pid_name
    if not pid_file.exists():
        return False
    try:
        pid = int(pid_file.read_text().strip())
        if sys.platform == "win32":
            # On Windows, ask tasklist if the PID is currently active
            result = subprocess.run(
                ["tasklist", "/FI", f"PID eq {pid}"],
                capture_output=True, text=True
            )
            return str(pid) in result.stdout
        else:
            # On Unix, signal 0 checks existence without killing
            os.kill(pid, 0)
            return True
    except (ValueError, ProcessLookupError, PermissionError, OSError):
        # Process is dead — clean up the orphan PID file
        pid_file.unlink(missing_ok=True)
        return False


def save_json_file(filepath, data):
    """Persist a Python dict to disk as formatted JSON."""
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


# ═══════════════════════════════════════════════════════════════════
#  PAGE CONFIGURATION & GLOBAL STYLES
# ═══════════════════════════════════════════════════════════════════

st.set_page_config(
    page_title=t.get("page_title", "ARIEL Dashboard"),
    page_icon=str(BASE_DIR / "profiles" / "ariel-logo.png"),
    layout="wide",
    # Hide Streamlit's default "Get Help / Report a bug / About" menu items
    menu_items={'Get Help': None, 'Report a bug': None, 'About': None}
)

# Inject custom CSS to tighten layout and hide Streamlit's native header
st.markdown("""
    <style>
        .block-container { padding-top: 2rem !important; }
        [data-testid="stSidebar"] { min-width: 220px !important; max-width: 220px !important; }
        /* Hide the native Streamlit header (Deploy button, 3-dot menu, top bar) */
        [data-testid="stHeader"] { display: none !important; }
        /* Add bottom padding so the fixed chat input bar doesn't overlap the last message.
           The chat_input widget floats at the bottom of the viewport, so we need enough
           space below the last message for it to scroll past the input bar. */
        [data-testid="stChatMessageContainer"] { padding-bottom: 150px; }
        .stChatFloatingInputContainer { padding-top: 10px; }
        [data-testid="stBottomBlockContainer"] { padding-top: 10px; }
    </style>
    <script>
        // Auto-scroll the chat container to the bottom after each Streamlit rerun.
        // Uses a brief delay to ensure all content has been rendered before scrolling.
        (function() {
            function scrollChat() {
                const containers = document.querySelectorAll('[data-testid="stChatMessageContainer"]');
                containers.forEach(c => c.scrollTop = c.scrollHeight);
            }
            setTimeout(scrollChat, 300);
            setTimeout(scrollChat, 800);
        })();
    </script>
""", unsafe_allow_html=True)


# ═══════════════════════════════════════════════════════════════════
#  LOGIN / SETUP GATE
#  Handles two scenarios entirely within the GUI:
#    1. No password set yet → offer to create one (first-time setup)
#    2. Password exists → require login before showing the dashboard
#  If launched from start.py with a valid session key in the env var,
#  the login screen is skipped automatically.
# ═══════════════════════════════════════════════════════════════════

def _show_ariel_header():
    """Display the centered ARIEL logo header used on login/setup/loading screens."""
    _logo_path = BASE_DIR / "profiles" / "ariel-logo.png"
    if _logo_path.exists():
        with open(_logo_path, "rb") as _img:
            _b64 = base64.b64encode(_img.read()).decode()
        _logo_html = f"<img src='data:image/png;base64,{_b64}' style='width:50px; height:50px; border-radius:12px; vertical-align:middle; margin-right:12px; margin-bottom:6px;'>"
    else:
        _logo_html = "🤖 "
    st.markdown(
        f"<div style='text-align:center; margin-top:60px;'>"
        f"<h1>{_logo_html}ARIEL</h1>"
        f"</div>",
        unsafe_allow_html=True
    )

# Check if we already have a valid session key (from start.py or previous login)
if "session_key" not in st.session_state:
    env_key = os.environ.get(SESSION_KEY_ENV)
    if env_key:
        st.session_state.session_key = env_key

if is_password_set():
    # ── Password exists: require login ──────────────────────────
    if "session_key" not in st.session_state:
        _show_ariel_header()
        st.markdown(
            f"<p style='text-align:center; color:gray;'>"
            f"{t.get('login_subtitle', 'Enter your password to continue')}</p>",
            unsafe_allow_html=True
        )
        col_left, col_center, col_right = st.columns([1, 2, 1])
        with col_center:
            login_pw = st.text_input(
                t.get("login_password", "Password"),
                type="password", key="login_password_input"
            )
            if st.button(t.get("login_button", "🔓 Log in"), use_container_width=True):
                if verify_password(login_pw):
                    st.session_state.session_key = get_session_key(login_pw)
                    os.environ[SESSION_KEY_ENV] = st.session_state.session_key
                    # Send session key to the orchestrator so it can decrypt API tokens
                    try:
                        ArielClient(BASE_DIR).set_session_key(st.session_state.session_key)
                    except Exception:
                        pass  # Orchestrator may not be ready yet — IPC init will retry
                    st.rerun()
                else:
                    st.error(t.get("login_error", "❌ Incorrect password."))
        st.stop()

else:
    # ── No password yet: offer first-time setup ─────────────────
    if "session_key" not in st.session_state:
        _show_ariel_header()
        st.markdown(
            f"<p style='text-align:center; color:gray;'>"
            f"{t.get('setup_subtitle', 'Welcome! You can protect ARIEL with a password.')}</p>",
            unsafe_allow_html=True
        )
        col_left, col_center, col_right = st.columns([1, 2, 1])
        with col_center:
            setup_pw1 = st.text_input(
                t.get("setup_new_password", "New password"),
                type="password", key="setup_pw1"
            )
            setup_pw2 = st.text_input(
                t.get("setup_repeat_password", "Repeat password"),
                type="password", key="setup_pw2"
            )

            if st.button(t.get("setup_create_button", "🔒 Create password"), use_container_width=True):
                if not setup_pw1 or len(setup_pw1) < 4:
                    st.error(t.get("setup_too_short", "❌ Must be at least 4 characters."))
                elif setup_pw1 != setup_pw2:
                    st.error(t.get("setup_mismatch", "❌ Passwords don't match."))
                else:
                    setup_password(setup_pw1)
                    session_key = get_session_key(setup_pw1)
                    encrypt_existing_tokens(session_key)
                    st.session_state.session_key = session_key
                    os.environ[SESSION_KEY_ENV] = session_key
                    # Send session key to the orchestrator so it can decrypt API tokens
                    try:
                        ArielClient(BASE_DIR).set_session_key(session_key)
                    except Exception:
                        pass  # Orchestrator may not be ready yet — IPC init will retry
                    st.rerun()
        st.stop()

# Ensure the env var is always set for child processes started from GUI
if "session_key" in st.session_state:
    os.environ[SESSION_KEY_ENV] = st.session_state.session_key


# ═══════════════════════════════════════════════════════════════════
#  AUTO-START BOTS AFTER LOGIN
#  If Telegram/WhatsApp are enabled but not running (because start.py
#  couldn't decrypt tokens without the password), start them now that
#  we have the session key available.
# ═══════════════════════════════════════════════════════════════════

if "bots_autostarted" not in st.session_state:
    _bot_kwargs = {"creationflags": subprocess.CREATE_NO_WINDOW} if sys.platform == "win32" else {}

    # Auto-start Telegram
    tg_conf = _tmp_config.get("integrations", {}).get("telegram", {})
    if tg_conf.get("enabled", False) and tg_conf.get("bot_token"):
        if not is_process_running("telegram.pid"):
            try:
                kill_previous_process("telegram.pid")
                p_tg = subprocess.Popen(
                    [sys.executable, "gateways/telegram_bot.py"],
                    cwd=str(BASE_DIR), **_bot_kwargs
                )
                (TMP_DIR / "telegram.pid").write_text(str(p_tg.pid))
            except Exception:
                pass

    # Auto-start WhatsApp
    wa_conf = _tmp_config.get("integrations", {}).get("whatsapp", {})
    if wa_conf.get("enabled", False):
        if not is_process_running("whatsapp.pid"):
            try:
                kill_previous_process("whatsapp.pid")
                p_wa = subprocess.Popen(
                    [sys.executable, "gateways/whatsapp_bot.py"],
                    cwd=str(BASE_DIR), **_bot_kwargs
                )
                (TMP_DIR / "whatsapp.pid").write_text(str(p_wa.pid))
            except Exception:
                pass

    st.session_state.bots_autostarted = True


# ═══════════════════════════════════════════════════════════════════
#  IPC CLIENT INITIALIZATION (runs once per Streamlit session)
#  Connects to the orchestrator (ariel.py) via local socket.
#  The orchestrator owns the agent — we're just a thin UI client.
# ═══════════════════════════════════════════════════════════════════

if "ipc" not in st.session_state:
    try:
        _show_ariel_header()
        st.markdown(
            f"""
            <div style='text-align:center; color:gray;'>
                <p>{t.get('loading_agent', '🤖 Starting ARIEL... (first launch may take a moment while AI models are downloaded)')}</p>
                <div style='display:inline-block; width:30px; height:30px; border:3px solid #ddd;
                     border-top:3px solid #888; border-radius:50%;
                     animation:spin 1s linear infinite; margin-top:10px;'></div>
            </div>
            <style>@keyframes spin {{ from{{transform:rotate(0deg)}} to{{transform:rotate(360deg)}} }}</style>
            """,
            unsafe_allow_html=True
        )

        # Wait for the orchestrator to be ready (it may still be loading models)
        import time as _time
        _ipc = ArielClient(BASE_DIR)
        _connected = False
        for _attempt in range(30):
            if _ipc.ping():
                _connected = True
                break
            _time.sleep(1)

        if not _connected:
            st.error(t.get("error_ipc",
                "❌ Could not connect to the ARIEL orchestrator. "
                "Make sure ariel.py is running."))
            st.stop()

        st.session_state.ipc = _ipc

        # If we have a session key (from login or CLI), send it to the
        # orchestrator so it can decrypt ENC:-prefixed API tokens.
        if "session_key" in st.session_state:
            _ipc.set_session_key(st.session_state.session_key)

        st.session_state.messages = []

        # ── First-time welcome: if user profile is empty, greet them ──
        _user_prof = load_json(BASE_DIR / "profiles" / "user.json")
        if not _user_prof.get("identity", {}).get("name"):
            st.session_state.messages.append({
                "role": "assistant",
                "content": t.get("welcome_message", "👋 Hello! I'm ARIEL. Tell me about yourself!")
            })

        st.rerun()
    except Exception as e:
        st.error(f"{t.get('error_init', 'Error initializing ARIEL')}: {e}")
        st.stop()


# ═══════════════════════════════════════════════════════════════════
#  MODAL 1: SETTINGS
#  Allows the user to configure API, model, temperature, language, etc.
# ═══════════════════════════════════════════════════════════════════

@st.dialog(t.get("settings_title", "⚙️ System Settings"))
def settings_modal():
    config_path = BASE_DIR / "settings" / "config.json"
    config_data = load_json(config_path)

    api_conf = config_data.get("api", {})
    agent_conf = config_data.get("agent", {})
    log_conf = config_data.get("logging", {})

    st.write(t.get("settings_desc", "Adjust ARIEL's behavior."))

    # -- API Provider --
    provider_options = ["anthropic", "openai"]
    provider_labels = [
        t.get("provider_anthropic", "Anthropic (Claude)"),
        t.get("provider_openai", "OpenAI Compatible (LM Studio, Ollama, GPT...)")
    ]
    current_provider = api_conf.get("provider", "anthropic")
    provider_idx = provider_options.index(current_provider) if current_provider in provider_options else 0

    new_provider = st.selectbox(
        t.get("set_provider", "Provider"), provider_labels,
        index=provider_idx,
        help=t.get("help_provider", "The AI engine.")
    )
    new_provider_code = provider_options[provider_labels.index(new_provider)]

    # -- API Key & Base URL --
    col1, col2 = st.columns(2)
    with col1:
        display_api_key = decrypt_if_needed(api_conf.get("api_key", ""))
        key_help = t.get("help_api_key", "Your secret key.")
        if new_provider_code == "openai":
            key_help = t.get("help_api_key_local", "API key. For local servers (LM Studio, Ollama) leave empty or type anything.")
        new_api_key = st.text_input(
            t.get("set_api_key", "API Key"),
            value=display_api_key, type="password",
            help=key_help
        )
        # Show warning only for Anthropic if key is missing
        if new_provider_code == "anthropic" and (not new_api_key or not new_api_key.strip() or new_api_key.strip().startswith("[")):
            st.markdown(
                f"<span style='color:red; font-size:0.85em;'>{t.get('api_key_missing', '⚠️ API Key needs to be introduced')}</span>",
                unsafe_allow_html=True
            )
    with col2:
        # Base URL: only relevant for OpenAI-compatible providers
        default_url = api_conf.get("base_url", "")
        if new_provider_code == "openai" and not default_url:
            default_url = "http://localhost:1234/v1"
        new_base_url = st.text_input(
            t.get("set_base_url", "🌐 Base URL"),
            value=default_url if new_provider_code == "openai" else "",
            disabled=(new_provider_code != "openai"),
            help=t.get("help_base_url", "Server URL. LM Studio: http://localhost:1234/v1 · Ollama: http://localhost:11434/v1")
        )

    # -- Model --
    new_model = st.text_input(
        t.get("set_model", "AI Model"),
        value=api_conf.get("model", "claude-sonnet-4-5-20250929"),
        help=t.get("help_model", "The specific AI brain.")
    )

    # -- Creativity (temperature) --
    new_temp = st.slider(
        t.get("set_temp", "Creativity"), 0.0, 1.0,
        float(api_conf.get("temperature", 0.0)), 0.1,
        help=t.get("help_temp", "0.0 is strict, 1.0 is creative.")
    )

    # -- Max tokens & Max steps --
    col3, col4 = st.columns(2)
    with col3:
        new_tokens = st.number_input(
            t.get("set_tokens", "Max Tokens"), 100, 8192,
            int(api_conf.get("max_tokens", 4096)),
            help=t.get("help_tokens", "Max response length.")
        )
    with col4:
        new_steps = st.number_input(
            t.get("set_steps", "Max Steps"), 1, 50,
            int(agent_conf.get("max_steps_per_task", 15)),
            help=t.get("help_steps", "Attempts before giving up.")
        )

    st.markdown("---")

    # -- Language & Log destination --
    col5, col6 = st.columns(2)
    with col5:
        current_lang = agent_conf.get("language", "en")
        lang_codes = ["es", "en"]
        lang_labels = [t.get("lang_spanish", "Spanish"), t.get("lang_english", "English")]
        lang_idx = lang_codes.index(current_lang) if current_lang in lang_codes else 1
        lang_display = st.selectbox(
            t.get("set_language", "Language"), lang_labels,
            index=lang_idx, help=t.get("help_lang", "Interface language.")
        )
        new_lang = lang_codes[lang_labels.index(lang_display)]

    with col6:
        current_dest = log_conf.get("output_dest", "Console")
        dest_idx = 0 if current_dest in ["Console", "Consola"] else 1
        new_log_dest_display = st.selectbox(
            t.get("set_log_dest", "Log Destination"),
            [t.get("log_dest_console", "Console"), t.get("log_dest_gui", "GUI Tab")],
            index=dest_idx, help=t.get("help_dest", "Where to send logs.")
        )
        new_log_dest = "Console" if new_log_dest_display == t.get("log_dest_console", "Console") else "GUI Tab"

    # ── Screen Control ──────────────────────────────────────────────
    st.markdown("---")
    st.markdown(f"**{t.get('set_screen_control', '🖥️ Screen Control')}**")

    sc_conf = config_data.get("screen_control", {})

    cu_fallback = st.toggle(
        t.get("set_cu_fallback", "Enable Computer Use fallback (vision-based, higher API cost)"),
        value=sc_conf.get("computer_use_fallback", False),
        help=t.get("help_cu_fallback",
            "When enabled, ARIEL can fall back to screenshot-based control "
            "for apps without accessibility support. This uses more API tokens.")
    )

    # -- Save button --
    if st.button(t.get("save_changes", "💾 Save Changes"), use_container_width=True):
        if "api" not in config_data: config_data["api"] = {}
        if "agent" not in config_data: config_data["agent"] = {}
        if "logging" not in config_data: config_data["logging"] = {"level": "INFO", "log_dir": "logs"}

        config_data["api"]["provider"] = new_provider_code
        # Encrypt the API key if a password is configured, otherwise save as plaintext
        if new_api_key and is_password_set() and "session_key" in st.session_state:
            config_data["api"]["api_key"] = encrypt_token(new_api_key, st.session_state.session_key)
        else:
            config_data["api"]["api_key"] = new_api_key
        config_data["api"]["base_url"] = new_base_url if new_provider_code == "openai" else ""
        config_data["api"]["model"] = new_model
        config_data["api"]["temperature"] = new_temp
        config_data["api"]["max_tokens"] = new_tokens
        config_data["agent"]["max_steps_per_task"] = new_steps
        config_data["agent"]["language"] = new_lang
        config_data["logging"]["output_dest"] = new_log_dest

        # Save screen control settings
        if "screen_control" not in config_data:
            config_data["screen_control"] = {"method": "ui_automation"}
        config_data["screen_control"]["computer_use_fallback"] = cu_fallback

        save_json_file(config_path, config_data)

        # Tell the orchestrator to reload config from disk so the
        # live agent picks up the new settings (model, temperature, etc.)
        st.session_state.ipc.reload_config()

        st.success(t.get("success_save", "Saved successfully!"))

    # ── Password management section ─────────────────────────────────
    st.markdown("---")
    with st.expander(t.get("pwd_section", "🔐 Password"), expanded=False):
        if is_password_set():
            # ── Change password ──────────────────────────────────
            st.markdown(f"**{t.get('pwd_change_title', 'Change password')}**")
            pwd_current = st.text_input(
                t.get("pwd_current", "Current password"),
                type="password", key="pwd_current"
            )
            pwd_new1 = st.text_input(
                t.get("pwd_new", "New password"),
                type="password", key="pwd_new1"
            )
            pwd_new2 = st.text_input(
                t.get("pwd_new_repeat", "Repeat new password"),
                type="password", key="pwd_new2"
            )
            if st.button(t.get("pwd_change_btn", "🔄 Change password"), use_container_width=True):
                if not verify_password(pwd_current):
                    st.error(t.get("pwd_wrong_current", "❌ Current password is incorrect."))
                elif len(pwd_new1) < 4:
                    st.error(t.get("setup_too_short", "❌ Must be at least 4 characters."))
                elif pwd_new1 != pwd_new2:
                    st.error(t.get("setup_mismatch", "❌ Passwords don't match."))
                else:
                    old_key = st.session_state.get("session_key", "")
                    new_key = change_password(old_key, pwd_new1)
                    st.session_state.session_key = new_key
                    os.environ[SESSION_KEY_ENV] = new_key
                    st.success(t.get("pwd_changed_ok", "✅ Password changed successfully."))

        else:
            # ── Create password (if not set) ─────────────────────
            st.markdown(f"**{t.get('pwd_create_title', 'Create a password')}**")
            st.caption(t.get("pwd_create_desc", "Encrypt your API and Telegram tokens on disk."))
            pwd_c1 = st.text_input(
                t.get("setup_new_password", "New password"),
                type="password", key="pwd_create1"
            )
            pwd_c2 = st.text_input(
                t.get("setup_repeat_password", "Repeat password"),
                type="password", key="pwd_create2"
            )
            if st.button(t.get("setup_create_button", "🔒 Create password"), key="pwd_create_btn", use_container_width=True):
                if len(pwd_c1) < 4:
                    st.error(t.get("setup_too_short", "❌ Must be at least 4 characters."))
                elif pwd_c1 != pwd_c2:
                    st.error(t.get("setup_mismatch", "❌ Passwords don't match."))
                else:
                    setup_password(pwd_c1)
                    new_key = get_session_key(pwd_c1)
                    encrypt_existing_tokens(new_key)
                    st.session_state.session_key = new_key
                    os.environ[SESSION_KEY_ENV] = new_key
                    st.success(t.get("pwd_changed_ok", "✅ Password created. Tokens encrypted."))


# ═══════════════════════════════════════════════════════════════════
#  MODAL 2: PROFILES
#  User profile (name, age, prefs) and Agent profile (personality).
# ═══════════════════════════════════════════════════════════════════

@st.dialog(t.get("profiles_title", "👤 Profiles"), width="large")
def profiles_modal():
    st.write(t.get("profiles_desc", "Edit user and agent profiles."))
    config_data = load_json(BASE_DIR / "settings" / "config.json")

    user_path = BASE_DIR / config_data.get("paths", {}).get("user_profile", "profiles/user.json")
    agent_path = BASE_DIR / config_data.get("paths", {}).get("agent_profile", "profiles/agent.json")

    user_data = load_json(user_path)
    agent_data = load_json(agent_path)

    # Two tabs: one for the user, one for the agent
    tab_u, tab_a = st.tabs([t.get("tab_user", "My Profile"), t.get("tab_agent", "Agent Profile")])

    # ── User Profile Tab ────────────────────────────────────────────
    with tab_u:
        st.markdown(f"#### {t.get('lbl_u_identity', 'Identity')}")
        c1, c2 = st.columns(2)
        with c1: u_name = st.text_input(t.get("lbl_name", "Name"), value=user_data.get("identity", {}).get("name", ""), help=t.get("help_name", "Your preferred name."))
        with c2: u_loc = st.text_input(t.get("lbl_location", "Location"), value=user_data.get("identity", {}).get("location", ""))

        c4, c5 = st.columns(2)
        with c4: u_tz = st.text_input(t.get("lbl_tz", "Timezone"), value=user_data.get("identity", {}).get("timezone", ""), help=t.get("help_tz", "Timezone for correct scheduling."))
        with c5: u_lang = st.text_input(t.get("lbl_lang", "Language"), value=user_data.get("identity", {}).get("language", ""))

        st.markdown(f"#### {t.get('lbl_u_work', 'Work & Hobbies')}")
        c6, c7 = st.columns(2)
        with c6: u_role = st.text_input(t.get("lbl_role", "Role"), value=user_data.get("work", {}).get("role", ""))
        with c7: u_ind = st.text_input(t.get("lbl_industry", "Industry"), value=user_data.get("work", {}).get("industry", ""))
        u_tools = st.text_input(t.get("lbl_tools", "Daily Tools"), value=", ".join(user_data.get("work", {}).get("tools_used_daily", [])))
        u_hobbies = st.text_input(t.get("lbl_hobbies", "Hobbies"), value=", ".join(user_data.get("hobbies", [])), help=t.get("help_hobbies", "Helps personalize responses."))

        # Collapsible section for communication & system preferences
        with st.expander(t.get("lbl_u_prefs", "Preferences")):
            st.caption(t.get("lbl_prefs_comm", "Communication"))
            c8, c9 = st.columns(2)
            with c8:
                u_tone = st.text_input(t.get("lbl_u_tone", "Preferred Tone"), value=user_data.get("preferences", {}).get("communication", {}).get("preferred_tone", ""), help=t.get("help_tone", "E.g., Direct, Friendly."))
                detail_options = ["low", "medium", "high"]
                curr_detail = user_data.get("preferences", {}).get("communication", {}).get("detail_level", "medium")
                det_idx = detail_options.index(curr_detail) if curr_detail in detail_options else 1
                u_detail = st.selectbox(t.get("lbl_u_detail", "Detail Level"), detail_options, index=det_idx, help=t.get("help_detail", "Short vs long answers."))
            with c9:
                st.markdown("<br>", unsafe_allow_html=True)
                u_conf = st.checkbox(t.get("lbl_u_conf_destructive", "Confirm destructive actions"), value=user_data.get("preferences", {}).get("communication", {}).get("confirm_before_destructive", True), help=t.get("help_conf_destructive", "Ask in the chat before deleting files or running dangerous commands."))
                u_notif = st.checkbox(t.get("lbl_u_notif_tg", "Notify via Telegram on completion"), value=user_data.get("preferences", {}).get("communication", {}).get("notify_telegram_on_complete", False), help=t.get("help_notif_tg", "Send a summary via Telegram when a task finishes."))

            st.caption(t.get("lbl_prefs_system", "System Paths & Apps"))
            c10, c11, c12 = st.columns(3)
            with c10: u_browser = st.text_input(t.get("lbl_browser", "Browser"), value=user_data.get("preferences", {}).get("system", {}).get("preferred_browser", ""))
            with c11: u_editor = st.text_input(t.get("lbl_editor", "Editor"), value=user_data.get("preferences", {}).get("system", {}).get("preferred_editor", ""))
            with c12: u_term = st.text_input(t.get("lbl_term", "Terminal"), value=user_data.get("preferences", {}).get("system", {}).get("preferred_terminal", ""))

            u_desk = st.text_input(t.get("lbl_desk", "Desktop"), value=user_data.get("preferences", {}).get("system", {}).get("desktop_path", ""))
            u_docs = st.text_input(t.get("lbl_docs", "Documents"), value=user_data.get("preferences", {}).get("system", {}).get("documents_path", ""))

        u_apps = st.text_input(t.get("lbl_apps", "Trusted Apps"), value=", ".join(user_data.get("trusted_apps", [])), help=t.get("help_apps", "Safe programs like Chrome or Excel."))
        u_notes = st.text_area(t.get("lbl_notes", "Notes"), value="\n".join(user_data.get("notes", [])), height=100, help=t.get("help_notes", "Extra rules to remember."))

    # ── Agent Profile Tab ───────────────────────────────────────────
    with tab_a:
        st.markdown(f"#### {t.get('lbl_a_identity', 'Identity')}")
        c13, c14 = st.columns(2)
        with c13: a_name = st.text_input(t.get("lbl_agent_name", "Name"), value=agent_data.get("identity", {}).get("name", ""))
        with c14: a_fname = st.text_input(t.get("lbl_agent_fname", "Full Name"), value=agent_data.get("identity", {}).get("full_name", ""))
        a_created = st.text_input(t.get("lbl_agent_created", "Creation"), value=agent_data.get("identity", {}).get("created", ""))

        st.markdown(f"#### {t.get('lbl_a_role', 'Role')}")
        a_pri = st.text_input(t.get("lbl_agent_role", "Primary Role"), value=agent_data.get("role", {}).get("primary", ""))
        a_sec = st.text_area(t.get("lbl_agent_sec", "Secondary Roles"), value="\n".join(agent_data.get("role", {}).get("secondary", [])), height=100, help=t.get("help_a_sec", "Other skills."))

        st.markdown(f"#### {t.get('lbl_a_pers', 'Personality')}")
        a_tone = st.text_input(t.get("lbl_agent_tone", "Tone"), value=agent_data.get("personality", {}).get("tone", ""))
        a_traits = st.text_area(t.get("lbl_agent_traits", "Traits"), value="\n".join(agent_data.get("personality", {}).get("traits", [])), height=150, help=t.get("help_a_traits", "Adjectives defining personality."))

        c15, c16 = st.columns(2)
        with c15:
            verb_opts = ["low", "medium", "high"]
            curr_verb = agent_data.get("personality", {}).get("communication_style", {}).get("verbosity", "medium")
            v_idx = verb_opts.index(curr_verb) if curr_verb in verb_opts else 1
            a_verb = st.selectbox(t.get("lbl_a_verb", "Verbosity"), verb_opts, index=v_idx, help=t.get("help_a_verb", "Low: direct. High: talkative."))
        with c16:
            st.markdown("<br>", unsafe_allow_html=True)
            a_emojis = st.checkbox(t.get("lbl_a_emojis", "Use Emojis"), value=agent_data.get("personality", {}).get("communication_style", {}).get("use_emojis", False))

    # -- Save both profiles with a single button --
    if st.button(t.get("save_changes", "💾 Save Changes"), use_container_width=True):
        # Rebuild the user profile dict from widget values
        user_data.setdefault("identity", {})
        user_data["identity"]["name"] = u_name
        user_data["identity"]["location"] = u_loc
        user_data["identity"]["timezone"] = u_tz
        user_data["identity"]["language"] = u_lang

        user_data.setdefault("work", {})
        user_data["work"]["role"] = u_role
        user_data["work"]["industry"] = u_ind
        user_data["work"]["tools_used_daily"] = [x.strip() for x in u_tools.split(",") if x.strip()]

        user_data["hobbies"] = [x.strip() for x in u_hobbies.split(",") if x.strip()]
        user_data["trusted_apps"] = [x.strip() for x in u_apps.split(",") if x.strip()]
        user_data["notes"] = [x.strip() for x in u_notes.split("\n") if x.strip()]

        user_data.setdefault("preferences", {})
        user_data["preferences"].setdefault("communication", {})
        user_data["preferences"]["communication"]["preferred_tone"] = u_tone
        user_data["preferences"]["communication"]["detail_level"] = u_detail
        user_data["preferences"]["communication"]["confirm_before_destructive"] = u_conf
        user_data["preferences"]["communication"]["notify_telegram_on_complete"] = u_notif

        user_data.setdefault("system", {})
        user_data["preferences"]["system"]["preferred_browser"] = u_browser
        user_data["preferences"]["system"]["preferred_editor"] = u_editor
        user_data["preferences"]["system"]["preferred_terminal"] = u_term
        user_data["preferences"]["system"]["desktop_path"] = u_desk
        user_data["preferences"]["system"]["documents_path"] = u_docs

        # Rebuild the agent profile dict from widget values
        agent_data.setdefault("identity", {})
        agent_data["identity"]["name"] = a_name
        agent_data["identity"]["full_name"] = a_fname
        agent_data["identity"]["created"] = a_created

        agent_data.setdefault("role", {})
        agent_data["role"]["primary"] = a_pri
        agent_data["role"]["secondary"] = [x.strip() for x in a_sec.split("\n") if x.strip()]

        agent_data.setdefault("personality", {})
        agent_data["personality"]["tone"] = a_tone
        agent_data["personality"]["traits"] = [x.strip() for x in a_traits.split("\n") if x.strip()]

        agent_data["personality"].setdefault("communication_style", {})
        agent_data["personality"]["communication_style"]["verbosity"] = a_verb
        agent_data["personality"]["communication_style"]["use_emojis"] = a_emojis

        # Persist both profiles to disk
        save_json_file(user_path, user_data)
        save_json_file(agent_path, agent_data)
        st.success(t.get("success_save", "Saved!"))


# ═══════════════════════════════════════════════════════════════════
#  MODAL 3: TOOLS
#  Read-only view of all registered tools (name, description, code).
# ═══════════════════════════════════════════════════════════════════

@st.dialog(t.get("tools_title", "🧰 Tools"), width="large")
def tools_modal():
    st.write(t.get("tools_desc", "Available tools for ARIEL:"))

    config_data = load_json(BASE_DIR / "settings" / "config.json")
    tool_index_path = config_data.get("paths", {}).get("tool_index", "settings/toolindex.json")
    tools_impl_path = config_data.get("paths", {}).get("tools", "settings/tools.json")

    index_data = load_json(BASE_DIR / tool_index_path)
    impl_data = load_json(BASE_DIR / tools_impl_path)

    tools_list = index_data.get("tools", [])
    implementations = impl_data.get("implementations", {})

    if not tools_list:
        st.warning(t.get("no_tools", "No tools found."))
    else:
        for tool in tools_list:
            name = tool.get('name', t.get("unnamed_tool", "Unnamed Tool"))
            with st.container(border=True):
                st.markdown(f"### 🛠️ {name}")
                st.write(tool.get('description', t.get("no_desc", "No description.")))

                impl = implementations.get(name)
                if impl:
                    tool_type = impl.get('type', 'python')
                    code = impl.get('code', t.get("lbl_code_na", "Code not available."))
                    lang = "python" if tool_type == "python" else "bash"

                    with st.expander(t.get("btn_view_code", "💻 View source code")):
                        st.caption(f"{t.get('lbl_tool_engine', 'Engine')}: **{tool_type.upper()}**")
                        input_schema = tool.get('input_schema', {})
                        if input_schema:
                            st.json(input_schema)
                        st.code(code, language=lang)


# ═══════════════════════════════════════════════════════════════════
#  MODAL 4: INTEGRATIONS (Connectors)
#  Configure and control external gateways (Telegram, WhatsApp, etc.).
# ═══════════════════════════════════════════════════════════════════

@st.dialog(t.get("integrations_title", "🔌 Connectors"), width="large")
def integrations_modal():
    st.write(t.get("integrations_desc", "Configure external gateways."))
    config_path = BASE_DIR / "settings" / "config.json"
    config_data = load_json(config_path)

    if "integrations" not in config_data:
        config_data["integrations"] = {}

    tg_data = config_data["integrations"].get("telegram", {})

    # ── Telegram section ────────────────────────────────────────────
    with st.expander(t.get("lbl_tg_section", "✈️ Telegram Bot"), expanded=False):
        tg_enabled = st.toggle(t.get("lbl_tg_enable", "Enable Telegram"), value=tg_data.get("enabled", False))
        # Decrypt the bot token for display (if encrypted)
        display_tg_token = decrypt_if_needed(tg_data.get("bot_token", ""))
        tg_token = st.text_input(t.get("lbl_tg_token", "Bot Token"), value=display_tg_token, type="password")
        tg_chat = st.text_input(t.get("lbl_tg_chat_id", "Allowed Chat ID"), value=tg_data.get("chat_id", ""))

        # Save Telegram configuration
        if st.button(t.get("save_changes", "💾 Save Changes"), key="save_tg"):
            if "integrations" not in config_data: config_data["integrations"] = {}
            if "telegram" not in config_data["integrations"]: config_data["integrations"]["telegram"] = {}

            config_data["integrations"]["telegram"]["enabled"] = tg_enabled
            # Encrypt the bot token if a password is configured
            if tg_token and is_password_set() and "session_key" in st.session_state:
                config_data["integrations"]["telegram"]["bot_token"] = encrypt_token(tg_token, st.session_state.session_key)
            else:
                config_data["integrations"]["telegram"]["bot_token"] = tg_token
            config_data["integrations"]["telegram"]["chat_id"] = tg_chat

            save_json_file(config_path, config_data)
            st.session_state.ipc.reload_config()
            st.success(t.get("success_save", "Saved successfully!"))

        st.markdown("---")

        # Show current bot status by checking the PID file
        is_running = is_process_running("telegram.pid")

        st.info(f"{t.get('bot_status', 'Bot Status')}: **{t.get('bot_running', '🟢 Running') if is_running else t.get('bot_stopped', '🔴 Stopped')}**")

        # Start / Stop buttons
        col_start, col_stop = st.columns(2)
        with col_start:
            if st.button(t.get("btn_start_bot", "▶️ Start Bot"), disabled=is_running, use_container_width=True):
                # Kill any orphan bot process before starting a fresh one
                kill_previous_process("telegram.pid")

                # Launch the Telegram bot as a background subprocess
                # (it will decrypt its token via IPC from the orchestrator)
                kwargs = {"creationflags": subprocess.CREATE_NO_WINDOW} if sys.platform == "win32" else {"start_new_session": True}
                proc = subprocess.Popen(
                    [sys.executable, "gateways/telegram_bot.py"],
                    cwd=str(BASE_DIR),
                    **kwargs
                )
                # Write the PID so any component can track it
                (TMP_DIR / "telegram.pid").write_text(str(proc.pid))
                st.rerun()

        with col_stop:
            if st.button(t.get("btn_stop_bot", "⏹️ Stop Bot"), disabled=not is_running, use_container_width=True):
                # Terminate the bot and remove the PID file
                kill_previous_process("telegram.pid")
                st.rerun()

    # ── WhatsApp section ────────────────────────────────────────────
    wa_data = config_data["integrations"].get("whatsapp", {})

    with st.expander(t.get("lbl_wa_section", "💬 WhatsApp"), expanded=False):
        wa_enabled = st.toggle(t.get("lbl_wa_enable", "Enable WhatsApp"), value=wa_data.get("enabled", False))

        # Security passphrase
        wa_passphrase = st.text_input(
            t.get("lbl_wa_passphrase", "Security passphrase"),
            value=wa_data.get("passphrase", ""),
            type="password",
            help=t.get("help_wa_passphrase",
                "A secret phrase that contacts must send as their first message to activate ARIEL. "
                "Only contacts in your phone's address book who also know the passphrase can use the bot. "
                "Leave empty to accept all known contacts without a passphrase.")
        )

        # Save WhatsApp configuration
        if st.button(t.get("save_changes", "💾 Save Changes"), key="save_wa"):
            if "integrations" not in config_data: config_data["integrations"] = {}
            if "whatsapp" not in config_data["integrations"]: config_data["integrations"]["whatsapp"] = {}

            config_data["integrations"]["whatsapp"]["enabled"] = wa_enabled
            config_data["integrations"]["whatsapp"]["passphrase"] = wa_passphrase.strip()

            save_json_file(config_path, config_data)
            st.session_state.ipc.reload_config()
            st.success(t.get("success_save", "Saved successfully!"))

        st.markdown("---")

        # Show authorized devices (LIDs that have sent the passphrase)
        auth_file = BASE_DIR / "settings" / "whatsapp_authorized.json"
        if auth_file.exists():
            auth_data = load_json(auth_file)
            authorized = auth_data.get("authorized", {})
            if authorized:
                st.markdown(f"**{t.get('wa_authorized_title', 'Authorized devices')}** ({len(authorized)}):")
                for lid, info in authorized.items():
                    # Backwards compatible: old format is "name", new format is {"name": ..., "jid_user": ...}
                    if isinstance(info, dict):
                        display_name = info.get("name", lid)
                        jid_user = info.get("jid_user", "")
                        detail = f" · {jid_user}" if jid_user else ""
                    else:
                        display_name = info
                        detail = ""
                    st.caption(f"  ✅ {display_name}{detail}")

                if st.button(t.get("wa_revoke_all", "🗑️ Revoke all authorizations"), key="wa_revoke"):
                    save_json_file(auth_file, {"authorized": {}})
                    # Stop the bot so it doesn't keep the old session alive
                    kill_previous_process("whatsapp.pid")
                    # Delete the neonize session files so a fresh QR is shown on next start
                    _wa_session = BASE_DIR / "settings" / "whatsapp_session"
                    if _wa_session.exists():
                        _wa_session.unlink(missing_ok=True)
                    _wa_session2 = BASE_DIR / "settings" / "ariel_whatsapp"
                    if _wa_session2.exists():
                        _wa_session2.unlink(missing_ok=True)
                    # Clear the status file
                    _wa_status = TMP_DIR / "whatsapp_status.txt"
                    if _wa_status.exists():
                        _wa_status.unlink(missing_ok=True)
                    st.success(t.get("wa_revoked_full",
                        "All authorizations revoked and device unlinked. "
                        "Press ▶️ Start Bot to scan a new QR code."))
                    st.rerun()

        st.markdown("---")

        # Show current WhatsApp bot status
        wa_is_running = is_process_running("whatsapp.pid")

        # Read connection status from the status file
        wa_status_file = TMP_DIR / "whatsapp_status.txt"
        wa_conn_status = ""
        if wa_status_file.exists():
            try:
                wa_conn_status = wa_status_file.read_text(encoding="utf-8").strip()
            except Exception:
                pass

        if wa_is_running:
            if wa_conn_status == "connected":
                st.info(f"{t.get('bot_status', 'Bot Status')}: **{t.get('wa_connected', '🟢 Connected')}**")
            elif wa_conn_status in ("waiting_qr", "connecting", "starting"):
                st.warning(f"{t.get('bot_status', 'Bot Status')}: **{t.get('wa_connecting', '🟡 Waiting for QR scan...')}**")

                qr_path = TMP_DIR / "whatsapp_qr.png"
                if qr_path.exists():
                    st.image(str(qr_path), caption=t.get("wa_scan_qr", "Scan this QR code with WhatsApp → Settings → Linked Devices → Link a Device"), width=300)
                    st.caption(t.get("wa_qr_reopen", "After scanning, close this window and reopen Connectors to see the updated status."))
                else:
                    st.caption(t.get("wa_qr_loading", "QR code is being generated... Close and reopen this window in a few seconds."))
            else:
                st.info(f"{t.get('bot_status', 'Bot Status')}: **{t.get('bot_running', '🟢 Running')}** ({wa_conn_status})")
        else:
            st.info(f"{t.get('bot_status', 'Bot Status')}: **{t.get('bot_stopped', '🔴 Stopped')}**")

        # Start / Stop buttons
        col_wa_start, col_wa_stop = st.columns(2)
        with col_wa_start:
            if st.button(t.get("btn_start_bot", "▶️ Start Bot"), disabled=wa_is_running, use_container_width=True, key="wa_start"):
                kill_previous_process("whatsapp.pid")

                # Launch the WhatsApp bot as a background subprocess
                # (it uses IPC for all AI processing — no local session key needed)
                wa_kwargs = {"creationflags": subprocess.CREATE_NO_WINDOW} if sys.platform == "win32" else {"start_new_session": True}
                proc = subprocess.Popen(
                    [sys.executable, "gateways/whatsapp_bot.py"],
                    cwd=str(BASE_DIR),
                    **wa_kwargs
                )
                (TMP_DIR / "whatsapp.pid").write_text(str(proc.pid))
                st.rerun()

        with col_wa_stop:
            if st.button(t.get("btn_stop_bot", "⏹️ Stop Bot"), disabled=not wa_is_running, use_container_width=True, key="wa_stop"):
                kill_previous_process("whatsapp.pid")
                if wa_status_file.exists():
                    wa_status_file.unlink(missing_ok=True)
                st.rerun()

        # Help text
        st.caption(t.get("wa_help",
            "WhatsApp connects via Linked Devices (like WhatsApp Web). "
            "Scan the QR on first start. Security: only contacts in your phone's "
            "address book can interact with ARIEL, and if a passphrase is set, "
            "they must send it first to activate their access."))


# ═══════════════════════════════════════════════════════════════════
#  MODAL 5: SCHEDULED TASKS (Scheduler)
#  CRUD interface for automatic tasks that run on a schedule.
# ═══════════════════════════════════════════════════════════════════

@st.dialog(t.get("tasks_title", "⏱️ Scheduled Tasks"), width="large")
def tasks_modal():
    st.write(t.get("tasks_desc", "Configure automatic routines."))

    # Counter used to regenerate widget keys after a delete,
    # which avoids DuplicateWidgetID errors without a full rerun
    if "tasks_refresh" not in st.session_state:
        st.session_state.tasks_refresh = 0

    tasks_path = BASE_DIR / "settings" / "tasks.json"
    tasks_data = load_json(tasks_path)
    if "tasks" not in tasks_data:
        tasks_data["tasks"] = []

    # ── Form to add a new task ──────────────────────────────────────
    with st.expander(t.get("add_task_title", "➕ Add new task"), expanded=False):
        new_prompt = st.text_input(
            t.get("lbl_task_prompt", "Task Instruction"),
            placeholder=t.get("ph_task_prompt", "E.g., Check my inbox and summarize it...")
        )
        new_time = st.time_input(t.get("lbl_task_time", "Execution Time"), value=datetime.time(8, 0))

        st.write(t.get("lbl_task_days", "Days of the week"))
        cols = st.columns(7)
        day_keys = ["day_0", "day_1", "day_2", "day_3", "day_4", "day_5", "day_6"]
        default_days = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]

        selected_days = []
        for i, col in enumerate(cols):
            # All days checked by default
            is_checked = col.checkbox(t.get(day_keys[i], default_days[i]), value=True)
            if is_checked:
                selected_days.append(i)  # Store day index (0=Mon, 6=Sun)

        if st.button(t.get("btn_save_task", "💾 Save Task"), use_container_width=True):
            if new_prompt.strip() and selected_days:
                new_task = {
                    "id": str(uuid.uuid4())[:8],
                    "prompt": new_prompt.strip(),
                    "time": new_time.strftime("%H:%M"),
                    "days": selected_days,
                    "enabled": True
                }
                tasks_data["tasks"].append(new_task)
                save_json_file(tasks_path, tasks_data)
                st.success(t.get("success_save", "Saved!"))

    st.markdown("---")

    # ── List existing tasks (re-read to catch any changes) ──────────
    tasks_data = load_json(tasks_path)
    if "tasks" not in tasks_data:
        tasks_data["tasks"] = []

    if not tasks_data["tasks"]:
        st.info(t.get("no_tasks", "No scheduled tasks."))
    else:
        for idx, task in enumerate(tasks_data["tasks"]):
            c1, c2, c3 = st.columns([6, 2, 2])
            with c1:
                st.markdown(f"**{task.get('prompt', '')}**")

                # Convert day indices to translated day names for display
                day_names = [t.get(f"day_{d}", str(d)) for d in task.get("days", [])]
                st.caption(f"🕒 {task.get('time', '')} | 📅 {', '.join(day_names)}")

            with c2:
                # ON/OFF toggle (persists immediately on change)
                is_active = st.toggle(
                    t.get("lbl_toggle_on", "ON"),
                    value=task.get("enabled", True),
                    key=f"tog_{task['id']}_{st.session_state.tasks_refresh}"
                )
                if is_active != task.get("enabled"):
                    tasks_data["tasks"][idx]["enabled"] = is_active
                    save_json_file(tasks_path, tasks_data)

            with c3:
                # Delete button — increment refresh counter to regenerate keys
                if st.button(t.get("btn_delete_item", "🗑️ Delete"), key=f"del_task_{task['id']}_{st.session_state.tasks_refresh}", use_container_width=True):
                    tasks_data["tasks"].pop(idx)
                    save_json_file(tasks_path, tasks_data)
                    st.session_state.tasks_refresh += 1
                    st.rerun()
            st.markdown("---")


# ═══════════════════════════════════════════════════════════════════
#  SIDEBAR (Back-office navigation)
# ═══════════════════════════════════════════════════════════════════

with st.sidebar:
    # Show current version from the agent class
    v_actual = ARIELAgent.VERSION

    st.markdown(f"""
        <div style='margin-top: -50px;'>
            <div style='display: flex; justify-content: space-between; color: gray; font-size: 0.8em; margin-bottom: 0px;'>
                <span>Version: v{v_actual}</span>
                <span>(by JoseG)</span>
            </div>
            <hr style='margin-top: 5px; margin-bottom: 15px;'>
        </div>
    """, unsafe_allow_html=True)

    # Each button opens a modal dialog
    if st.button(t.get("btn_settings", "⚙️ Settings"), use_container_width=True): settings_modal()
    if st.button(t.get("btn_profiles", "👤 Profiles"), use_container_width=True): profiles_modal()
    if st.button(t.get("btn_connectors", "🔌 Connectors"), use_container_width=True): integrations_modal()
    if st.button(t.get("btn_tools", "🧰 Tools"), use_container_width=True): tools_modal()
    if st.button(t.get("btn_tasks", "⏱️ Tasks"), use_container_width=True): tasks_modal()

    # Clear chat history button
    if st.button(t.get("btn_clear_chat", "🗑️ Clear Chat"), use_container_width=True):
        st.session_state.messages = []
        st.rerun()


# ═══════════════════════════════════════════════════════════════════
#  MAIN AREA — Header with logo + quick language selector
# ═══════════════════════════════════════════════════════════════════

col_title, col_lang = st.columns([8, 2])

# Load the logo as Base64 to embed it inline in the HTML header
logo_path = BASE_DIR / "profiles" / "ariel-logo.png"
if logo_path.exists():
    with open(logo_path, "rb") as img_file:
        b64_img = base64.b64encode(img_file.read()).decode()
    img_html = f"<img src='data:image/png;base64,{b64_img}' style='width: 45px; height: 45px; vertical-align: middle; margin-right: 10px; border-radius: 10px; margin-bottom: 8px;'>"
else:
    img_html = "🤖"  # Fallback emoji if the image file is missing

with col_title:
    st.markdown(
        f"""
        <h1 style='margin-bottom: 0px; margin-top: -15px;'>
            {img_html} ARIEL 
            <span style='font-size: 0.3em; color: gray; vertical-align: middle; margin-left: 15px;'>
                <b>A</b>dvanced <b>R</b>easoning & <b>I</b>ntelligent <b>E</b>xecution <b>L</b>ayer
            </span>
        </h1>
        """,
        unsafe_allow_html=True
    )

with col_lang:
    # Quick language switcher in the top-right corner
    _live_config = load_json(BASE_DIR / "settings" / "config.json")
    current_lang = _live_config.get("agent", {}).get("language", "en")
    lang_codes = ["es", "en"]
    lang_labels = [t.get("lang_spanish", "Spanish"), t.get("lang_english", "English")]
    lang_idx = lang_codes.index(current_lang) if current_lang in lang_codes else 1

    st.markdown("<div style='margin-top: 10px;'></div>", unsafe_allow_html=True)
    quick_lang = st.selectbox(
        t.get("set_language", "Language"),
        lang_labels,
        index=lang_idx,
        label_visibility="collapsed"
    )

    new_quick_lang = lang_codes[lang_labels.index(quick_lang)]

    # If the user changed the language, persist it and reload the page
    if new_quick_lang != current_lang:
        config_path = BASE_DIR / "settings" / "config.json"
        config_data = load_json(config_path)
        config_data.setdefault("agent", {})["language"] = new_quick_lang
        save_json_file(config_path, config_data)
        st.session_state.ipc.reload_config()

        st.rerun()

st.markdown("<hr style='margin-top: 0px; margin-bottom: 20px;'>", unsafe_allow_html=True)


# ═══════════════════════════════════════════════════════════════════
#  MAIN TABS: Chat, Short-Term Memory, Long-Term Memory, Debug
# ═══════════════════════════════════════════════════════════════════

tab_chat, tab_st_mem, tab_lt_mem, tab_debug = st.tabs([
    t.get("tab_chat", "💬 Chat"),
    t.get("tab_st_mem", "🐠 Short-Term Mem"),
    t.get("tab_lt_mem", "🧠 Long-Term Mem"),
    t.get("tab_debug", "🐞 Debug (Logs)")
])


# ── TAB 1: CONVERSATION ────────────────────────────────────────────
with tab_chat:
    # Render all previous messages from the session history
    for msg in st.session_state.messages:
        with st.chat_message(msg["role"]):
            st.markdown(msg["content"])

    # Chat input — when the user submits a message, run the agent
    if prompt := st.chat_input(t.get("chat_placeholder", "Type a task...")):
        st.session_state.messages.append({"role": "user", "content": prompt})
        with st.chat_message("user"): st.markdown(prompt)

        with st.chat_message("assistant"):
            msg_ph = st.empty()    # Placeholder for streaming text
            stat_ph = st.empty()   # Placeholder for tool status updates
            full_resp = ""

            # Iterate through the agent's generator, updating the UI in real time
            for update in st.session_state.ipc.run_task(prompt, "gui"):
                if update["type"] == "message":
                    full_resp += update["content"] + "\n\n"
                    msg_ph.markdown(full_resp)
                elif update["type"] == "tool_start":
                    with stat_ph:
                        with st.spinner(update["content"]): pass
                elif update["type"] in ["tool_end", "status"]:
                    stat_ph.caption(f"*{update['content']}*")
                elif update["type"] == "error":
                    st.error(update["content"])
                elif update["type"] == "done":
                    stat_ph.success(t.get("task_completed", "✅ Task completed."))

            # Save the assistant's complete response to session history
            if full_resp:
                st.session_state.messages.append({"role": "assistant", "content": full_resp})


# ── TAB 2: SHORT-TERM MEMORY ───────────────────────────────────────
with tab_st_mem:
    st.subheader(t.get("tab_st_mem", "🐠 Short-Term Mem"))
    _st_mem = st.session_state.ipc.get_short_term_memory()
    if not _st_mem:
        st.info(t.get("no_st_mem", "No context in memory currently."))
    else:
        # Show the raw JSON payload that gets sent to the LLM API
        with st.expander(t.get("view_context", "View context sent to LLM"), expanded=True):
            _api_msgs = st.session_state.ipc.get_api_messages()
            st.json(_api_msgs)


# ── TAB 3: LONG-TERM MEMORY ────────────────────────────────────────
with tab_lt_mem:
    st.subheader(t.get("tab_lt_mem", "🧠 Long-Term Mem"))
    lt_mem_path = BASE_DIR / "memory" / "longtermmemory.json"
    emb_path = BASE_DIR / "memory" / "embeddings.json"

    # Counter for refreshing widget keys after deletions
    if "lt_mem_refresh" not in st.session_state:
        st.session_state.lt_mem_refresh = 0

    lt_data = load_json(lt_mem_path)
    memories = lt_data.get("memories", [])

    if not memories:
        st.info(t.get("no_lt_mem", "ARIEL hasn't learned anything long-term yet."))
    else:
        col_title, col_clear = st.columns([8, 2])
        with col_clear:
            # Wipe all long-term memories and their embeddings at once
            if st.button(t.get("btn_clear_lt", "🗑️ Clear All"), use_container_width=True):
                lt_data["memories"] = []
                save_json_file(lt_mem_path, lt_data)
                # Also clear all embeddings since they're now orphans
                save_json_file(emb_path, {"embeddings": {}})
                st.session_state.lt_mem_refresh += 1
                st.rerun()

        st.markdown("---")

        # Display memories in reverse chronological order (newest first)
        for i, mem in enumerate(reversed(memories)):
            # Calculate the real index in the original list for deletion
            real_idx = len(memories) - 1 - i

            c1, c2 = st.columns([9, 1])
            with c1:
                st.markdown(f"**{mem.get('timestamp', t.get('unknown_date', 'Unknown date'))}**")
                st.write(mem.get("content", ""))
            with c2:
                # Unique delete button per memory (key includes refresh counter)
                if st.button(t.get("btn_delete_item", "❌ Delete"), key=f"del_mem_{real_idx}_{st.session_state.lt_mem_refresh}"):
                    # Remove the corresponding embedding if it exists
                    deleted_mem = lt_data["memories"].pop(real_idx)
                    mem_id = deleted_mem.get("id", "")
                    if mem_id:
                        emb_data = load_json(emb_path)
                        if mem_id in emb_data.get("embeddings", {}):
                            del emb_data["embeddings"][mem_id]
                            save_json_file(emb_path, emb_data)
                    save_json_file(lt_mem_path, lt_data)
                    st.session_state.lt_mem_refresh += 1
                    st.rerun()
            st.markdown("---")


# ── TAB 4: DEBUG (LOGS) ────────────────────────────────────────────
with tab_debug:
    st.subheader(t.get("tab_debug", "🐞 Debug (Logs)"))

    log_dir_name = load_json(BASE_DIR / "settings" / "config.json").get("logging", {}).get("log_dir", "logs")
    log_dir_path = BASE_DIR / log_dir_name

    # Find all .log files, sorted by modification time (newest first)
    if log_dir_path.exists():
        log_files = sorted(list(log_dir_path.glob("*.log")), key=os.path.getmtime, reverse=True)
    else:
        log_files = []

    if not log_files:
        st.info(t.get("no_logs", "No logs generated yet."))
    else:
        file_names = [f.name for f in log_files]

        col1, col2 = st.columns([4, 1])
        with col1:
            selected_file = st.selectbox(t.get("select_log", "Select log file:"), file_names)
        with col2:
            st.markdown("<br>", unsafe_allow_html=True)
            if st.button(t.get("refresh_logs", "🔄 Refresh"), use_container_width=True):
                st.rerun()

        try:
            selected_path = log_dir_path / selected_file
            with open(selected_path, "r", encoding="utf-8") as f:
                logs_content = f.read()

            st.code(
                logs_content if logs_content else t.get("waiting_events", "Waiting for events..."),
                language="bash"
            )
        except Exception as e:
            st.error(f"{t.get('error_log', 'Error loading log:')} {e}")