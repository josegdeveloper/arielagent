"""
gateways/whatsapp_bot.py — WhatsApp gateway for ARIEL.

Runs as an independent background process (launched by ariel.py or
from the GUI's Connectors modal). Connects to WhatsApp via the
WhatsApp Web protocol (neonize/Whatsmeow) and forwards messages
to the ARIEL orchestrator via IPC for processing.

This is a thin client — it does NOT create its own ARIELAgent.
All AI processing happens in the central orchestrator (ariel.py).

Authentication:
  - First run: generates a QR code saved to tmp/whatsapp_qr.png.
    The user scans it from WhatsApp → Settings → Linked Devices.
  - Subsequent runs: reconnects automatically using the saved session.

Security (dual layer):
  1. CONTACT CHECK: The sender must be a known contact in the phone's
     address book (Contact.Found == true). Strangers are rejected.
  2. PASSPHRASE: The sender must send a configured passphrase as their
     first message to authorize their LID. Once authorized, they can
     send normal messages. Authorized LIDs are persisted to disk so
     they survive restarts.

Dependencies:
  - neonize (pip install neonize)
  - qrcode[pil] (pip install qrcode[pil])
"""

import sys
import os
import json
import time
import logging
from pathlib import Path

# Allow importing the 'core' package from the gateways/ subdirectory
BASE_DIR = Path(__file__).parent.parent
sys.path.append(str(BASE_DIR))

from core.ipc import ArielClient
from core.utils import load_json, save_json, get_translations

# ── Paths ────────────────────────────────────────────────────────────
CONFIG_PATH = BASE_DIR / "settings" / "config.json"
QR_IMAGE_PATH = BASE_DIR / "tmp" / "whatsapp_qr.png"
STATUS_FILE = BASE_DIR / "tmp" / "whatsapp_status.txt"
AUTHORIZED_LIDS_PATH = BASE_DIR / "settings" / "whatsapp_authorized.json"

# Simple logger for the WhatsApp gateway process
log = logging.getLogger("whatsapp_gw")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [WA] %(message)s")


def write_status(status: str):
    """Write current connection status to a file so the GUI can read it."""
    try:
        STATUS_FILE.parent.mkdir(parents=True, exist_ok=True)
        STATUS_FILE.write_text(status, encoding="utf-8")
    except Exception:
        pass


def load_authorized_lids() -> dict:
    """Load the set of authorized LIDs from disk.

    Returns a dict like:
      {"authorized": {
          "221646216511518": {"name": "Jose", "jid_user": "34663543389", "jid_server": "s.whatsapp.net"},
          "123456789": {"name": "Mama", "jid_user": "34666274646", "jid_server": "s.whatsapp.net"}
      }}

    Backwards compatible: old format {"lid": "name"} is upgraded to
    {"lid": {"name": "name"}} on read.
    """
    data = load_json(AUTHORIZED_LIDS_PATH)
    if "authorized" not in data:
        data = {"authorized": {}}
    # Migrate old string format to new dict format
    for lid, val in data["authorized"].items():
        if isinstance(val, str):
            data["authorized"][lid] = {"name": val}
    return data


def save_authorized_lid(lid: str, name: str, jid_user: str = "", jid_server: str = "s.whatsapp.net"):
    """Add a newly authorized LID to the persistent file, including JID for outbound messaging."""
    data = load_authorized_lids()
    data["authorized"][lid] = {
        "name": name,
        "jid_user": jid_user,
        "jid_server": jid_server,
    }
    save_json(AUTHORIZED_LIDS_PATH, data)


def run_whatsapp_gateway():
    """Main entry point for the WhatsApp gateway."""

    config = load_json(CONFIG_PATH)
    wa_config = config.get("integrations", {}).get("whatsapp", {})

    if not wa_config.get("enabled", False):
        write_status("disabled")
        return

    lang_code = config.get("agent", {}).get("language", "en")
    t = get_translations(lang_code).get("wa", {})

    # Read the security passphrase
    passphrase = wa_config.get("passphrase", "").strip()

    # ── Import neonize ───────────────────────────────────────────────
    try:
        from neonize.client import NewClient
        from neonize.events import MessageEv, ConnectedEv, event as neonize_event
    except ImportError:
        print("ERROR: neonize not installed. Run: pip install neonize")
        write_status("error:neonize_not_installed")
        return

    # QR code image generation
    try:
        import qrcode as qrcode_lib
        HAS_QRCODE = True
    except ImportError:
        HAS_QRCODE = False
        print("WARNING: qrcode library not installed. Run: pip install qrcode[pil]")

    # ── Ensure directories exist ─────────────────────────────────────
    QR_IMAGE_PATH.parent.mkdir(parents=True, exist_ok=True)

    # ── Initialize IPC client (instead of creating own agent) ────────
    ipc = ArielClient(BASE_DIR)

    # ── In-memory JID cache for outbound messaging ───────────────────
    # Maps sender_id (LID) → the original neonize JID object used for
    # replies. The outbox checker uses this to send proactive messages
    # with the exact JID that neonize knows works.
    known_jids = {}

    # ── Load authorized LIDs into memory ─────────────────────────────
    authorized_lids = set(load_authorized_lids().get("authorized", {}).keys())
    log.info(f"WhatsApp: {len(authorized_lids)} authorized LID(s) loaded.")

    # ── Create WhatsApp client ───────────────────────────────────────
    # Session is stored in settings/ (persistent — survives restarts)
    session_name = str(BASE_DIR / "settings" / "whatsapp_session")
    client = NewClient(session_name)

    # ── QR Code callback ─────────────────────────────────────────────
    @client.qr
    def on_qr(client_instance, qr_data):
        """Called when a QR code is available for scanning."""
        log.info("WhatsApp QR code received. Scan it to link.")
        write_status("waiting_qr")

        if HAS_QRCODE:
            try:
                text = qr_data.decode("utf-8", errors="ignore") if isinstance(qr_data, bytes) else str(qr_data)
                qr = qrcode_lib.QRCode(
                    version=1,
                    error_correction=qrcode_lib.constants.ERROR_CORRECT_L,
                    box_size=10,
                    border=4,
                )
                qr.add_data(text)
                qr.make(fit=True)
                img = qr.make_image(fill_color="black", back_color="white")
                img.save(str(QR_IMAGE_PATH))
                log.info(f"QR code saved to {QR_IMAGE_PATH}")
            except Exception as e:
                log.error(f"Failed to save QR image: {e}")

    # ── Connection event ─────────────────────────────────────────────
    @client.event(ConnectedEv)
    def on_connected(client_instance: NewClient, event: ConnectedEv):
        """Called when successfully connected to WhatsApp."""
        log.info("WhatsApp gateway connected successfully.")
        write_status("connected")

        if QR_IMAGE_PATH.exists():
            QR_IMAGE_PATH.unlink(missing_ok=True)

    # ── Message handler ──────────────────────────────────────────────
    @client.event(MessageEv)
    def on_message(client_instance: NewClient, event: MessageEv):
        """Process incoming WhatsApp messages with dual security."""
        try:
            msg = event.Message
            info = event.Info

            # ── Extract sender identity ───────────────────────────────
            sender = info.MessageSource.Sender
            sender_id = str(getattr(sender, 'User', '')) or ''

            # Skip messages from self
            if info.MessageSource.IsFromMe:
                return

            # Skip group messages (only DMs)
            if info.MessageSource.IsGroup:
                return

            # ── Extract text ──────────────────────────────────────────
            text = ""
            if msg.conversation:
                text = msg.conversation
            elif msg.extendedTextMessage and msg.extendedTextMessage.text:
                text = msg.extendedTextMessage.text

            if not text or not text.strip():
                return

            text = text.strip()
            reply_jid = info.MessageSource.Chat

            # Cache this JID so the outbox checker can use it for proactive sending
            known_jids[sender_id] = reply_jid

            # ── SECURITY LAYER 1: Contact check ──────────────────────
            # The sender must be a known contact in the phone's address book.
            is_known_contact = False
            contact_name = ""
            try:
                contact_info = client_instance.contact.get_contact(sender)
                is_known_contact = getattr(contact_info, 'Found', False)
                contact_name = (
                    getattr(contact_info, 'FullName', '') or
                    getattr(contact_info, 'FirstName', '') or
                    getattr(contact_info, 'PushName', '') or
                    getattr(contact_info, 'BusinessName', '') or
                    sender_id
                )
            except Exception as e:
                log.warning(f"WhatsApp: could not check contact for {sender_id}: {e}")

            if not is_known_contact:
                log.warning(
                    f"WhatsApp: rejected message from {sender_id} — not a known contact."
                )
                client_instance.send_message(
                    reply_jid,
                    t.get("not_a_contact", "⛔ Access denied. You are not a known contact.")
                )
                return

            # ── SECURITY LAYER 2: Passphrase authorization ────────────
            # If a passphrase is configured, check if this LID is authorized.
            if passphrase:
                if sender_id not in authorized_lids:
                    # Check if the message IS the passphrase
                    if text == passphrase:
                        # Authorize this LID
                        authorized_lids.add(sender_id)
                        # Extract the real JID for outbound messaging
                        jid_user = str(getattr(reply_jid, 'User', '')) or ''
                        jid_server = str(getattr(reply_jid, 'Server', 's.whatsapp.net')) or 's.whatsapp.net'
                        save_authorized_lid(sender_id, contact_name, jid_user, jid_server)
                        log.info(
                            f"WhatsApp: LID {sender_id} ({contact_name}) authorized successfully."
                        )
                        client_instance.send_message(
                            reply_jid,
                            t.get("authorized_ok",
                                "✅ Authorized! You can now send commands to ARIEL.")
                        )
                        return
                    else:
                        # Not authorized and wrong passphrase
                        log.warning(
                            f"WhatsApp: rejected message from {sender_id} ({contact_name}) "
                            f"— contact found but not authorized (wrong passphrase)."
                        )
                        client_instance.send_message(
                            reply_jid,
                            t.get("need_passphrase",
                                "🔐 You are a known contact but you need to send the "
                                "passphrase first to activate ARIEL.")
                        )
                        return

            # ── Both checks passed — process the message ──────────────
            log.info(
                f"WhatsApp: received from {contact_name} ({sender_id}): {text[:80]}..."
            )

            full_response = ""
            try:
                for update_data in ipc.run_task(text, source="whatsapp"):
                    if update_data["type"] == "message":
                        full_response += update_data["content"] + "\n\n"
            except Exception as e:
                full_response = f"❌ Error: {e}"
                log.error(f"WhatsApp IPC error: {e}")

            # ── Send the response back ────────────────────────────────
            if full_response.strip():
                response_text = full_response.strip()
                if len(response_text) > 4000:
                    for i in range(0, len(response_text), 4000):
                        client_instance.send_message(reply_jid, response_text[i:i+4000])
                else:
                    client_instance.send_message(reply_jid, response_text)

                log.info(
                    f"WhatsApp: replied to {contact_name} ({len(response_text)} chars)."
                )

        except Exception as e:
            log.error(f"WhatsApp message handler error: {e}")

    # ── Outbox checker (proactive sending via IPC) ─────────────────
    # The send_whatsapp_message tool queues messages via IPC.
    # This thread polls the orchestrator every 3 seconds for pending
    # outbound messages and sends them through neonize.
    # Uses the known_jids cache to get the real JID objects that work.

    import threading

    def _check_outbox():
        """Poll the orchestrator for pending WhatsApp messages and send them."""
        while True:
            try:
                pending = ipc.get_whatsapp_outbox()
                for item in pending:
                    target_id = item.get("phone", "")
                    message = item.get("message", "")
                    if not target_id or not message:
                        continue

                    # Look up the real JID from our in-memory cache
                    jid = known_jids.get(target_id)
                    if not jid:
                        # Fallback: try to reconstruct from stored data
                        try:
                            from neonize.proto.Neonize_pb2 import JID
                            jid_server = item.get("jid_server", "s.whatsapp.net")
                            jid = JID(User=target_id, Server=jid_server)
                            log.warning(f"WhatsApp outbox: no cached JID for {target_id}, "
                                        f"using reconstructed {target_id}@{jid_server}.")
                        except Exception as e:
                            log.error(f"WhatsApp outbox: cannot build JID for {target_id}: {e}")
                            continue

                    try:
                        client.send_message(jid, message)
                        log.info(f"WhatsApp outbox: sent to {target_id} ({len(message)} chars).")
                    except Exception as e:
                        log.error(f"WhatsApp outbox: send_message failed for {target_id}: {e}")

            except Exception as e:
                if str(e):
                    log.error(f"WhatsApp outbox: poll error: {e}")

            time.sleep(3)

    outbox_thread = threading.Thread(target=_check_outbox, daemon=True)
    outbox_thread.start()

    # ── Connect and run ──────────────────────────────────────────────
    log.info("WhatsApp gateway starting...")
    if passphrase:
        log.info("WhatsApp: passphrase security enabled.")
    else:
        log.info("WhatsApp: no passphrase configured — all contacts accepted.")
    write_status("starting")

    try:
        client.connect()
        neonize_event.wait()
    except KeyboardInterrupt:
        log.info("WhatsApp gateway shutting down.")
        write_status("stopped")
    except Exception as e:
        log.error(f"WhatsApp gateway fatal error: {e}")
        write_status(f"error:{e}")


if __name__ == "__main__":
    run_whatsapp_gateway()
