"""
gateways/scheduler.py — Automatic task scheduler.

Runs as an independent background process (launched by ariel.py).
Every 30 seconds it checks the task list in settings/tasks.json
and runs any tasks whose scheduled day and time match the current
moment. Each task is executed at most once per minute to prevent
duplicates.

This is a thin client — it does NOT create its own ARIELAgent.
All AI processing happens in the central orchestrator (ariel.py)
via the IPC socket.
"""

import sys
import time
import logging
from datetime import datetime
from pathlib import Path

# Allow importing the 'core' package from the gateways/ subdirectory
BASE_DIR = Path(__file__).parent.parent
sys.path.append(str(BASE_DIR))

from core.ipc import ArielClient
from core.utils import load_json

# Simple logger for the scheduler process
log = logging.getLogger("scheduler")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [SCHED] %(message)s")


def run_scheduler():
    """Main loop: check task schedule every 30 seconds and run matches."""

    # IPC client to communicate with the orchestrator (instead of own agent)
    ipc = ArielClient(BASE_DIR)

    # Wait for the orchestrator to be ready before entering the loop
    for _attempt in range(30):
        if ipc.ping():
            break
        time.sleep(1)

    log.info("Scheduler connected to orchestrator.")

    # Track which tasks have already run in the current minute
    # to avoid executing the same task multiple times
    last_executed = {}

    while True:
        try:
            # Re-read the task list on every cycle so changes from the
            # GUI are picked up without restarting the scheduler
            tasks_path = BASE_DIR / "settings" / "tasks.json"
            tasks_data = load_json(tasks_path)
            tasks = tasks_data.get("tasks", [])

            now = datetime.now()
            current_day = now.weekday()     # 0 = Monday, 6 = Sunday
            current_time = now.strftime("%H:%M")
            current_date = now.strftime("%Y-%m-%d")

            for task in tasks:
                # Skip disabled tasks
                if not task.get("enabled", True):
                    continue

                # Check if the current day and time match the task's schedule
                if current_day in task.get("days", []) and current_time == task.get("time"):
                    task_id = task.get("id")

                    # Build a unique key for this exact execution window
                    execution_key = f"{task_id}_{current_date}_{current_time}"

                    # Only run if we haven't already executed in this minute
                    if last_executed.get(task_id) != execution_key:
                        log.info(f"⏰ Launching scheduled task: {task.get('prompt')}")
                        last_executed[task_id] = execution_key

                        # Run the task via IPC — consume the generator fully.
                        # Output goes to orchestrator logs, not to screen.
                        try:
                            for update in ipc.run_task(task["prompt"], source="scheduler"):
                                pass
                            log.info(f"✅ Task '{task.get('prompt')}' finished.")
                        except Exception as e:
                            log.error(f"Task execution error: {e}")

        except Exception:
            # If there's an error reading the file (e.g., user is editing it
            # in the GUI at this exact moment), silently skip and retry
            pass

        # Sleep for 30 seconds between checks to avoid CPU saturation
        time.sleep(30)


if __name__ == "__main__":
    run_scheduler()
