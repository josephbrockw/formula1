"""
Shared tools for F1 analytics agents.

All tools return plain strings (text or JSON).
"""

import json
import os
import subprocess
import sys
from pathlib import Path

# Project root = f1_analytics/ (one level up from agents/)
PROJECT_ROOT = Path(__file__).parent.parent.resolve()

# Django must be configured before using models
def _ensure_django():
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
    import django
    django.setup()


# ---------------------------------------------------------------------------
# Tool: query_data_summary
# ---------------------------------------------------------------------------

def query_data_summary() -> str:
    """
    Return counts of key tables and the latest snapshot date.
    """
    _ensure_django()
    from analytics.models import (
        Season, Race, Session, SessionResult, Lap,
        DriverRacePerformance, DriverSnapshot,
    )
    from django.db.models import Max

    latest_snapshot = DriverSnapshot.objects.aggregate(
        latest=Max("snapshot_date")
    )["latest"]

    summary = {
        "seasons": Season.objects.count(),
        "races": Race.objects.count(),
        "sessions": Session.objects.count(),
        "session_results": SessionResult.objects.count(),
        "laps": Lap.objects.count(),
        "driver_race_performances": DriverRacePerformance.objects.count(),
        "latest_snapshot_date": str(latest_snapshot) if latest_snapshot else None,
    }
    return json.dumps(summary, indent=2)


QUERY_DATA_SUMMARY_SCHEMA = {
    "name": "query_data_summary",
    "description": (
        "Return counts of key database tables (seasons, races, sessions, "
        "session_results, laps, driver_race_performances) plus the latest "
        "snapshot date. Use this to understand what data is available."
    ),
    "input_schema": {
        "type": "object",
        "properties": {},
        "required": [],
    },
    "fn": lambda: query_data_summary(),
}


# ---------------------------------------------------------------------------
# Tool: read_file
# ---------------------------------------------------------------------------

def read_file(path: str) -> str:
    """
    Read a source file, sandboxed to the project root.
    """
    try:
        full_path = (PROJECT_ROOT / path).resolve()
        if not str(full_path).startswith(str(PROJECT_ROOT)):
            return "Error: access denied — path is outside the project root"
        if not full_path.exists():
            return f"Error: file not found: {path}"
        if not full_path.is_file():
            return f"Error: not a file: {path}"
        content = full_path.read_text(encoding="utf-8", errors="replace")
        # Truncate very large files to avoid filling context
        if len(content) > 16_000:
            content = content[:16_000] + "\n... [truncated]"
        return content
    except Exception as exc:
        return f"Error reading {path}: {exc}"


READ_FILE_SCHEMA = {
    "name": "read_file",
    "description": (
        "Read the contents of a source file relative to the f1_analytics/ project root. "
        "Sandboxed: cannot read files outside the project. "
        "Example path: 'analytics/models/fantasy.py'"
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Relative path from the f1_analytics/ root.",
            }
        },
        "required": ["path"],
    },
    "fn": read_file,
}


# ---------------------------------------------------------------------------
# Tool: run_management_command
# ---------------------------------------------------------------------------

ALLOWLISTED_COMMANDS = {
    "import_fastf1",
    "optimize_lineup",
    "train_model",
    "predict_race",
    "advise_lineup",
    "import_schedule",
    "check_driver_integrity",
}


def run_management_command(cmd: str) -> str:
    """
    Run an allowlisted Django management command and return stdout + stderr.

    cmd should be just the command name (e.g. 'optimize_lineup'), without
    'python manage.py'. Additional flags may be appended separated by spaces.
    """
    parts = cmd.strip().split()
    command_name = parts[0]
    extra_args = parts[1:]

    if command_name not in ALLOWLISTED_COMMANDS:
        return (
            f"Error: '{command_name}' is not allowlisted. "
            f"Allowlisted commands: {sorted(ALLOWLISTED_COMMANDS)}"
        )

    manage_py = PROJECT_ROOT / "manage.py"
    full_cmd = [sys.executable, str(manage_py), command_name] + extra_args

    try:
        result = subprocess.run(
            full_cmd,
            capture_output=True,
            text=True,
            timeout=120,
            cwd=str(PROJECT_ROOT),
        )
        output = result.stdout
        if result.stderr:
            output += "\n[stderr]\n" + result.stderr
        if len(output) > 8000:
            output = output[:8000] + "\n... [truncated]"
        return output
    except subprocess.TimeoutExpired:
        return f"Error: command '{cmd}' timed out after 120 seconds"
    except Exception as exc:
        return f"Error running '{cmd}': {exc}"


RUN_MANAGEMENT_COMMAND_SCHEMA = {
    "name": "run_management_command",
    "description": (
        "Run an allowlisted Django management command and return its output. "
        "cmd is the command name plus optional flags (e.g. 'optimize_lineup', "
        "'advise_lineup --year 2025 --round 5'). "
        f"Allowlisted: {sorted(ALLOWLISTED_COMMANDS)}"
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "cmd": {
                "type": "string",
                "description": (
                    "Management command name and optional flags, "
                    "e.g. 'optimize_lineup' or 'advise_lineup --year 2025'"
                ),
            }
        },
        "required": ["cmd"],
    },
    "fn": run_management_command,
}


# ---------------------------------------------------------------------------
# Exported tool list
# ---------------------------------------------------------------------------

ALL_TOOLS = [
    QUERY_DATA_SUMMARY_SCHEMA,
    READ_FILE_SCHEMA,
    RUN_MANAGEMENT_COMMAND_SCHEMA,
]
