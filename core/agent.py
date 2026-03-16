"""
core/agent.py — Main controller that orchestrates communication with Claude
and tool execution.

This is the brain of ARIEL. It:
  1. Builds a system prompt from profiles, laws, memories, and screen control config.
  2. Sends messages to the Anthropic API (standard or beta, depending on config).
  3. Handles the agentic loop: if Claude requests a tool, execute it
     and feed the result back until the task is complete.
  4. After each task, generates a summary for long-term memory.

The run() method is a generator that yields status updates so the UI
(gui.py) can render them in real time.
"""

import os
import uuid
import json
from core.utils import BASE_DIR, CONFIG_PATH, load_json
from core.logger import LoggerManager
from core.memory import MemoryManager
from core.executor import ToolExecutor
from core.screen_control import ScreenController
from core.llm_provider import create_provider


class ARIELAgent:
    """Main controller that orchestrates communication with LLM and tool execution."""

    VERSION = "1.20.0"

    # ── Computer Use beta flag (Anthropic-specific) ────────────────
    COMPUTER_USE_BETA = "computer-use-2025-01-24"

    def __init__(self):
        """Initialize the agent: load config, set up logging, memory, tools, and screen control."""
        self.config = load_json(CONFIG_PATH)

        self.uploads_dir = BASE_DIR / "uploads"
        self.uploads_dir.mkdir(parents=True, exist_ok=True)

        self.session_id = str(uuid.uuid4())[:8]

        # Core subsystems (logger first — provider may need it)
        self.logger = LoggerManager(self.config, self.session_id)
        self._init_provider()
        self.memory = MemoryManager(self.logger)
        self.executor = ToolExecutor(self.config, self.logger)
        self.screen = ScreenController(self.config, self.logger)

        self.logger.info(f"ARIEL INITIALIZED. MODE: READY TO RECEIVE TASKS.")

    def _init_provider(self):
        """Create the LLM provider based on the configured backend.

        Supports 'anthropic' (Claude) and 'openai' (LM Studio, Ollama, GPT, etc.).
        The provider handles all API format conversion internally.
        """
        self.provider = create_provider(self.config, self.logger)

    def reload_config(self):
        """Reload configuration from disk and reinitialize dependent components."""
        self.config = load_json(CONFIG_PATH)
        self._init_provider()
        self.screen = ScreenController(self.config, self.logger)

        new_dest = self.config.get("logging", {}).get("output_dest", "Console")
        self.logger.set_output_destination(new_dest)

        self.logger.info("Configuration reloaded from disk.")

    # ── Tool list construction ────────────────────────────────────

    def _build_tool_list(self) -> list:
        """Build the full tool list for the API call.

        Combines:
          - Custom tools from toolindex.json (file ops, web, memory, etc.)
          - The native Computer Use tool (only if Anthropic provider + enabled)
        """
        tools = list(self.executor.api_tool_definitions)

        # Computer Use is Anthropic-specific — only add if using Anthropic
        if self.provider.get_provider_name() == "anthropic":
            cu_tool = self.screen.get_computer_use_tool_definition()
            if cu_tool:
                tools.append(cu_tool)
                self.logger.info("Computer Use tool added to tool list (fallback enabled).")

        return tools

    # ── System prompt ─────────────────────────────────────────────

    def _build_system_prompt(self, task: str = "") -> str:
        """Assemble the system prompt that defines ARIEL's behavior.

        The prompt is composed of several pieces:
          - Screen resolution (for screenshot-based tools).
          - Screen control instructions (UI Automation vs Computer Use).
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
        prompt += f"EXECUTION ENVIRONMENT:\n- Current screen resolution: {screen_width}x{screen_height} pixels.\n\n"

        # ── Screen control instructions (UI Automation / Computer Use) ──
        prompt += self.screen.get_system_prompt_section()

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

    # ── Agentic loop ──────────────────────────────────────────────

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

        # Build the tool list (custom + optionally Computer Use)
        tools = self._build_tool_list()
        use_beta = (self.provider.get_provider_name() == "anthropic"
                    and self.screen.is_computer_use_enabled())

        for step in range(1, max_steps + 1):
            self.logger.info(f"--- STEP {step}/{max_steps} ---")

            try:
                # ── Call the LLM via the provider ──────────────────
                response = self.provider.call(
                    model=self.config["api"]["model"],
                    system_prompt=system_prompt,
                    messages=self.memory.get_api_messages(),
                    tools=tools,
                    max_tokens=self.config["api"].get("max_tokens", 4096),
                    temperature=self.config["api"].get("temperature", 0.0),
                    use_beta=use_beta,
                    beta_flags=[self.COMPUTER_USE_BETA] if use_beta else None,
                )

                # Log token usage
                usage = response.usage
                cache_read = getattr(usage, "cache_read_input_tokens", 0) or 0
                cache_write = getattr(usage, "cache_creation_input_tokens", 0) or 0

                self.logger.log_event("api_call", {
                    "step": step,
                    "provider": self.provider.get_provider_name(),
                    "input_tokens": usage.input_tokens,
                    "output_tokens": usage.output_tokens,
                    "cache_read_tokens": cache_read,
                    "cache_write_tokens": cache_write,
                    "stop_reason": response.stop_reason,
                    "computer_use_beta": use_beta
                })

                # Log a human-friendly summary of cache performance
                if cache_read > 0:
                    self.logger.info(f"Step {step}: {cache_read} tokens from cache (90% cheaper).")
                elif cache_write > 0:
                    self.logger.info(f"Step {step}: {cache_write} tokens written to cache.")

                # Store the assistant's response in short-term memory.
                # Convert custom dataclass objects to plain dicts so they
                # serialize correctly for both IPC and subsequent API calls.
                serialized_content = [
                    b.model_dump() if hasattr(b, "model_dump") else
                    b.dict() if hasattr(b, "dict") else b
                    for b in response.content
                ]
                self.memory.add_assistant_message(serialized_content)

                # ── Yield any text blocks to the UI ─────────────────
                has_text = False
                for block in response.content:
                    if block.type == "text" and block.text.strip():
                        self.logger.info(f"Text response: {block.text[:100]}...")
                        yield {"type": "message", "content": block.text}
                        has_text = True

                # If the model produced no usable text (e.g. spent all tokens
                # on <think> reasoning), warn the user in their language.
                has_tools = any(b.type == "tool_use" for b in response.content)
                if not has_text and not has_tools and response.stop_reason == "end_turn":
                    _warn_key = "warning_model_thinking"
                    from core.utils import get_translations as _get_t
                    _user_lang = self.config.get("agent", {}).get("language", "en")
                    _warn_msg = _get_t(_user_lang).get("gui", {}).get(_warn_key,
                        "⚠️ The local model used all its capacity on internal reasoning "
                        "and could not produce a response. Try again or increase Max Tokens.")
                    self.logger.warning("Model produced empty response (thinking tokens exhausted).")
                    yield {"type": "message", "content": _warn_msg}

                # ── Handle stop reasons ─────────────────────────────
                if response.stop_reason == "end_turn":
                    # Task is complete — generate a summary for long-term memory
                    self.logger.info("Task completed. Generating summary...")
                    yield {"type": "status", "content": "Generating summary for long-term memory..."}

                    try:
                        # Detect the user's spoken language from their profile.
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
                        summary_response = self.provider.call(
                            model=self.config["api"]["model"],
                            system_prompt=f"You are a hyper-strict deduplication expert for a memory system. Your job is to detect ONLY genuinely new information. {lang_instruction}",
                            messages=self.memory.get_api_messages() + [{"role": "user", "content": summary_prompt}],
                            tools=[],
                            max_tokens=150,
                            temperature=0.0,
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
                            tool_name = block.name

                            # ── Route: Computer Use (native Anthropic tool) ──
                            if tool_name == "computer":
                                action = block.input.get("action", "")
                                self.logger.info(f"Computer Use: {action}")
                                yield {"type": "tool_start", "content": f"Computer Use: {action}..."}

                                result = self.screen.computer_use.execute_action(action, block.input)

                                # Format the result for the API
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
                                    "tool": f"computer:{action}",
                                    "result_preview": str(result)[:200]
                                })
                                yield {"type": "tool_end", "content": f"Computer Use: {action} finished."}

                            # ── Route: Custom tools (file ops, web, memory, UI Automation, etc.) ──
                            else:
                                self.logger.info(f"Executing tool: {tool_name}")
                                yield {"type": "tool_start", "content": f"Executing {tool_name}..."}

                                result = self.executor.execute(tool_name, block.input)

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
                                    "tool": tool_name,
                                    "result_preview": str(result)[:200]
                                })
                                yield {"type": "tool_end", "content": f"{tool_name} finished."}

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
