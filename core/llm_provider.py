"""
core/llm_provider.py — LLM provider abstraction layer.

Allows ARIEL to work with different AI backends without changing
the agentic loop. Each provider translates between ARIEL's internal
format (Anthropic-like) and the target API's native format.

Supported providers:
  - anthropic: Claude models via the Anthropic API.
  - openai: Any OpenAI-compatible API (OpenAI, LM Studio, Ollama, etc.)

ARIEL's internal format (the "lingua franca"):
  - Tools: Anthropic-style dicts with name/description/input_schema.
  - Messages: {"role": "user"|"assistant", "content": str|list}.
  - Response blocks: ContentBlock objects with .type, .text, .id, etc.

Each provider converts to/from its native API format internally,
so the rest of ARIEL doesn't need to know which backend is active.
"""

import os
import json
import re
import uuid
from typing import List, Dict, Any, Optional
from dataclasses import dataclass, field


# ═══════════════════════════════════════════════════════════════════
#  UTILITY: STRIP REASONING/THINKING TAGS
#  Many local models (Qwen3.5, DeepSeek, etc.) wrap their internal
#  reasoning in <think>...</think> tags. We strip these so the user
#  only sees the final answer, not the model's thought process.
# ═══════════════════════════════════════════════════════════════════

_THINK_COMPLETE = re.compile(r"<think>.*?</think>\s*", re.DOTALL)
_THINK_UNCLOSED_END = re.compile(r"^.*?</think>\s*", re.DOTALL)

def _strip_thinking_tags(text: str) -> str:
    """Remove <think>...</think> blocks from model output.

    Handles three patterns:
      1. Complete:  <think>reasoning</think>actual answer
      2. No opener: reasoning</think>actual answer
      3. Unclosed:  <think>reasoning... (model ran out of tokens thinking)

    Case 3 returns empty string — the model wasted all tokens on reasoning
    and never produced an answer.
    """
    if not text:
        return text

    # Case 1: strip complete <think>...</think> pairs
    cleaned = _THINK_COMPLETE.sub("", text)

    # Case 2: </think> without opening <think> (started reasoning from beginning)
    if "</think>" in cleaned:
        cleaned = _THINK_UNCLOSED_END.sub("", cleaned)

    # Case 3: <think> opened but never closed (ran out of tokens while thinking)
    if "<think>" in cleaned and "</think>" not in cleaned:
        cleaned = re.sub(r"<think>.*", "", cleaned, flags=re.DOTALL)

    return cleaned.strip()


# ═══════════════════════════════════════════════════════════════════
#  NORMALIZED RESPONSE OBJECTS
#  These mimic Anthropic SDK objects so the agentic loop in agent.py
#  can access .type, .text, .name, .input, .id without changes.
# ═══════════════════════════════════════════════════════════════════

@dataclass
class TextBlock:
    """A text response block."""
    text: str
    type: str = "text"

    def model_dump(self):
        return {"type": self.type, "text": self.text}


@dataclass
class ToolUseBlock:
    """A tool call request block."""
    id: str
    name: str
    input: dict
    type: str = "tool_use"

    def model_dump(self):
        return {"type": self.type, "id": self.id, "name": self.name, "input": self.input}


@dataclass
class Usage:
    """Token usage statistics."""
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_input_tokens: int = 0
    cache_creation_input_tokens: int = 0


@dataclass
class NormalizedResponse:
    """Normalized API response that works identically to Anthropic's response.

    The agent loop accesses:
      response.content     → list of TextBlock / ToolUseBlock
      response.stop_reason → "end_turn" or "tool_use"
      response.usage       → Usage object with token counts
    """
    content: list
    stop_reason: str
    usage: Usage = field(default_factory=Usage)


# ═══════════════════════════════════════════════════════════════════
#  BASE PROVIDER
# ═══════════════════════════════════════════════════════════════════

class BaseLLMProvider:
    """Abstract base for LLM providers."""

    def __init__(self, config: dict, logger=None):
        self.config = config
        self.logger = logger

    def init_client(self):
        """Initialize the API client. Called on startup and reload."""
        raise NotImplementedError

    def call(self, model: str, system_prompt: str, messages: list,
             tools: list, max_tokens: int, temperature: float,
             use_beta: bool = False, beta_flags: list = None) -> NormalizedResponse:
        """Make an API call and return a NormalizedResponse."""
        raise NotImplementedError

    def get_provider_name(self) -> str:
        raise NotImplementedError


# ═══════════════════════════════════════════════════════════════════
#  ANTHROPIC PROVIDER
# ═══════════════════════════════════════════════════════════════════

class AnthropicProvider(BaseLLMProvider):
    """Provider for Claude models via the Anthropic API.

    This is a thin wrapper — Anthropic is ARIEL's native format,
    so minimal conversion is needed. The main job is normalizing
    the SDK response objects into our dataclasses.
    """

    COMPUTER_USE_BETA = "computer-use-2025-01-24"

    def init_client(self):
        import anthropic
        from core.security import decrypt_if_needed

        api_key = self.config["api"].get("api_key") or os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            raise ValueError("ANTHROPIC API KEY NOT FOUND IN CONFIG OR ENVIRONMENT.")
        api_key = decrypt_if_needed(api_key)
        self.client = anthropic.Anthropic(api_key=api_key)

    def call(self, model, system_prompt, messages, tools,
             max_tokens, temperature, use_beta=False, beta_flags=None):

        api_kwargs = dict(
            model=model,
            max_tokens=max_tokens,
            temperature=temperature,
            system=[{
                "type": "text",
                "text": system_prompt,
                "cache_control": {"type": "ephemeral"}
            }],
            messages=messages,
            tools=tools,
        )

        if use_beta:
            response = self.client.beta.messages.create(
                **api_kwargs,
                betas=beta_flags or [self.COMPUTER_USE_BETA],
            )
        else:
            response = self.client.messages.create(**api_kwargs)

        # Normalize the SDK response into our standard format
        content = []
        for block in response.content:
            if block.type == "text":
                content.append(TextBlock(text=block.text))
            elif block.type == "tool_use":
                content.append(ToolUseBlock(
                    id=block.id,
                    name=block.name,
                    input=block.input,
                ))

        usage_obj = response.usage
        usage = Usage(
            input_tokens=usage_obj.input_tokens,
            output_tokens=usage_obj.output_tokens,
            cache_read_input_tokens=getattr(usage_obj, "cache_read_input_tokens", 0) or 0,
            cache_creation_input_tokens=getattr(usage_obj, "cache_creation_input_tokens", 0) or 0,
        )

        return NormalizedResponse(
            content=content,
            stop_reason=response.stop_reason,
            usage=usage,
        )

    def get_provider_name(self):
        return "anthropic"


# ═══════════════════════════════════════════════════════════════════
#  OPENAI-COMPATIBLE PROVIDER
#  Works with: OpenAI, LM Studio, Ollama, vLLM, Together, etc.
# ═══════════════════════════════════════════════════════════════════

class OpenAIProvider(BaseLLMProvider):
    """Provider for any OpenAI-compatible API.

    Handles format conversion between ARIEL's internal Anthropic-like
    format and the OpenAI chat completions API. This includes:
      - System prompt: content block list → system message
      - Tools: Anthropic format → OpenAI function format
      - Messages: mixed content blocks → OpenAI messages
      - Response: choices/tool_calls → NormalizedResponse
    """

    def init_client(self):
        from openai import OpenAI

        api_conf = self.config["api"]
        api_key = api_conf.get("api_key", "")

        # Decrypt if encrypted (e.g. for OpenAI proper)
        from core.security import decrypt_if_needed
        api_key = decrypt_if_needed(api_key)

        # Local servers (LM Studio, Ollama) often don't need a real key
        if not api_key or api_key.strip().startswith("[") or api_key.strip() == "":
            api_key = "not-needed"

        base_url = api_conf.get("base_url", "").strip()
        if not base_url:
            base_url = "http://localhost:1234/v1"

        self.client = OpenAI(api_key=api_key, base_url=base_url)

        if self.logger:
            self.logger.info(f"OpenAI-compatible client initialized: {base_url}")

    def call(self, model, system_prompt, messages, tools,
             max_tokens, temperature, use_beta=False, beta_flags=None):

        # Convert messages from Anthropic format to OpenAI format
        oai_messages = self._convert_messages(system_prompt, messages)

        # Convert tools from Anthropic format to OpenAI function format
        oai_tools = self._convert_tools(tools)

        # Build the API call kwargs
        api_kwargs = dict(
            model=model,
            messages=oai_messages,
            max_tokens=max_tokens,
            temperature=temperature,
        )

        # Only include tools if we have any
        if oai_tools:
            api_kwargs["tools"] = oai_tools

        response = self.client.chat.completions.create(**api_kwargs)

        return self._normalize_response(response)

    # ── Format conversion helpers ──────────────────────────────────

    def _convert_tools(self, anthropic_tools: list) -> list:
        """Convert Anthropic-format tool defs to OpenAI function format.

        Anthropic: {"name": "x", "description": "y", "input_schema": {...}}
        OpenAI:    {"type": "function", "function": {"name": "x", "description": "y", "parameters": {...}}}

        Skips the Anthropic-native 'computer' tool (Computer Use) since
        it's not supported by OpenAI-compatible endpoints.
        """
        oai_tools = []
        for tool in anthropic_tools:
            # Skip Anthropic-native tools (Computer Use, etc.)
            if tool.get("type") in ("computer_20241022", "computer_20250124"):
                continue

            oai_tools.append({
                "type": "function",
                "function": {
                    "name": tool["name"],
                    "description": tool.get("description", ""),
                    "parameters": tool.get("input_schema", {}),
                }
            })
        return oai_tools

    def _convert_messages(self, system_prompt: str, messages: list) -> list:
        """Convert Anthropic-format conversation history to OpenAI format.

        Handles three message types:
          1. Simple user text → {"role": "user", "content": "text"}
          2. Assistant with text+tool_use blocks → split into content + tool_calls
          3. User with tool_result blocks → multiple {"role": "tool", ...} messages
        """
        oai = [{"role": "system", "content": system_prompt}]

        for msg in messages:
            role = msg.get("role", "user")
            content = msg.get("content")

            if role == "user":
                if isinstance(content, str):
                    oai.append({"role": "user", "content": content})
                elif isinstance(content, list):
                    # Tool result blocks from the agentic loop
                    for block in content:
                        if isinstance(block, dict) and block.get("type") == "tool_result":
                            tool_content = block.get("content", "")
                            # Images/complex content → stringify
                            if isinstance(tool_content, list):
                                tool_content = json.dumps(tool_content, ensure_ascii=False)
                            oai.append({
                                "role": "tool",
                                "tool_call_id": block.get("tool_use_id", ""),
                                "content": str(tool_content),
                            })
                        else:
                            # Generic content block — stringify
                            oai.append({"role": "user", "content": str(block)})

            elif role == "assistant":
                if isinstance(content, str):
                    oai.append({"role": "assistant", "content": content})
                elif isinstance(content, list):
                    text_parts = []
                    tool_calls = []

                    for block in content:
                        btype = getattr(block, "type", None) or (block.get("type") if isinstance(block, dict) else "")

                        if btype == "text":
                            text_parts.append(
                                getattr(block, "text", None) or block.get("text", "")
                            )
                        elif btype == "tool_use":
                            bid = getattr(block, "id", None) or block.get("id", str(uuid.uuid4())[:8])
                            bname = getattr(block, "name", None) or block.get("name", "")
                            binput = getattr(block, "input", None) or block.get("input", {})
                            tool_calls.append({
                                "id": bid,
                                "type": "function",
                                "function": {
                                    "name": bname,
                                    "arguments": json.dumps(binput, ensure_ascii=False),
                                }
                            })

                    assistant_msg = {"role": "assistant"}
                    assistant_msg["content"] = "\n".join(text_parts) if text_parts else None
                    if tool_calls:
                        assistant_msg["tool_calls"] = tool_calls
                    oai.append(assistant_msg)

        return oai

    def _normalize_response(self, response) -> NormalizedResponse:
        """Convert an OpenAI ChatCompletion response to NormalizedResponse."""
        choice = response.choices[0]
        message = choice.message
        content = []

        # Extract text (strip reasoning/thinking tags from local models)
        if message.content:
            cleaned = _strip_thinking_tags(message.content)
            if cleaned:
                content.append(TextBlock(text=cleaned))

        # Extract tool calls
        if message.tool_calls:
            for tc in message.tool_calls:
                try:
                    args = json.loads(tc.function.arguments)
                except (json.JSONDecodeError, TypeError):
                    args = {"raw": tc.function.arguments}

                content.append(ToolUseBlock(
                    id=tc.id or str(uuid.uuid4())[:8],
                    name=tc.function.name,
                    input=args,
                ))

        # Map OpenAI stop reasons to Anthropic equivalents
        stop_reason_map = {
            "stop": "end_turn",
            "length": "end_turn",
            "tool_calls": "tool_use",
            "function_call": "tool_use",
        }
        stop_reason = stop_reason_map.get(choice.finish_reason, "end_turn")

        # If the model ran out of tokens while thinking (no usable content
        # after stripping thinking tags), add an empty text block.
        # The agent loop will detect this and yield a translated warning.
        has_text = any(isinstance(b, TextBlock) for b in content)
        has_tools = any(isinstance(b, ToolUseBlock) for b in content)
        if not has_text and not has_tools:
            content.append(TextBlock(text=""))

        # Extract token usage
        usage = Usage()
        if response.usage:
            usage.input_tokens = response.usage.prompt_tokens or 0
            usage.output_tokens = response.usage.completion_tokens or 0

        return NormalizedResponse(
            content=content,
            stop_reason=stop_reason,
            usage=usage,
        )

    def get_provider_name(self):
        return "openai"


# ═══════════════════════════════════════════════════════════════════
#  FACTORY
# ═══════════════════════════════════════════════════════════════════

def create_provider(config: dict, logger=None) -> BaseLLMProvider:
    """Create the appropriate LLM provider based on config.

    Reads config["api"]["provider"] to determine which backend to use:
      - "anthropic" → AnthropicProvider (Claude)
      - "openai"    → OpenAIProvider (LM Studio, Ollama, OpenAI, etc.)
    """
    provider_name = config.get("api", {}).get("provider", "anthropic").lower()

    if provider_name == "openai":
        provider = OpenAIProvider(config, logger)
    else:
        provider = AnthropicProvider(config, logger)

    provider.init_client()

    if logger:
        logger.info(f"LLM provider initialized: {provider.get_provider_name()}")

    return provider
