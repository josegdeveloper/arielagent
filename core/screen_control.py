"""
core/screen_control.py — Hybrid screen control: UI Automation + Computer Use.

Provides two complementary methods for interacting with the desktop:

  1. UI Automation (primary): Reads the Windows Accessibility Tree via
     pywinauto's UIA backend. Each UI element exposes its name, type,
     state, and coordinates — so the agent can click "the Save button"
     instead of guessing pixel coordinates. Fast, cheap (text-only),
     and reliable for standard Windows applications.

  2. Computer Use (optional fallback): Uses Anthropic's native computer
     tool to capture screenshots and execute mouse/keyboard actions
     via vision. More expensive (image tokens) but works with ANY
     application, including those without accessibility support.

The agent prefers UI Automation and only falls back to Computer Use
when explicitly enabled in config and when UI Automation cannot find
the requested element.

Dependencies:
  - pywinauto (pip install pywinauto) — for UI Automation
  - pyautogui (already required) — for Computer Use action execution
  - Pillow (already required) — for screenshot processing
"""

import math
import base64
import io
from typing import Any, Dict, List, Optional, Tuple
from core.logger import LoggerManager


# ═══════════════════════════════════════════════════════════════════
#  UI AUTOMATION — Accessibility Tree
# ═══════════════════════════════════════════════════════════════════

class UIAutomation:
    """Interacts with Windows desktop via the UI Automation API.

    Uses pywinauto's UIA backend to read the accessibility tree —
    the same structured data that screen readers use. This gives the
    agent a text-based map of every button, field, menu, and label
    on screen, without needing to interpret screenshots.

    If pywinauto is not installed, all methods degrade gracefully
    and is_available() returns False.
    """

    def __init__(self, logger: LoggerManager):
        self.logger = logger
        self._available = None

    def is_available(self) -> bool:
        """Check if pywinauto is installed and usable."""
        if self._available is None:
            try:
                from pywinauto import Desktop  # noqa: F401
                self._available = True
                self.logger.info("UI Automation available (pywinauto found).")
            except ImportError:
                self._available = False
                self.logger.warning(
                    "pywinauto not installed. UI Automation disabled. "
                    "Install with: pip install pywinauto"
                )
        return self._available

    def get_snapshot(self, window_title: str = "", max_elements: int = 80) -> str:
        """Return a text snapshot of the accessibility tree.

        Scans the active window (or a specific window by title) and
        returns a structured text listing of all interactive elements
        with their names, types, states, and coordinates.

        Args:
            window_title: Filter by window title (substring match).
                          If empty, uses the foreground window.
            max_elements: Maximum number of elements to return
                          (prevents token explosion on complex UIs).

        Returns:
            A formatted string describing visible UI elements, or
            an error message if the tree cannot be read.
        """
        if not self.is_available():
            return "ERROR: pywinauto not installed."

        try:
            from pywinauto import Desktop

            desktop = Desktop(backend="uia")

            # Find the target window
            if window_title:
                windows = desktop.windows(title_re=f".*{window_title}.*", visible_only=True)
                if not windows:
                    return f"No visible window found matching '{window_title}'."
                target = windows[0]
            else:
                # Use the foreground window
                import pywinauto
                target = pywinauto.application.Application(backend="uia").connect(active_only=True).active()

            # Collect interactive elements from the window
            elements = []
            try:
                descendants = target.descendants(control_type=None)
            except Exception:
                # Fallback: try children only (one level deep)
                descendants = target.children()

            for elem in descendants[:max_elements * 2]:  # Over-fetch, then filter
                try:
                    # Only include elements that are visible and potentially interactive
                    if not elem.is_visible():
                        continue

                    control_type = elem.element_info.control_type or "Unknown"
                    name = elem.element_info.name or ""
                    auto_id = elem.element_info.automation_id or ""
                    rect = elem.rectangle()

                    # Skip elements with no meaningful identity
                    if not name and not auto_id:
                        continue

                    # Skip tiny or zero-size elements
                    if rect.width() < 5 or rect.height() < 5:
                        continue

                    # Calculate center coordinates for clicking
                    cx = rect.left + rect.width() // 2
                    cy = rect.top + rect.height() // 2

                    # Build element description
                    state_parts = []
                    try:
                        if hasattr(elem, 'is_enabled') and not elem.is_enabled():
                            state_parts.append("disabled")
                        if hasattr(elem, 'is_selected') and elem.is_selected():
                            state_parts.append("selected")
                        if hasattr(elem, 'get_toggle_state'):
                            toggle = elem.get_toggle_state()
                            if toggle is not None:
                                state_parts.append(f"toggled={toggle}")
                    except Exception:
                        pass

                    state_str = f" [{', '.join(state_parts)}]" if state_parts else ""

                    # Get current value for editable elements
                    value_str = ""
                    try:
                        if control_type in ("Edit", "ComboBox", "Slider"):
                            val = elem.get_value()
                            if val:
                                value_str = f' value="{val}"'
                    except Exception:
                        pass

                    entry = (
                        f"  [{control_type}] "
                        f'"{name}"'
                        f"{value_str}{state_str} "
                        f"— center=({cx},{cy}) "
                        f"size={rect.width()}x{rect.height()}"
                    )
                    if auto_id:
                        entry += f" id={auto_id}"

                    elements.append(entry)

                    if len(elements) >= max_elements:
                        break

                except Exception:
                    continue  # Skip elements that can't be read

            if not elements:
                return f"Window '{target.element_info.name}' found but no interactive elements detected."

            header = f"Window: \"{target.element_info.name}\"\n"
            header += f"Elements ({len(elements)}):\n"
            return header + "\n".join(elements)

        except Exception as e:
            self.logger.error(f"UI Automation snapshot failed: {e}")
            return f"ERROR: Could not read accessibility tree: {e}"

    def click_element(self, name: str = "", control_type: str = "",
                      automation_id: str = "", window_title: str = "") -> str:
        """Click a UI element identified by name, type, or automation ID.

        Searches the accessibility tree for a matching element and
        performs a click on it. At least one of name, control_type,
        or automation_id must be provided.

        Returns:
            A success or error message.
        """
        if not self.is_available():
            return "ERROR: pywinauto not installed."

        try:
            element = self._find_element(name, control_type, automation_id, window_title)
            if element is None:
                return self._not_found_message(name, control_type, automation_id)

            element.click_input()
            desc = name or automation_id or control_type
            self.logger.info(f"UI click: '{desc}'")
            return f"Clicked '{desc}' successfully."

        except Exception as e:
            self.logger.error(f"UI click failed: {e}")
            return f"ERROR: Click failed: {e}"

    def type_in_element(self, text: str, name: str = "",
                        automation_id: str = "", window_title: str = "",
                        clear_first: bool = False) -> str:
        """Type text into a UI element (edit field, combo box, etc.).

        If name/automation_id are provided, finds and focuses the element first.
        If not provided, types into the currently focused element.

        Args:
            text: The text to type.
            clear_first: If True, clears the field before typing.

        Returns:
            A success or error message.
        """
        if not self.is_available():
            return "ERROR: pywinauto not installed."

        try:
            if name or automation_id:
                element = self._find_element(name, "", automation_id, window_title)
                if element is None:
                    return self._not_found_message(name, "", automation_id)

                element.click_input()

                if clear_first:
                    element.type_keys("^a{DELETE}", with_spaces=True)

                element.type_keys(text, with_spaces=True)
            else:
                # Type into whatever is currently focused
                import pyautogui
                if clear_first:
                    pyautogui.hotkey("ctrl", "a")
                    pyautogui.press("delete")
                pyautogui.write(text, interval=0.02)

            self.logger.info(f"UI type: '{text[:30]}...' into '{name or automation_id or 'focused element'}'")
            return f"Typed text successfully."

        except Exception as e:
            self.logger.error(f"UI type failed: {e}")
            return f"ERROR: Type failed: {e}"

    def _find_element(self, name: str = "", control_type: str = "",
                      automation_id: str = "", window_title: str = ""):
        """Search the accessibility tree for a matching element.

        Returns the first matching pywinauto wrapper, or None.
        """
        from pywinauto import Desktop

        desktop = Desktop(backend="uia")

        if window_title:
            windows = desktop.windows(title_re=f".*{window_title}.*", visible_only=True)
        else:
            windows = desktop.windows(visible_only=True)

        for win in windows:
            try:
                # Build search criteria
                criteria = {}
                if name:
                    criteria["title_re"] = f".*{name}.*"
                if control_type:
                    criteria["control_type"] = control_type
                if automation_id:
                    criteria["auto_id"] = automation_id

                if not criteria:
                    continue

                found = win.descendants(**criteria)
                if found:
                    # Return the first visible match
                    for elem in found:
                        try:
                            if elem.is_visible():
                                return elem
                        except Exception:
                            continue
            except Exception:
                continue

        return None

    @staticmethod
    def _not_found_message(name: str, control_type: str, automation_id: str) -> str:
        """Build a helpful error message when an element is not found."""
        parts = []
        if name:
            parts.append(f"name='{name}'")
        if control_type:
            parts.append(f"type='{control_type}'")
        if automation_id:
            parts.append(f"id='{automation_id}'")
        criteria = ", ".join(parts)
        return (
            f"ELEMENT NOT FOUND ({criteria}). "
            "Try using ui_snapshot to see available elements, "
            "or use Computer Use (screenshot) as fallback if enabled."
        )


# ═══════════════════════════════════════════════════════════════════
#  COMPUTER USE — Vision-based screen control (Anthropic native)
# ═══════════════════════════════════════════════════════════════════

class ComputerUseHandler:
    """Executes Computer Use actions from the Anthropic API.

    When Claude responds with a 'computer' tool_use block, the input
    contains an 'action' field (screenshot, left_click, type, key, etc.).
    This class translates those actions into real desktop operations
    using pyautogui.

    Handles coordinate scaling automatically: the API constrains images
    to max 1568px on the longest edge, so if the real screen is larger,
    we scale coordinates proportionally.
    """

    def __init__(self, logger: LoggerManager):
        self.logger = logger
        self._scale_factor = None

    def get_display_size(self) -> Tuple[int, int]:
        """Return the real screen resolution."""
        import pyautogui
        return pyautogui.size()

    def get_scaled_size(self) -> Tuple[int, int]:
        """Return the downscaled dimensions for the API.

        The Anthropic API resizes images to max 1568px on the longest
        edge and ~1.15 megapixels total. We pre-scale to match so
        Claude's coordinates align with our execution.
        """
        width, height = self.get_display_size()
        scale = self._compute_scale_factor(width, height)
        return int(width * scale), int(height * scale)

    def _compute_scale_factor(self, width: int, height: int) -> float:
        """Calculate the scale factor matching the API's image constraints."""
        long_edge = max(width, height)
        total_pixels = width * height

        long_edge_scale = 1568 / long_edge
        total_pixels_scale = math.sqrt(1_150_000 / total_pixels)

        return min(1.0, long_edge_scale, total_pixels_scale)

    def execute_action(self, action: str, params: Dict[str, Any]) -> Any:
        """Execute a single Computer Use action.

        Args:
            action: The action type (screenshot, left_click, type, key, etc.)
            params: The full input dict from Claude's tool_use block.

        Returns:
            For screenshot: a dict with type="image" for the API.
            For other actions: a text confirmation string.
        """
        import pyautogui

        real_w, real_h = self.get_display_size()
        scale = self._compute_scale_factor(real_w, real_h)

        self.logger.info(f"Computer Use action: {action}")

        try:
            if action == "screenshot":
                return self._take_screenshot(real_w, real_h, scale)

            elif action == "left_click":
                x, y = self._scale_coords(params.get("coordinate", [0, 0]), scale)
                pyautogui.click(x, y)
                return f"Clicked at ({x}, {y})."

            elif action == "right_click":
                x, y = self._scale_coords(params.get("coordinate", [0, 0]), scale)
                pyautogui.rightClick(x, y)
                return f"Right-clicked at ({x}, {y})."

            elif action == "middle_click":
                x, y = self._scale_coords(params.get("coordinate", [0, 0]), scale)
                pyautogui.middleClick(x, y)
                return f"Middle-clicked at ({x}, {y})."

            elif action == "double_click":
                x, y = self._scale_coords(params.get("coordinate", [0, 0]), scale)
                pyautogui.doubleClick(x, y)
                return f"Double-clicked at ({x}, {y})."

            elif action == "triple_click":
                x, y = self._scale_coords(params.get("coordinate", [0, 0]), scale)
                pyautogui.tripleClick(x, y)
                return f"Triple-clicked at ({x}, {y})."

            elif action == "left_click_drag":
                sx, sy = self._scale_coords(params.get("start_coordinate", [0, 0]), scale)
                ex, ey = self._scale_coords(params.get("coordinate", [0, 0]), scale)
                pyautogui.moveTo(sx, sy)
                pyautogui.drag(ex - sx, ey - sy, duration=0.5)
                return f"Dragged from ({sx},{sy}) to ({ex},{ey})."

            elif action == "mouse_move":
                x, y = self._scale_coords(params.get("coordinate", [0, 0]), scale)
                pyautogui.moveTo(x, y)
                return f"Moved mouse to ({x}, {y})."

            elif action == "type":
                text = params.get("text", "")
                pyautogui.write(text, interval=0.02)
                return f"Typed '{text[:50]}...'." if len(text) > 50 else f"Typed '{text}'."

            elif action == "key":
                key_combo = params.get("text", "")
                # Handle combos like "ctrl+s", "alt+f4"
                keys = key_combo.replace("+", " ").split()
                if len(keys) > 1:
                    pyautogui.hotkey(*keys)
                else:
                    pyautogui.press(keys[0])
                return f"Pressed '{key_combo}'."

            elif action == "scroll":
                x, y = self._scale_coords(params.get("coordinate", [0, 0]), scale)
                direction = params.get("direction", "down")
                amount = params.get("amount", 3)
                scroll_val = amount if direction == "up" else -amount
                if direction in ("left", "right"):
                    pyautogui.hscroll(amount if direction == "right" else -amount, x=x, y=y)
                else:
                    pyautogui.scroll(scroll_val, x=x, y=y)
                return f"Scrolled {direction} by {amount} at ({x},{y})."

            elif action == "wait":
                import time
                duration = params.get("duration", 1)
                time.sleep(duration)
                return f"Waited {duration} second(s)."

            elif action == "hold_key":
                import time
                key = params.get("text", "")
                duration = params.get("duration", 1)
                pyautogui.keyDown(key)
                time.sleep(duration)
                pyautogui.keyUp(key)
                return f"Held '{key}' for {duration}s."

            else:
                return f"Unknown Computer Use action: {action}"

        except Exception as e:
            self.logger.error(f"Computer Use action '{action}' failed: {e}")
            return f"ERROR: {action} failed: {e}"

    def _take_screenshot(self, real_w: int, real_h: int, scale: float) -> Dict:
        """Capture the screen and return it in the API's expected format.

        The screenshot is downscaled to match the API's image constraints
        so that Claude's coordinate output aligns with the real screen
        after we scale them back up.
        """
        import pyautogui
        from PIL import Image

        screenshot = pyautogui.screenshot()

        # Downscale to match API constraints
        scaled_w = int(real_w * scale)
        scaled_h = int(real_h * scale)
        screenshot = screenshot.resize((scaled_w, scaled_h), Image.LANCZOS)

        # Convert to base64 PNG
        buffer = io.BytesIO()
        screenshot.save(buffer, format="PNG")
        img_base64 = base64.b64encode(buffer.getvalue()).decode("utf-8")

        self.logger.info(f"Screenshot captured: {scaled_w}x{scaled_h} (scale={scale:.3f})")

        return {
            "type": "image",
            "media_type": "image/png",
            "data": img_base64
        }

    def _scale_coords(self, coordinate: list, scale: float) -> Tuple[int, int]:
        """Scale Claude's coordinates from the downscaled image back to real screen coords.

        Claude returns coordinates relative to the scaled-down screenshot.
        We need to map them back to the actual screen resolution.
        """
        if not coordinate or len(coordinate) < 2:
            return (0, 0)
        x = int(coordinate[0] / scale)
        y = int(coordinate[1] / scale)
        return (x, y)


# ═══════════════════════════════════════════════════════════════════
#  SCREEN CONTROLLER — Unified interface for agent.py / executor.py
# ═══════════════════════════════════════════════════════════════════

class ScreenController:
    """Unified facade for all screen interaction methods.

    Reads the screen_control config and provides the right tools
    and handlers to the agent and executor.

    Config options (in settings/config.json → screen_control):
        method: "ui_automation"  (always UI Automation, no vision)
        computer_use_fallback: true/false  (enable Computer Use as fallback)
    """

    def __init__(self, config: dict, logger: LoggerManager):
        self.config = config.get("screen_control", {})
        self.logger = logger

        # Initialize subsystems
        self.ui_auto = UIAutomation(logger)
        self.computer_use = ComputerUseHandler(logger)

        # Read config
        self.method = self.config.get("method", "ui_automation")
        self.cu_fallback = self.config.get("computer_use_fallback", False)

        self.logger.info(
            f"Screen control: method={self.method}, "
            f"computer_use_fallback={self.cu_fallback}"
        )

    def is_computer_use_enabled(self) -> bool:
        """Check if Computer Use should be available as a tool."""
        return self.cu_fallback

    def get_computer_use_tool_definition(self) -> Optional[Dict]:
        """Return the Anthropic-native Computer Use tool definition.

        Returns None if Computer Use is disabled in config.
        """
        if not self.cu_fallback:
            return None

        scaled_w, scaled_h = self.computer_use.get_scaled_size()

        return {
            "type": "computer_20250124",
            "name": "computer",
            "display_width_px": scaled_w,
            "display_height_px": scaled_h,
        }

    def get_system_prompt_section(self) -> str:
        """Return a system prompt section explaining the available screen methods.

        This tells Claude which tools to prefer and when to fall back.
        """
        prompt = "SCREEN CONTROL:\n"

        if self.method == "ui_automation":
            prompt += (
                "- You have UI Automation tools (ui_snapshot, ui_click, ui_type) that read the "
                "Windows Accessibility Tree. ALWAYS prefer these — they are fast, precise, and cheap.\n"
                "- Use ui_snapshot first to see available elements, then ui_click or ui_type to interact.\n"
            )

            if self.cu_fallback:
                prompt += (
                    "- You ALSO have the 'computer' tool (Anthropic Computer Use) as a FALLBACK.\n"
                    "- ONLY use 'computer' when ui_snapshot shows no elements for the app, or when "
                    "the element you need is not in the accessibility tree (e.g., custom-drawn UIs, "
                    "games, or canvas-based applications).\n"
                    "- The 'computer' tool uses screenshots and is more expensive. Minimize its use.\n"
                )
            else:
                prompt += (
                    "- Computer Use (vision-based) is DISABLED. If UI Automation cannot find an element, "
                    "explain this to the user and suggest enabling Computer Use fallback in Settings.\n"
                )

        prompt += "\n"
        return prompt
