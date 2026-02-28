"""
core/utils.py — Shared utility functions and constants.

Provides:
  - BASE_DIR / CONFIG_PATH: Canonical paths used across the project.
  - load_json / save_json: Safe JSON I/O with UTF-8 encoding.
  - get_translations: Loads the UI language file for i18n support.
"""

import json
from pathlib import Path
from typing import Any

# ── Base project directory (parent of the 'core' folder) ────────────
BASE_DIR = Path(__file__).parent.parent

# ── Path to the main configuration file ─────────────────────────────
CONFIG_PATH = BASE_DIR / "settings" / "config.json"


def load_json(path: Path) -> dict:
    """Load a JSON file and return its contents as a dict.

    Returns an empty dict if the file does not exist, which allows
    callers to use .get() safely without checking for FileNotFoundError.
    """
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}


def save_json(path: Path, data: Any):
    """Save data to a JSON file with pretty-printing and UTF-8 encoding.

    Creates parent directories automatically if they don't exist.
    Uses ensure_ascii=False so that accented characters (Spanish, etc.)
    are written as-is rather than escaped.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8"
    )


def get_translations(lang_code: str = "en") -> dict:
    """Load the UI translation dictionary for the given language code.

    Looks for a file named '{lang_code}.json' in the languages/ folder.
    Falls back to English if the requested language file doesn't exist.

    Returns:
        A dict with nested keys like {"gui": {...}, "tg": {...}, "start": {...}}.
    """
    lang_path = BASE_DIR / "languages" / f"{lang_code}.json"
    if not lang_path.exists():
        lang_path = BASE_DIR / "languages" / "en.json"  # Fallback to English
    return load_json(lang_path)
