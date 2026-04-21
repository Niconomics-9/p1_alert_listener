"""
storage.py – JSON persistence for alert history and user settings.

Files:
  data/alert_history.json  – list of serialised Alert dicts
  data/settings.json       – user-overridden settings dict
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from models import Alert
import config

log = logging.getLogger("p1alert.storage")

HISTORY_FILE = config.HISTORY_FILE
SETTINGS_FILE = config.SETTINGS_FILE


# ---------------------------------------------------------------------------
# History
# ---------------------------------------------------------------------------

def load_history() -> list[Alert]:
    """Load persisted alert history from disk.  Returns [] on any error."""
    if not HISTORY_FILE.exists():
        return []
    try:
        with open(HISTORY_FILE, encoding="utf-8") as f:
            data = json.load(f)
        alerts = [Alert.from_dict(d) for d in data if isinstance(d, dict)]
        log.info(f"Loaded {len(alerts)} alerts from history file")
        return alerts
    except Exception as exc:
        log.warning(f"Could not load history: {exc}")
        return []


def save_history(history: list[Alert]) -> None:
    """Persist current history list to disk."""
    try:
        HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
        data = [a.to_dict() for a in history]
        with open(HISTORY_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        log.debug(f"Saved {len(data)} alerts to history file")
    except Exception as exc:
        log.warning(f"Could not save history: {exc}")


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------

def load_settings() -> dict[str, Any]:
    """Load user settings from data/settings.json.  Returns {} on any error."""
    if not SETTINGS_FILE.exists():
        return {}
    try:
        with open(SETTINGS_FILE, encoding="utf-8") as f:
            data = json.load(f)
        log.info("Loaded settings from settings.json")
        return data
    except Exception as exc:
        log.warning(f"Could not load settings: {exc}")
        return {}


def save_settings(settings: dict[str, Any]) -> None:
    """Persist settings dict to data/settings.json."""
    try:
        SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
        # Never persist the raw shared secret to disk in plain text
        # if you don't want that.  Currently we DO persist it for usability –
        # this is a local-only tool on a controlled machine.
        with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
            json.dump(settings, f, indent=2, ensure_ascii=False)
        log.info("Settings saved to settings.json")
    except Exception as exc:
        log.warning(f"Could not save settings: {exc}")
