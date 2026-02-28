"""
core/security.py — Password verification and token encryption.

Provides a simple but effective security layer for ARIEL:
  - Password hashing with PBKDF2-SHA256 (100k iterations) for login.
  - Token encryption with Fernet (AES-128-CBC + HMAC) for storing
    API keys and bot tokens safely in config.json.

Flow:
  1. First launch: user creates a password → hash + salt stored in security.json.
  2. Existing plaintext tokens in config.json get encrypted with "ENC:" prefix.
  3. Subsequent launches: user enters password → verified against hash →
     derived key used to decrypt tokens in memory.
  4. The derived key is passed to child processes (scheduler, GUI, telegram)
     via the ARIEL_SESSION_KEY environment variable, which only lives in
     memory and disappears when ARIEL shuts down.

Dependencies:
  - cryptography (pip install cryptography)
"""

import os
import base64
import hashlib
import json
from pathlib import Path
from typing import Optional

# ── Paths ───────────────────────────────────────────────────────────
BASE_DIR = Path(__file__).parent.parent
SECURITY_FILE = BASE_DIR / "settings" / "security.json"
CONFIG_FILE = BASE_DIR / "settings" / "config.json"

# ── Environment variable name for the session key ───────────────────
SESSION_KEY_ENV = "ARIEL_SESSION_KEY"

# ── Encryption prefix ──────────────────────────────────────────────
# Tokens in config.json that start with this prefix are encrypted.
# Tokens without this prefix are treated as plaintext (backwards compatible).
ENC_PREFIX = "ENC:"


# ═══════════════════════════════════════════════════════════════════
#  KEY DERIVATION
# ═══════════════════════════════════════════════════════════════════

def _derive_fernet_key(password: str, salt: bytes) -> bytes:
    """Derive a Fernet-compatible key (32 bytes, URL-safe base64) from a password.

    Uses PBKDF2-HMAC-SHA256 with 100,000 iterations — strong enough for
    local use, fast enough to not annoy the user.
    """
    raw_key = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 100_000, dklen=32)
    return base64.urlsafe_b64encode(raw_key)


def _get_salt() -> bytes:
    """Read the salt from security.json."""
    data = json.loads(SECURITY_FILE.read_text(encoding="utf-8"))
    return base64.b64decode(data["salt"])


# ═══════════════════════════════════════════════════════════════════
#  PASSWORD MANAGEMENT
# ═══════════════════════════════════════════════════════════════════

def is_password_set() -> bool:
    """Check if a password has been configured."""
    if not SECURITY_FILE.exists():
        return False
    try:
        data = json.loads(SECURITY_FILE.read_text(encoding="utf-8"))
        return bool(data.get("password_hash"))
    except (json.JSONDecodeError, KeyError):
        return False


def setup_password(password: str):
    """Create a new password: generate salt, hash password, and save to security.json.

    This should only be called once (first-time setup). After calling this,
    use encrypt_existing_tokens() to encrypt any plaintext tokens in config.json.
    """
    salt = os.urandom(16)
    password_hash = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 100_000).hex()

    data = {
        "password_hash": password_hash,
        "salt": base64.b64encode(salt).decode()
    }

    SECURITY_FILE.parent.mkdir(parents=True, exist_ok=True)
    SECURITY_FILE.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def verify_password(password: str) -> bool:
    """Verify a password against the stored hash."""
    if not SECURITY_FILE.exists():
        return False
    try:
        data = json.loads(SECURITY_FILE.read_text(encoding="utf-8"))
        salt = base64.b64decode(data["salt"])
        expected_hash = data["password_hash"]
        actual_hash = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 100_000).hex()
        return actual_hash == expected_hash
    except Exception:
        return False


def get_session_key(password: str) -> str:
    """Derive the Fernet key from the password and return it as a string.

    This string is what gets stored in the ARIEL_SESSION_KEY environment
    variable and used for all encrypt/decrypt operations.
    """
    salt = _get_salt()
    fernet_key = _derive_fernet_key(password, salt)
    return fernet_key.decode()


# ═══════════════════════════════════════════════════════════════════
#  TOKEN ENCRYPTION / DECRYPTION
# ═══════════════════════════════════════════════════════════════════

def encrypt_token(plaintext: str, session_key: Optional[str] = None) -> str:
    """Encrypt a token string. Returns "ENC:<encrypted_data>".

    If no session_key is provided, reads from the environment variable.
    """
    from cryptography.fernet import Fernet

    key = session_key or os.environ.get(SESSION_KEY_ENV)
    if not key:
        raise ValueError("No session key available for encryption.")

    f = Fernet(key.encode())
    encrypted = f.encrypt(plaintext.encode()).decode()
    return f"{ENC_PREFIX}{encrypted}"


def decrypt_token(value: str, session_key: Optional[str] = None) -> str:
    """Decrypt a token string if it has the ENC: prefix.

    If the value doesn't start with ENC:, it's returned as-is
    (backwards compatible with old plaintext configs).
    """
    if not value or not value.startswith(ENC_PREFIX):
        return value  # Not encrypted — return as-is

    from cryptography.fernet import Fernet

    key = session_key or os.environ.get(SESSION_KEY_ENV)
    if not key:
        raise ValueError("No session key available for decryption.")

    encrypted_data = value[len(ENC_PREFIX):]
    f = Fernet(key.encode())
    return f.decrypt(encrypted_data.encode()).decode()


def decrypt_if_needed(value: str) -> str:
    """Convenience function: decrypt if encrypted, pass through if not.

    Safe to call on any config value — plaintext values pass through unchanged.
    """
    try:
        return decrypt_token(value)
    except Exception:
        return value  # If decryption fails, return original value


# ═══════════════════════════════════════════════════════════════════
#  BULK TOKEN ENCRYPTION (first-time setup)
# ═══════════════════════════════════════════════════════════════════

def encrypt_existing_tokens(session_key: str):
    """Find any plaintext tokens in config.json and encrypt them.

    Called once during first-time password setup. Scans for:
      - api.api_key
      - integrations.telegram.bot_token

    Only encrypts values that don't already have the ENC: prefix.
    """
    config = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    changed = False

    # Encrypt API key
    api_key = config.get("api", {}).get("api_key", "")
    if api_key and not api_key.startswith(ENC_PREFIX):
        config["api"]["api_key"] = encrypt_token(api_key, session_key)
        changed = True

    # Encrypt Telegram bot token
    tg_token = config.get("integrations", {}).get("telegram", {}).get("bot_token", "")
    if tg_token and not tg_token.startswith(ENC_PREFIX):
        config["integrations"]["telegram"]["bot_token"] = encrypt_token(tg_token, session_key)
        changed = True

    if changed:
        CONFIG_FILE.write_text(json.dumps(config, indent=2, ensure_ascii=False), encoding="utf-8")

    return changed


def change_password(old_session_key: str, new_password: str) -> str:
    """Change the password: decrypt tokens with old key, re-encrypt with new key.

    Steps:
      1. Read all encrypted tokens and decrypt them with the old session key.
      2. Create a new password hash + salt (overwrites security.json).
      3. Derive the new session key from the new password.
      4. Re-encrypt all tokens with the new key.
      5. Return the new session key.
    """
    config = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))

    # Step 1: Decrypt existing tokens with the old key
    api_key = config.get("api", {}).get("api_key", "")
    if api_key and api_key.startswith(ENC_PREFIX):
        api_key = decrypt_token(api_key, old_session_key)

    tg_token = config.get("integrations", {}).get("telegram", {}).get("bot_token", "")
    if tg_token and tg_token.startswith(ENC_PREFIX):
        tg_token = decrypt_token(tg_token, old_session_key)

    # Step 2: Create new password hash + salt
    setup_password(new_password)

    # Step 3: Derive new session key
    new_session_key = get_session_key(new_password)

    # Step 4: Re-encrypt tokens with the new key
    if api_key:
        config["api"]["api_key"] = encrypt_token(api_key, new_session_key)
    if tg_token:
        config["integrations"]["telegram"]["bot_token"] = encrypt_token(tg_token, new_session_key)

    CONFIG_FILE.write_text(json.dumps(config, indent=2, ensure_ascii=False), encoding="utf-8")

    return new_session_key


def remove_password(old_session_key: str):
    """Remove the password: decrypt all tokens back to plaintext and delete security.json.

    After this, ARIEL will run without any password protection.
    """
    config = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))

    # Decrypt all tokens back to plaintext
    api_key = config.get("api", {}).get("api_key", "")
    if api_key and api_key.startswith(ENC_PREFIX):
        config["api"]["api_key"] = decrypt_token(api_key, old_session_key)

    tg_token = config.get("integrations", {}).get("telegram", {}).get("bot_token", "")
    if tg_token and tg_token.startswith(ENC_PREFIX):
        config["integrations"]["telegram"]["bot_token"] = decrypt_token(tg_token, old_session_key)

    CONFIG_FILE.write_text(json.dumps(config, indent=2, ensure_ascii=False), encoding="utf-8")

    # Delete security.json
    if SECURITY_FILE.exists():
        SECURITY_FILE.unlink()