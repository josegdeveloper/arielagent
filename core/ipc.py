"""
core/ipc.py — Inter-Process Communication for the ARIEL system.

Provides two classes:
  - ArielServer: A socket-based server that owns the single ARIELAgent
    instance and dispatches commands from clients (GUI, bots, scheduler).
  - ArielClient: A lightweight client used by any process that needs
    to communicate with the orchestrator (run tasks, query status, etc.).

Protocol:
  - Transport: Unix domain socket (Linux/macOS) or TCP localhost (Windows).
  - Framing: Newline-delimited JSON (one JSON object per line).
  - Each client connection handles one request-response cycle.
  - For run_task, the server streams multiple JSON lines (one per
    agent update) until a final {"type": "end_stream"} sentinel.

Commands:
  - ping              → {"type": "result", "data": {"status": "ok"}}
  - run_task          → streams agent updates, then end_stream
  - get_status        → busy flag and current source
  - reload_config     → agent reloads config.json from disk
  - get_short_term_memory → current conversation history
  - get_api_messages  → API-formatted messages (for debug tab)
"""

import socket
import threading
import json
import sys
import os
import uuid
from pathlib import Path
from typing import Generator

# ── Default connection parameters ──────────────────────────────────
DEFAULT_PORT = 19420
DEFAULT_SOCK_NAME = "ariel.sock"

# socket.AF_UNIX doesn't exist on Windows — store it safely for comparisons
_AF_UNIX = getattr(socket, "AF_UNIX", None)


def _get_address(base_dir: Path):
    """Return (socket_family, address) for the IPC socket.

    On Unix/macOS: uses a Unix domain socket in tmp/ (fast, no port conflicts).
    On Windows: uses TCP on localhost (Unix sockets not available).
    """
    if sys.platform == "win32" or _AF_UNIX is None:
        return (socket.AF_INET, ("127.0.0.1", DEFAULT_PORT))
    else:
        sock_path = str(base_dir / "tmp" / DEFAULT_SOCK_NAME)
        return (_AF_UNIX, sock_path)


# ═══════════════════════════════════════════════════════════════════
#  SERVER
# ═══════════════════════════════════════════════════════════════════

class ArielServer:
    """IPC server that owns the ARIELAgent and handles client requests.

    Runs in the orchestrator process (ariel.py). Accepts connections
    from GUI, Telegram, WhatsApp, and Scheduler clients.

    Thread safety: a threading.Lock ensures only one agent.run() call
    executes at a time. If a second run_task arrives while one is in
    progress, it blocks until the first finishes (up to a timeout).
    """

    def __init__(self, agent, base_dir: Path, logger=None):
        self.agent = agent
        self.base_dir = Path(base_dir)
        self.logger = logger
        self._lock = threading.Lock()
        self._busy = False
        self._current_source = None
        self._running = False
        self._family, self._address = _get_address(self.base_dir)
        # In-memory outbox queues for proactive messaging via gateways
        self._whatsapp_outbox = []
        self._outbox_lock = threading.Lock()

    def start(self):
        """Start the IPC server in a background daemon thread."""
        self._running = True

        # Clean up stale socket file from a previous run
        if self._family == _AF_UNIX:
            try:
                os.unlink(self._address)
            except OSError:
                pass

        self._sock = socket.socket(self._family, socket.SOCK_STREAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.bind(self._address)
        self._sock.listen(8)
        self._sock.settimeout(1.0)  # Allows clean shutdown via stop()

        self._thread = threading.Thread(target=self._accept_loop, daemon=True)
        self._thread.start()

        if self.logger:
            addr_str = self._address if isinstance(self._address, str) else f"{self._address[0]}:{self._address[1]}"
            self.logger.info(f"IPC server listening on {addr_str}")

    def stop(self):
        """Shut down the server and clean up resources."""
        self._running = False
        try:
            self._sock.close()
        except Exception:
            pass
        if self._family == _AF_UNIX:
            try:
                os.unlink(self._address)
            except OSError:
                pass

    # ── Accept loop ────────────────────────────────────────────────

    def _accept_loop(self):
        """Accept incoming connections and spawn a handler thread for each."""
        while self._running:
            try:
                conn, _ = self._sock.accept()
                t = threading.Thread(target=self._handle_client, args=(conn,), daemon=True)
                t.start()
            except socket.timeout:
                continue
            except OSError:
                break

    # ── Client handler ─────────────────────────────────────────────

    def _handle_client(self, conn: socket.socket):
        """Handle a single client connection: read request, dispatch, respond."""
        rid = "?"
        try:
            conn.settimeout(300)  # 5 min — generous for long agent tasks

            # Read one JSON line (the request)
            data = b""
            while b"\n" not in data:
                chunk = conn.recv(4096)
                if not chunk:
                    return
                data += chunk

            line = data.split(b"\n")[0]
            request = json.loads(line.decode("utf-8"))
            cmd = request.get("cmd", "")
            rid = request.get("rid", str(uuid.uuid4())[:8])

            # ── Dispatch by command ────────────────────────────────
            if cmd == "ping":
                self._send(conn, {"rid": rid, "type": "result", "data": {"status": "ok"}})

            elif cmd == "run_task":
                self._handle_run_task(conn, request, rid)

            elif cmd == "get_status":
                self._send(conn, {"rid": rid, "type": "result", "data": {
                    "busy": self._busy,
                    "source": self._current_source
                }})

            elif cmd == "reload_config":
                self._handle_reload_config(conn, rid)

            elif cmd == "set_session_key":
                self._handle_set_session_key(conn, request, rid)

            elif cmd == "decrypt_token":
                self._handle_decrypt_token(conn, request, rid)

            elif cmd == "get_short_term_memory":
                msgs = self._serialize_messages(self.agent.memory.messages)
                self._send(conn, {"rid": rid, "type": "result", "data": {"messages": msgs}})

            elif cmd == "get_api_messages":
                api_msgs = self._serialize_messages(self.agent.memory.get_api_messages())
                self._send(conn, {"rid": rid, "type": "result", "data": {"messages": api_msgs}})

            elif cmd == "queue_whatsapp_message":
                phone = request.get("phone", "")
                jid_server = request.get("jid_server", "s.whatsapp.net")
                message = request.get("message", "")
                with self._outbox_lock:
                    self._whatsapp_outbox.append({"phone": phone, "jid_server": jid_server, "message": message})
                self._send(conn, {"rid": rid, "type": "result", "data": {"status": "queued"}})
                if self.logger:
                    self.logger.info(f"IPC: WhatsApp message queued for {phone}@{jid_server}.")

            elif cmd == "get_whatsapp_outbox":
                with self._outbox_lock:
                    pending = list(self._whatsapp_outbox)
                    self._whatsapp_outbox.clear()
                self._send(conn, {"rid": rid, "type": "result", "data": {"messages": pending}})

            else:
                self._send(conn, {"rid": rid, "type": "error", "content": f"Unknown command: {cmd}"})

        except Exception as e:
            try:
                self._send(conn, {"rid": rid, "type": "error", "content": str(e)})
            except Exception:
                pass
            if self.logger:
                self.logger.error(f"IPC handler error: {e}")
        finally:
            # Always send end_stream and close
            try:
                self._send(conn, {"rid": rid, "type": "end_stream"})
            except Exception:
                pass
            try:
                conn.close()
            except Exception:
                pass

    # ── Command handlers ───────────────────────────────────────────

    def _handle_run_task(self, conn: socket.socket, request: dict, rid: str):
        """Execute a task through the agent and stream updates to the client."""
        task = request.get("task", "")
        source = request.get("source", "unknown")

        if self.logger:
            self.logger.info(f"IPC: run_task from '{source}': {task[:80]}...")

        # Acquire the lock (wait up to 2 min if another task is running)
        acquired = self._lock.acquire(timeout=120)
        if not acquired:
            self._send(conn, {"rid": rid, "type": "error",
                              "content": "Agent is busy. Try again later."})
            return

        try:
            self._busy = True
            self._current_source = source

            for update in self.agent.run(task):
                self._send(conn, {"rid": rid, **update})

        except Exception as e:
            self._send(conn, {"rid": rid, "type": "error", "content": str(e)})
            if self.logger:
                self.logger.error(f"IPC: run_task error: {e}")
        finally:
            self._busy = False
            self._current_source = None
            self._lock.release()

    def _handle_reload_config(self, conn: socket.socket, rid: str):
        """Tell the agent to reload its configuration from disk."""
        try:
            self.agent.reload_config()
            self._send(conn, {"rid": rid, "type": "result", "data": {"status": "ok"}})
            if self.logger:
                self.logger.info("IPC: Configuration reloaded successfully.")
        except Exception as e:
            self._send(conn, {"rid": rid, "type": "error", "content": str(e)})
            if self.logger:
                self.logger.error(f"IPC: reload_config error: {e}")

    def _handle_set_session_key(self, conn: socket.socket, request: dict, rid: str):
        """Receive the session key from the GUI after user login.

        This is needed when the API key is encrypted (ENC: prefix) and
        the user didn't pass a password via CLI. The GUI sends the
        session key after login so the orchestrator can decrypt tokens.
        """
        from core.security import SESSION_KEY_ENV
        key = request.get("key", "")
        if not key:
            self._send(conn, {"rid": rid, "type": "error", "content": "No key provided."})
            return

        try:
            os.environ[SESSION_KEY_ENV] = key
            self.agent.reload_config()
            self._send(conn, {"rid": rid, "type": "result", "data": {"status": "ok"}})
            if self.logger:
                self.logger.info("IPC: Session key received — agent re-authenticated.")
        except Exception as e:
            self._send(conn, {"rid": rid, "type": "error", "content": str(e)})
            if self.logger:
                self.logger.error(f"IPC: set_session_key error: {e}")

    def _handle_decrypt_token(self, conn: socket.socket, request: dict, rid: str):
        """Decrypt an ENC:-prefixed token on behalf of a client process.

        Used by Telegram/WhatsApp bots that need to decrypt their own
        tokens (e.g. bot_token) without having the session key locally.
        The orchestrator has the key in its environment after login.
        """
        from core.security import decrypt_if_needed
        token = request.get("token", "")
        try:
            decrypted = decrypt_if_needed(token)
            self._send(conn, {"rid": rid, "type": "result", "data": {"token": decrypted}})
        except Exception as e:
            self._send(conn, {"rid": rid, "type": "error", "content": str(e)})

    # ── Wire helpers ───────────────────────────────────────────────

    def _send(self, conn: socket.socket, data: dict):
        """Send a single JSON line to the client."""
        line = json.dumps(data, ensure_ascii=False, default=str) + "\n"
        conn.sendall(line.encode("utf-8"))

    def _serialize_messages(self, messages: list) -> list:
        """Convert conversation messages to JSON-serializable format.

        Handles Anthropic SDK objects (TextBlock, ToolUseBlock, etc.)
        that may be stored in the memory's message list.
        """
        result = []
        for msg in messages:
            serialized = {"role": msg.get("role", "unknown")}
            content = msg.get("content")

            if isinstance(content, str):
                serialized["content"] = content
            elif isinstance(content, list):
                serialized["content"] = [self._serialize_block(item) for item in content]
            else:
                # Single SDK object or other type
                serialized["content"] = self._serialize_block(content)
            result.append(serialized)
        return result

    @staticmethod
    def _serialize_block(item):
        """Convert a single content block to a JSON-safe dict."""
        if isinstance(item, dict):
            return item
        if hasattr(item, "model_dump"):
            # Pydantic v2 (modern Anthropic SDK)
            return item.model_dump()
        if hasattr(item, "dict"):
            # Pydantic v1 fallback
            return item.dict()
        return str(item)


# ═══════════════════════════════════════════════════════════════════
#  CLIENT
# ═══════════════════════════════════════════════════════════════════

class ArielClient:
    """Lightweight IPC client for communicating with the ARIEL orchestrator.

    Used by GUI (Streamlit), Telegram bot, WhatsApp bot, and Scheduler
    to send tasks and queries without needing their own ARIELAgent.

    Each method call opens a fresh socket connection, sends the request,
    reads all responses, and closes. This keeps the protocol simple and
    avoids managing persistent connections across Streamlit reruns.
    """

    def __init__(self, base_dir=None):
        if base_dir is None:
            base_dir = Path(__file__).parent.parent
        self.base_dir = Path(base_dir)
        self._family, self._address = _get_address(self.base_dir)

    def _connect(self) -> socket.socket:
        """Open a fresh connection to the orchestrator."""
        sock = socket.socket(self._family, socket.SOCK_STREAM)
        sock.connect(self._address)
        return sock

    def _request(self, cmd_dict: dict) -> Generator[dict, None, None]:
        """Send a request and yield response dicts until end_stream.

        This is the low-level transport method. Higher-level methods
        (ping, run_task, etc.) wrap this with appropriate command dicts.
        """
        sock = self._connect()
        try:
            sock.settimeout(300)
            line = json.dumps(cmd_dict, ensure_ascii=False) + "\n"
            sock.sendall(line.encode("utf-8"))

            buf = b""
            while True:
                chunk = sock.recv(8192)
                if not chunk:
                    break
                buf += chunk
                # Process all complete lines in the buffer
                while b"\n" in buf:
                    raw_line, buf = buf.split(b"\n", 1)
                    if not raw_line.strip():
                        continue
                    resp = json.loads(raw_line.decode("utf-8"))
                    if resp.get("type") == "end_stream":
                        return
                    yield resp
        finally:
            try:
                sock.close()
            except Exception:
                pass

    # ── Public API ─────────────────────────────────────────────────

    def ping(self) -> bool:
        """Check if the orchestrator is alive and accepting requests."""
        try:
            for resp in self._request({"cmd": "ping"}):
                if resp.get("type") == "result":
                    return resp.get("data", {}).get("status") == "ok"
            return False
        except Exception:
            return False

    def run_task(self, task: str, source: str = "unknown"):
        """Execute a task. Yields dicts identical to agent.run().

        The yielded dicts have the same structure as ARIELAgent.run():
          {"type": "message",    "content": "..."}
          {"type": "tool_start", "content": "..."}
          {"type": "tool_end",   "content": "..."}
          {"type": "status",     "content": "..."}
          {"type": "error",      "content": "..."}
          {"type": "done",       "content": "..."}

        This makes it a drop-in replacement for agent.run() in the GUI
        and bot code — the calling code doesn't need to change its loop.
        """
        rid = str(uuid.uuid4())[:8]
        for resp in self._request({"cmd": "run_task", "task": task, "source": source, "rid": rid}):
            resp.pop("rid", None)
            yield resp

    def get_status(self) -> dict:
        """Query whether the agent is currently busy."""
        try:
            for resp in self._request({"cmd": "get_status"}):
                if resp.get("type") == "result":
                    return resp.get("data", {})
            return {}
        except Exception:
            return {}

    def reload_config(self) -> bool:
        """Tell the orchestrator to reload configuration from disk.

        Call this after saving changes to config.json so the live
        agent picks up the new settings (API key, model, temperature, etc.).
        """
        try:
            for resp in self._request({"cmd": "reload_config"}):
                if resp.get("type") == "result":
                    return resp.get("data", {}).get("status") == "ok"
                if resp.get("type") == "error":
                    return False
            return False
        except Exception:
            return False

    def set_session_key(self, session_key: str) -> bool:
        """Send the session key to the orchestrator after GUI login.

        The orchestrator needs this to decrypt ENC:-prefixed tokens
        (API key, bot tokens). Call immediately after the user logs in.
        """
        try:
            for resp in self._request({"cmd": "set_session_key", "key": session_key}):
                if resp.get("type") == "result":
                    return resp.get("data", {}).get("status") == "ok"
                if resp.get("type") == "error":
                    return False
            return False
        except Exception:
            return False

    def decrypt_token(self, token: str) -> str:
        """Ask the orchestrator to decrypt an ENC:-prefixed token.

        Used by bot processes that need their own tokens decrypted
        (e.g. Telegram bot_token) without having the session key locally.
        Returns the decrypted value, or the original value if decryption
        fails or the token isn't encrypted.
        """
        if not token or not token.startswith("ENC:"):
            return token
        try:
            for resp in self._request({"cmd": "decrypt_token", "token": token}):
                if resp.get("type") == "result":
                    return resp.get("data", {}).get("token", token)
            return token
        except Exception:
            return token

    def get_short_term_memory(self) -> list:
        """Retrieve the current short-term (session) memory."""
        try:
            for resp in self._request({"cmd": "get_short_term_memory"}):
                if resp.get("type") == "result":
                    return resp.get("data", {}).get("messages", [])
            return []
        except Exception:
            return []

    def get_api_messages(self) -> list:
        """Retrieve the API-formatted messages (for the debug/memory tab)."""
        try:
            for resp in self._request({"cmd": "get_api_messages"}):
                if resp.get("type") == "result":
                    return resp.get("data", {}).get("messages", [])
            return []
        except Exception:
            return []

    def queue_whatsapp_message(self, phone: str, message: str, jid_server: str = "s.whatsapp.net") -> bool:
        """Queue a WhatsApp message for delivery via the outbox.

        The WhatsApp bot polls get_whatsapp_outbox() to pick up
        and send these messages through neonize.
        """
        try:
            for resp in self._request({"cmd": "queue_whatsapp_message",
                                       "phone": phone, "jid_server": jid_server,
                                       "message": message}):
                if resp.get("type") == "result":
                    return resp.get("data", {}).get("status") == "queued"
            return False
        except Exception:
            return False

    def get_whatsapp_outbox(self) -> list:
        """Retrieve and clear pending WhatsApp outbox messages.

        Returns a list of {"phone": "...", "message": "..."} dicts.
        The server clears the queue after returning, so each message
        is delivered exactly once.
        """
        try:
            for resp in self._request({"cmd": "get_whatsapp_outbox"}):
                if resp.get("type") == "result":
                    return resp.get("data", {}).get("messages", [])
            return []
        except Exception:
            return []
