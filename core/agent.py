"""
core/agent.py — Main controller that orchestrates communication with Claude
and tool execution.

This is the brain of ARIEL. It:
  1. Builds a system prompt from profiles, laws, and memories.
  2. Sends messages to the Anthropic API.
  3. Handles the agentic loop: if Claude requests a tool, execute it
     and feed the result back until the task is complete.
  4. After each task, generates a summary for long-term memory.

The run() method is a generator that yields status updates so the UI
(gui.py) can render them in real time.
"""

import os
import uuid
import json
import anthropic
from core.utils import BASE_DIR, CONFIG_PATH, load_json
from core.logger import LoggerManager
from core.memory import MemoryManager
from core.executor import ToolExecutor


class ARIELAgent:
    """Main controller that orchestrates communication with Claude and tool execution."""

    # Canonical version — used by start.py, gui.py, etc.
    VERSION = "1.16.0"

    def __init__(self):
        """Initialize the agent: load config, set up logging, memory, and tools."""
        self.config = load_json(CONFIG_PATH)

        # Ensure the uploads directory exists (used by Telegram file handler, etc.)
        self.uploads_dir = BASE_DIR / "uploads"
        self.uploads_dir.mkdir(parents=True, exist_ok=True)

        # Unique 8-char ID for this session (used in log filenames, etc.)
        self.session_id = str(uuid.uuid4())[:8]
        self._init_client()

        # Core subsystems
        self.logger = LoggerManager(self.config, self.session_id)
        self.memory = MemoryManager(self.logger)
        self.executor = ToolExecutor(self.config, self.logger)

        self.logger.info(f"ARIEL INITIALIZED. MODE: READY TO RECEIVE TASKS.")

    def _init_client(self):
        """Create the Anthropic API client using the configured key.

        If the key is encrypted (prefixed with 'ENC:'), it's decrypted
        using the session key from the environment. Falls back to the
        ANTHROPIC_API_KEY environment variable if no key is in config.
        """
        from core.security import decrypt_if_needed
        api_key = self.config["api"].get("api_key") or os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            raise ValueError("ANTHROPIC API KEY NOT FOUND IN CONFIG OR ENVIRONMENT.")
        # Decrypt if encrypted, pass through if plaintext
        api_key = decrypt_if_needed(api_key)
        self.client = anthropic.Anthropic(api_key=api_key)

    def _build_system_prompt(self, task: str = "") -> str:
        """Assemble the system prompt that defines ARIEL's behavior.

        The prompt is composed of several pieces:
          - Screen resolution (for screenshot-based tools).
          - Relevant long-term memories (retrieved by semantic search
            against the current task, or most recent if embeddings
            are unavailable).
          - Agent personality profile.
          - User preferences profile.
          - Laws and restrictions (the agent's "constitution").
          - A rule forcing the agent to respond in the user's language.

        Args:
            task: The user's current task text. Used to retrieve the
                  most semantically relevant memories. If empty, falls
                  back to the most recent memories.
        """
        import pyautogui

        # Load all JSON data sources
        laws = load_json(BASE_DIR / self.config["paths"].get("laws", "settings/laws.json"))
        profile = load_json(BASE_DIR / self.config["paths"].get("agent_profile", "profiles/agent.json"))
        user_profile = load_json(BASE_DIR / self.config["paths"].get("user_profile", "profiles/user.json"))

        # Retrieve the most relevant memories for this specific task
        # (semantic search if embeddings available, otherwise most recent)
        relevant_memories = self.memory.search_relevant_memories(task, top_k=5) if task else ["No previous memories."]

        # Detect current screen size for coordinate-based tools
        screen_width, screen_height = pyautogui.size()

        # Build the prompt section by section
        prompt = "You are ARIEL, an advanced AI agent.\n\n"
        prompt += f"EXECUTION ENVIRONMENT:\n- Current screen resolution: {screen_width}x{screen_height} pixels.\n- When using 'screenshot', the returned image will have a red grid every 100 pixels.\n\n"
        prompt += f"LONG-TERM MEMORIES (Context from previous sessions, ranked by relevance to current task):\n" + "\n".join(f"- {m}" for m in relevant_memories) + "\n\n"
        prompt += f"AGENT PROFILE:\n{json.dumps(profile, indent=2)}\n\n"
        prompt += f"USER PROFILE:\n{json.dumps(user_profile, indent=2)}\n\n"
        prompt += f"LAWS AND RESTRICTIONS:\n{json.dumps(laws, indent=2)}\n\n"

        # ── Enforce user preferences as concrete instructions ───────
        user_prefs = user_profile.get("preferences", {}).get("communication", {})

        if user_prefs.get("confirm_before_destructive", True):
            prompt += (
                "DESTRUCTIVE ACTION RULE: Before executing ANY of these tools — "
                "file_delete, file_move, run_command (with rm, del, format, shutdown, reboot, or similar) "
                "— you MUST first explain what you are about to do and ask the user for explicit "
                "confirmation in the chat (e.g., '¿Confirmo?'). Do NOT execute until the user says yes. "
                "This rule overrides all other instructions.\n\n"
            )

        # Force the agent to match the user's language dynamically
        prompt += "CRITICAL RULE: ALWAYS RESPOND IN THE EXACT SAME LANGUAGE THE USER IS USING. IF THE USER SPEAKS SPANISH, REPLY IN SPANISH. IF ENGLISH, REPLY IN ENGLISH.\n\n"

        # Auto-fill user profile from conversation
        prompt += (
            "USER PROFILE AUTO-UPDATE RULE: If the user mentions personal information during the conversation "
            "that matches fields in the user profile (name, location, timezone, language, role, industry, "
            "hobbies, preferred browser, editor, terminal, desktop path, documents path, trusted apps, or notes), "
            "you MUST silently update the user profile file (profiles/user.json) using file_read and file_write. "
            "Read the current JSON, merge the new data into the appropriate fields, and write it back. "
            "Do NOT ask for permission — just do it and briefly confirm what you updated."
        )
        return prompt

    def run(self, task: str):
        """Execute a task using the agentic loop. This is a generator.

        Yields dicts with a 'type' key indicating the kind of update:
          - {"type": "message",    "content": "..."} — text from Claude
          - {"type": "tool_start", "content": "..."} — a tool is about to run
          - {"type": "tool_end",   "content": "..."} — a tool just finished
          - {"type": "status",     "content": "..."} — informational status
          - {"type": "error",      "content": "..."} — something went wrong
          - {"type": "done",       "content": "..."} — task completed

        The loop runs until Claude signals 'end_turn' or the maximum
        number of steps is reached.
        """
        self.logger.info(f"=== STARTING TASK: {task} ===")

        # Reset short-term memory for a fresh conversation
        self.memory.reset()
        self.memory.add_user_message(task)
        system_prompt = self._build_system_prompt(task)
        max_steps = self.config["agent"].get("max_steps_per_task", 15)

        for step in range(1, max_steps + 1):
            self.logger.info(f"--- STEP {step}/{max_steps} ---")

            try:
                # ── Call the Anthropic API ──────────────────────────
                # Prompt caching: pass system as a list of content blocks
                # with cache_control on the last block. This tells the API
                # to cache everything (tools + system) so that on step 2+
                # of the same task, those tokens are read from cache at
                # 90% discount instead of reprocessed.
                # Cache lives for 5 minutes and refreshes on each hit.
                response = self.client.messages.create(
                    model=self.config["api"]["model"],
                    max_tokens=self.config["api"].get("max_tokens", 4096),
                    temperature=self.config["api"].get("temperature", 0.0),
                    system=[{
                        "type": "text",
                        "text": system_prompt,
                        "cache_control": {"type": "ephemeral"}
                    }],
                    messages=self.memory.get_api_messages(),
                    tools=self.executor.api_tool_definitions
                )

                # Log token usage including cache stats for cost tracking.
                # cache_creation_input_tokens: tokens written to cache (1.25x cost, first call only)
                # cache_read_input_tokens: tokens read from cache (0.1x cost — the big saving)
                usage = response.usage
                cache_read = getattr(usage, "cache_read_input_tokens", 0) or 0
                cache_write = getattr(usage, "cache_creation_input_tokens", 0) or 0

                self.logger.log_event("api_call", {
                    "step": step,
                    "input_tokens": usage.input_tokens,
                    "output_tokens": usage.output_tokens,
                    "cache_read_tokens": cache_read,
                    "cache_write_tokens": cache_write,
                    "stop_reason": response.stop_reason
                })

                # Log a human-friendly summary of cache performance
                if cache_read > 0:
                    self.logger.info(f"Step {step}: {cache_read} tokens from cache (90% cheaper).")
                elif cache_write > 0:
                    self.logger.info(f"Step {step}: {cache_write} tokens written to cache.")

                # Store the assistant's response in short-term memory
                self.memory.add_assistant_message(response.content)

                # ── Yield any text blocks to the UI ─────────────────
                for block in response.content:
                    if block.type == "text" and block.text.strip():
                        self.logger.info(f"Text response: {block.text[:100]}...")
                        yield {"type": "message", "content": block.text}

                # ── Handle stop reasons ─────────────────────────────
                if response.stop_reason == "end_turn":
                    # Task is complete — generate a summary for long-term memory
                    self.logger.info("Task completed. Generating summary...")
                    yield {"type": "status", "content": "Generating summary for long-term memory..."}

                    try:
                        # Detect the user's spoken language from their profile.
                        # This is more reliable than the UI language (agent.language),
                        # since a user might have the UI in English but chat in Spanish.
                        user_profile_path = self.config["paths"].get("user_profile", "profiles/user.json")
                        user_profile_data = load_json(BASE_DIR / user_profile_path)
                        user_lang = user_profile_data.get("identity", {}).get("language", "en")

                        if user_lang == "es":
                            lang_instruction = "DEBES responder SIEMPRE en español."
                        else:
                            lang_instruction = "You MUST respond in English."

                        # Retrieve existing memories related to this task so Claude
                        # can compare and avoid storing duplicate information.
                        existing_memories = self.memory.search_relevant_memories(task, top_k=10)
                        existing_block = "\n".join(f"- {m}" for m in existing_memories)

                        summary_prompt = (
                            "Analyze the previous conversation. Has any GENUINELY NEW fact been revealed "
                            "that is NOT already stored in the existing memories below?\n\n"
                            f"EXISTING MEMORIES (already stored — do NOT repeat these):\n{existing_block}\n\n"
                            "RULES:\n"
                            "- If ALL information from the conversation is already covered by the existing memories, "
                            "respond ONLY with the word NOTHING_RELEVANT.\n"
                            "- If there IS new information, write ONLY the new facts. Do NOT repeat what is already stored.\n"
                            f"- {lang_instruction}\n"
                            "- Be concise: bare facts only, no headers, no categories, no analysis."
                        )
                        summary_response = self.client.messages.create(
                            model=self.config["api"]["model"],
                            max_tokens=150,
                            system=[{
                                "type": "text",
                                "text": f"You are a hyper-strict deduplication expert for a memory system. Your job is to detect ONLY genuinely new information. {lang_instruction}",
                                "cache_control": {"type": "ephemeral"}
                            }],
                            messages=self.memory.get_api_messages() + [{"role": "user", "content": summary_prompt}]
                        )
                        summary_text = summary_response.content[0].text.strip()

                        # Only save the summary if it contains new information
                        if "NOTHING_RELEVANT" not in summary_text.upper():
                            self.memory.update_long_term_memory(summary_text)
                            self.logger.info(f"Summary saved: {summary_text}")
                            yield {"type": "status", "content": "Long-term memory updated."}
                        else:
                            self.logger.info("No new information for long-term memory.")
                    except Exception as e:
                        self.logger.error(f"Error generating memory summary: {e}")

                    # ── Notify via Telegram if the user enabled it ──────
                    try:
                        _up_path = self.config["paths"].get("user_profile", "profiles/user.json")
                        _up_data = load_json(BASE_DIR / _up_path)
                        _up_prefs = _up_data.get("preferences", {}).get("communication", {})
                        _up_lang = _up_data.get("identity", {}).get("language", "en")
                        tg_conf = self.config.get("integrations", {}).get("telegram", {})
                        if _up_prefs.get("notify_telegram_on_complete", False) and tg_conf.get("enabled", False) and tg_conf.get("bot_token"):
                            if _up_lang == "es":
                                tg_msg = f"✅ Tarea completada: {task[:100]}"
                            else:
                                tg_msg = f"✅ Task completed: {task[:100]}"
                            self.executor.execute("send_telegram_message", {"message": tg_msg})
                            self.logger.info("Telegram notification sent.")
                    except Exception as e:
                        self.logger.error(f"Error sending Telegram notification: {e}")

                    yield {"type": "done", "content": "Task finished."}
                    break

                elif response.stop_reason == "tool_use":
                    # Claude wants to use one or more tools — execute them
                    tool_results_blocks = []
                    for block in response.content:
                        if block.type == "tool_use":
                            self.logger.info(f"Executing tool: {block.name}")
                            yield {"type": "tool_start", "content": f"Executing {block.name}..."}

                            # Run the tool and capture its output
                            result = self.executor.execute(block.name, block.input)

                            # Format the result for the API (images need special handling)
                            if isinstance(result, dict) and result.get("type") == "image":
                                formatted_content = [{
                                    "type": "image",
                                    "source": {
                                        "type": "base64",
                                        "media_type": result.get("media_type", "image/png"),
                                        "data": result.get("data", "")
                                    }
                                }]
                            else:
                                formatted_content = str(result)

                            tool_results_blocks.append({
                                "type": "tool_result",
                                "tool_use_id": block.id,
                                "content": formatted_content
                            })

                            self.logger.log_event("tool_execution", {
                                "tool": block.name,
                                "result_preview": str(result)[:200]
                            })
                            yield {"type": "tool_end", "content": f"{block.name} finished."}

                    # Feed tool results back into the conversation
                    self.memory.add_user_message(tool_results_blocks)

                else:
                    self.logger.warning(f"Unknown stop reason: {response.stop_reason}")
                    yield {"type": "error", "content": "Unknown stop reason."}
                    break

            except Exception as e:
                self.logger.error(f"Critical error during execution loop: {e}")
                yield {"type": "error", "content": f"An error occurred: {e}"}
                break
        else:
            # The for-loop completed without breaking — max steps reached
            self.logger.warning(f"Maximum steps reached ({max_steps}). Task aborted.")
            yield {"type": "error", "content": "Maximum allowed steps reached."}