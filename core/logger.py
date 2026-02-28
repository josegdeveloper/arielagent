"""
core/logger.py — Logging system for ARIEL.

Provides two types of logging:
  - Text logs: Human-readable log lines written to .log files (and
    optionally printed to the console). These are displayed in the
    GUI's Debug tab.
  - JSON logs: Structured event data written to .json files for
    programmatic analysis (e.g., token usage, tool executions).

The output destination (Console vs GUI Tab) can be changed at runtime
via the set_output_destination() method, which is called from the
Settings modal in the GUI.
"""

import logging
from datetime import datetime
from core.utils import BASE_DIR, save_json


class LoggerManager:
    """Manages text and JSON logging for a single ARIEL session."""

    def __init__(self, config: dict, session_id: str):
        """Set up file-based text logger and JSON event log.

        Args:
            config: The global config dict (from config.json).
            session_id: Unique 8-char ID for this session.
        """
        self.config = config
        self.session_id = session_id

        # Create the log directory if it doesn't exist
        self.log_dir = BASE_DIR / config.get("logging", {}).get("log_dir", "logs")
        self.log_dir.mkdir(parents=True, exist_ok=True)

        # Generate timestamp-based filenames for this session
        self.timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.log_file_path = self.log_dir / f"session_{self.timestamp}_{self.session_id}.log"

        # Initialize the text logger (writes to file + optionally to console)
        self.logger = self._setup_text_logger()

        # Initialize the JSON event log (structured data for analysis)
        self.json_log_path = self.log_dir / f"session_{self.timestamp}_{self.session_id}.json"
        self._json_data = self._init_json_structure()
        self._flush_json()

    def _setup_text_logger(self) -> logging.Logger:
        """Create and configure a Python logger with file and optional console handlers.

        The FileHandler always writes to disk (so the GUI can read it).
        The StreamHandler (console) is only attached if the user has
        configured logging output to "Console" in settings.
        """
        logger = logging.getLogger(f"ARIEL.{self.session_id}")
        level = getattr(logging, self.config.get("logging", {}).get("level", "INFO"))
        logger.setLevel(level)

        if not logger.handlers:
            formatter = logging.Formatter("[%(asctime)s] %(levelname)s — %(message)s", "%H:%M:%S")

            # 1. FileHandler: Always active — writes to disk for the GUI's Debug tab
            fh = logging.FileHandler(self.log_file_path, encoding="utf-8")
            fh.setFormatter(formatter)
            logger.addHandler(fh)

            # 2. StreamHandler: Only if config says "Console" (visible in terminal)
            dest = self.config.get("logging", {}).get("output_dest", "Console")
            if dest in ["Console", "Consola"]:
                ch = logging.StreamHandler()
                ch.setFormatter(formatter)
                logger.addHandler(ch)

        return logger

    def set_output_destination(self, dest: str):
        """Enable or disable console output at runtime.

        Called from the Settings modal when the user changes the
        log destination preference.

        Args:
            dest: "Console" to enable terminal output, anything else to disable.
        """
        # Remove existing console handler if present
        for handler in self.logger.handlers[:]:
            if type(handler) is logging.StreamHandler:
                self.logger.removeHandler(handler)

        # Re-attach console handler if the user wants console output
        if dest in ["Console", "Consola"]:
            ch = logging.StreamHandler()
            formatter = logging.Formatter("[%(asctime)s] %(levelname)s — %(message)s", "%H:%M:%S")
            ch.setFormatter(formatter)
            self.logger.addHandler(ch)

    def _init_json_structure(self) -> dict:
        """Create the initial structure for the JSON event log."""
        return {
            "session_info": {
                "id": self.session_id,
                "started_at": datetime.now().isoformat(),
            },
            "events": []
        }

    def _flush_json(self):
        """Write the current JSON log data to disk."""
        save_json(self.json_log_path, self._json_data)

    def log_event(self, event_type: str, details: dict):
        """Record a structured event to the JSON log.

        Args:
            event_type: Category string (e.g., "api_call", "tool_execution").
            details: Arbitrary dict with event-specific data.
        """
        event = {
            "timestamp": datetime.now().isoformat(),
            "type": event_type,
            "details": details
        }
        self._json_data["events"].append(event)
        self._flush_json()

    # ── Convenience methods for text logging ────────────────────────
    def info(self, msg: str): self.logger.info(msg)
    def warning(self, msg: str): self.logger.warning(msg)
    def error(self, msg: str): self.logger.error(msg)
