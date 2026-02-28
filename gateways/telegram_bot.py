"""
gateways/telegram_bot.py — Telegram gateway for ARIEL.

Runs as an independent background process (launched by start.py or
from the GUI's Connectors modal). Listens for messages on a Telegram
bot and forwards them to the ARIEL agent for processing.

Supports:
  - Text messages: sent directly to the agent.
  - File uploads: saved to the uploads/ folder and passed to the agent
    with an instruction (either the caption or a default prompt).

Security: Only the chat_id configured in config.json is allowed to
interact with the bot. All other messages are rejected.
"""

import sys
import json
from pathlib import Path
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, ContextTypes

# Allow importing the 'core' package from the gateways/ subdirectory
BASE_DIR = Path(__file__).parent.parent
sys.path.append(str(BASE_DIR))

from core.agent import ARIELAgent
from core.utils import get_translations, load_json


class ArielTelegramGateway:
    """Telegram bot that bridges messages between Telegram and the ARIEL agent."""

    def __init__(self):
        """Load config, translations, and initialize the agent (if enabled)."""
        config_path = BASE_DIR / "settings" / "config.json"
        self.config = load_json(config_path)

        # Load translated strings for bot messages
        lang_code = self.config.get("agent", {}).get("language", "en")
        self.t = get_translations(lang_code)["tg"]

        # Read Telegram-specific settings
        self.tg_config = self.config.get("integrations", {}).get("telegram", {})
        self.enabled = self.tg_config.get("enabled", False)
        # Decrypt the bot token if it's encrypted (prefixed with 'ENC:')
        from core.security import decrypt_if_needed
        self.token = decrypt_if_needed(self.tg_config.get("bot_token", ""))
        self.allowed_chat_id = str(self.tg_config.get("chat_id", "")).strip()

        # Only create the (expensive) agent instance if Telegram is enabled
        if self.enabled and self.token:
            self.agent = ARIELAgent()

    async def start_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle the /start command — greet the user or deny access."""
        if str(update.effective_chat.id) != self.allowed_chat_id:
            await update.message.reply_text(self.t["access_denied"])
            return
        await update.message.reply_text(self.t["start_greeting"])

    async def handle_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Process regular text messages from the allowed user."""
        if str(update.effective_chat.id) != self.allowed_chat_id:
            return

        user_text = update.message.text

        # Show a "thinking" indicator while the agent processes
        status_message = await update.message.reply_text(self.t["thinking"], parse_mode="Markdown")
        await self._process_with_agent(user_text, update, status_message)

    async def handle_document(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Receive a file, save it locally, and pass it to the agent."""
        if str(update.effective_chat.id) != self.allowed_chat_id:
            return

        # Download the file to the uploads/ directory
        file = await update.message.document.get_file()
        file_name = update.message.document.file_name
        save_path = BASE_DIR / "uploads" / file_name

        status_msg = await update.message.reply_text(
            f"{self.t['receiving_file']} `{file_name}`...",
            parse_mode="Markdown"
        )
        await file.download_to_drive(save_path)

        # Use the message caption as the instruction, or fall back to a default
        user_instruction = update.message.caption if update.message.caption else self.t["default_file_prompt"]

        # Build a system-level prompt that tells the agent about the uploaded file
        prompt_with_path = self.t.get(
            "system_file_uploaded",
            "SYSTEM: The user has uploaded a file to {path}. Instruction: {instruction}"
        ).format(path=save_path, instruction=user_instruction)

        await status_msg.edit_text(self.t["file_saved"])
        await self._process_with_agent(prompt_with_path, update, status_msg)

    async def _process_with_agent(self, text: str, update: Update, status_msg):
        """Common logic to execute a task and send results back to Telegram.

        Iterates through the agent's generator, collecting text responses
        and updating the status message with tool activity.
        """
        full_response = ""
        try:
            for update_data in self.agent.run(text):
                if update_data["type"] == "message":
                    full_response += update_data["content"] + "\n\n"
                elif update_data["type"] == "tool_start":
                    await status_msg.edit_text(f"⚙️ *{update_data['content']}*", parse_mode="Markdown")

            # Delete the status message and send the final response
            await status_msg.delete()
            if full_response.strip():
                # Telegram has a 4096-char limit per message — split if needed
                if len(full_response) > 4000:
                    for i in range(0, len(full_response), 4000):
                        await update.message.reply_text(full_response[i:i+4000])
                else:
                    await update.message.reply_text(full_response.strip())
            else:
                await update.message.reply_text(self.t["task_finished"])
        except Exception as e:
            await update.message.reply_text(f"❌ Error: {e}")

    def run(self):
        """Start the Telegram bot polling loop (blocks forever)."""
        if not self.enabled or not self.token:
            return

        app = ApplicationBuilder().token(self.token).build()

        # Register handlers for different message types
        app.add_handler(CommandHandler("start", self.start_command))
        app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self.handle_message))
        app.add_handler(MessageHandler(filters.Document.ALL, self.handle_document))

        # Start polling (this call blocks until the process is terminated)
        app.run_polling()


if __name__ == "__main__":
    ArielTelegramGateway().run()