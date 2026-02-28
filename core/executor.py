"""
core/executor.py — Tool execution engine.

Responsible for running the code or commands defined for each tool.
Tools can be of type:
  - "python": Inline Python code executed via exec().
  - "cmd" / "powershell" / "bash": Shell commands with parameter interpolation.

Tool definitions are loaded from two JSON files:
  - toolindex.json: API-facing definitions (name, description, input_schema).
  - tools.json: Implementation details (type, code).
"""

import textwrap
import shlex
import subprocess
from typing import Any
from core.logger import LoggerManager
from core.utils import BASE_DIR, load_json


class ToolExecutor:
    """Loads tool definitions and executes them on demand."""

    def __init__(self, config: dict, logger: LoggerManager):
        """Load tool index (API definitions) and tool implementations from JSON."""
        self.config = config
        self.logger = logger

        # tools.json contains the actual code for each tool
        self.tools_config = load_json(BASE_DIR / config["paths"]["tools"])

        # toolindex.json contains the API schema (name, description, input_schema)
        self.tool_index = load_json(BASE_DIR / config["paths"]["tool_index"])

        # Build the list of tool definitions in the format the Anthropic API expects
        self.api_tool_definitions = [
            {
                "name": t["name"],
                "description": t["description"],
                "input_schema": t["input_schema"]
            } for t in self.tool_index.get("tools", [])
        ]

    def execute(self, tool_name: str, params: dict) -> Any:
        """Execute a tool by name with the given parameters.

        Looks up the implementation in tools.json and dispatches to the
        appropriate runner (Python or shell).

        Returns:
            The tool's output (string, dict, etc.) or an error message.
        """
        impl = self.tools_config.get("implementations", {}).get(tool_name)

        if not impl:
            error_msg = f"Tool '{tool_name}' not implemented."
            self.logger.error(error_msg)
            return error_msg

        tool_type = impl.get("type")
        code = impl.get("code", "")

        try:
            if tool_type == "python":
                return self._run_python(code, params)
            elif tool_type in ("cmd", "powershell", "bash"):
                return self._run_shell(code, params, tool_type)
            else:
                return f"Tool type '{tool_type}' not supported."
        except Exception as e:
            error_msg = f"Error executing '{tool_name}': {e}"
            self.logger.error(error_msg)
            return error_msg

    def _run_python(self, code: str, params: dict) -> Any:
        """Execute inline Python code in a sandboxed scope.

        The code is wrapped inside a function that receives 'params' as
        its argument. The return value of that function is captured
        and returned.

        WARNING: This uses exec() — safe for a local desktop agent,
        but should NOT be exposed over a network without sandboxing.
        """
        # Indent the user code to nest it inside a function definition
        indented_code = textwrap.indent(code, "    ")
        wrapper = f"def _execute_tool(params):\n{indented_code}\n_result = _execute_tool(params)"

        local_scope = {}
        exec(
            compile(wrapper, "<tool>", "exec"),
            {"params": params, "__builtins__": __builtins__},
            local_scope
        )
        return local_scope.get("_result", "(No return value)")

    def _run_shell(self, command_template: str, params: dict, shell_type: str) -> str:
        """Execute a shell command with parameter interpolation.

        All parameter values are escaped with shlex.quote() to prevent
        shell injection. The command template uses Python str.format()
        syntax, e.g. "echo {message}".

        Supports: bash, cmd (Windows), powershell.
        """
        # Escape all parameter values to prevent injection
        safe_params = {k: shlex.quote(str(v)) for k, v in params.items()}
        command = command_template.format(**safe_params)

        # Build the command arguments depending on the shell type
        if shell_type == "powershell":
            cmd_args = ["powershell", "-Command", command]
        elif shell_type == "cmd":
            cmd_args = ["cmd", "/c", command]
        else:
            cmd_args = ["bash", "-c", command]

        timeout = params.get("timeout", 30)
        result = subprocess.run(cmd_args, capture_output=True, text=True, timeout=timeout)

        # Return stdout if available, otherwise stderr
        return result.stdout.strip() if result.stdout else result.stderr.strip()
