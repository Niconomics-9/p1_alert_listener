"""
state.py – Centralised, thread-safe application state.

All mutable state lives here.  The listener thread reads settings and
writes to alert_queue.  The GUI thread owns everything else via the queue.
"""
from __future__ import annotations

import queue
import threading
import time
from typing import Any

from models import Alert, QueueMsg
import config


class AppState:
    """Single source of truth for the running application."""

    def __init__(self) -> None:
        # ── Message queue (listener → GUI) ───────────────────────────────
        self.alert_queue: queue.Queue[QueueMsg] = queue.Queue()

        # ── Active / queued / history ────────────────────────────────────
        self._lock = threading.Lock()
        self.active_alert: Alert | None = None    # Currently displayed alert
        self.alert_queue_list: list[Alert] = []   # Pending alerts
        self.history: list[Alert] = []            # Acknowledged alerts

        # ── Dedupe tracking ───────────────────────────────────────────────
        # {dedupe_key: last_triggered_epoch}
        self._dedupe_times: dict[str, float] = {}

        # ── Listener thread reference ────────────────────────────────────
        self.listener_thread: threading.Thread | None = None
        self.listener_running: bool = False

        # ── Sound ─────────────────────────────────────────────────────────
        self.sound_playing: bool = False
        self.sound_silenced: bool = False  # Silenced for current alert session

        # ── Uptime ───────────────────────────────────────────────────────
        self.start_time: float = time.time()

        # ── Runtime settings (loaded from config + overrides) ────────────
        self.settings: dict[str, Any] = self._default_settings()

    # ------------------------------------------------------------------ settings

    @staticmethod
    def _default_settings() -> dict[str, Any]:
        return {
            "host": config.DEFAULT_HOST,
            "port": config.DEFAULT_PORT,
            "allow_lan": False,
            "cooldown_seconds": config.DEFAULT_COOLDOWN_SECONDS,
            "flash_interval_ms": config.DEFAULT_FLASH_INTERVAL_MS,
            "always_on_top": config.DEFAULT_ALWAYS_ON_TOP,
            "auto_open_fullscreen": config.DEFAULT_AUTO_OPEN_FULLSCREEN,
            "sound_mode": config.DEFAULT_SOUND_MODE,
            "wav_path": config.DEFAULT_WAV_PATH,
            "auth_enabled": config.DEFAULT_AUTH_ENABLED,
            "shared_secret": config.DEFAULT_SHARED_SECRET,
            "max_history": config.DEFAULT_MAX_HISTORY,
            "persist_history": config.DEFAULT_PERSIST_HISTORY,
            "log_file": config.DEFAULT_LOG_FILE,
            "theme": "dark",
        }

    # ------------------------------------------------------------------ dedupe

    def check_dedupe(self, alert: Alert) -> bool:
        """
        Return True if this alert should be suppressed (duplicate within cooldown).
        Side-effect: records the key if not suppressed.
        """
        cooldown = self.settings.get("cooldown_seconds", 60)
        now = time.time()
        key = alert.dedupe_key

        with self._lock:
            last = self._dedupe_times.get(key)
            if last is not None and (now - last) < cooldown:
                return True   # suppress
            self._dedupe_times[key] = now
            return False

    # ------------------------------------------------------------------ alert management

    def push_alert(self, alert: Alert) -> str:
        """
        Add a new alert.  Returns 'active' if it became the active alert,
        or 'queued' if another alert is already active.
        """
        with self._lock:
            if self.active_alert is None:
                self.active_alert = alert
                self.sound_silenced = False  # Reset silence for new alert
                return "active"
            else:
                self.alert_queue_list.append(alert)
                return "queued"

    def acknowledge_active(self) -> Alert | None:
        """Move active alert to history, promote next queued."""
        with self._lock:
            if self.active_alert is None:
                return None
            old = self.active_alert
            old.acknowledged = True
            self.history.insert(0, old)
            # Trim history
            max_h = self.settings.get("max_history", 100)
            self.history = self.history[:max_h]
            # Promote next
            if self.alert_queue_list:
                self.active_alert = self.alert_queue_list.pop(0)
                self.sound_silenced = False
            else:
                self.active_alert = None
            return old

    def silence_active(self) -> None:
        """Toggle silence for the current alert session."""
        with self._lock:
            self.sound_silenced = not self.sound_silenced

    def next_queued(self) -> Alert | None:
        """Move active alert to history and show next queued alert."""
        with self._lock:
            if self.active_alert is None:
                return None
            old = self.active_alert
            old.acknowledged = True
            self.history.insert(0, old)
            max_h = self.settings.get("max_history", 100)
            self.history = self.history[:max_h]
            if self.alert_queue_list:
                self.active_alert = self.alert_queue_list.pop(0)
                self.sound_silenced = False
                return self.active_alert
            else:
                self.active_alert = None
                return None

    def queue_count(self) -> int:
        with self._lock:
            return len(self.alert_queue_list)

    def add_to_history(self, alert: Alert) -> None:
        """Directly add an alert to history (e.g. test / non-displayed)."""
        with self._lock:
            self.history.insert(0, alert)
            max_h = self.settings.get("max_history", 100)
            self.history = self.history[:max_h]

    def clear_history(self) -> None:
        with self._lock:
            self.history.clear()

    def get_recent_history(self, n: int = 20) -> list[Alert]:
        with self._lock:
            return list(self.history[:n])

    # ------------------------------------------------------------------ uptime

    def uptime_str(self) -> str:
        elapsed = int(time.time() - self.start_time)
        h, rem = divmod(elapsed, 3600)
        m, s = divmod(rem, 60)
        return f"{h:02d}:{m:02d}:{s:02d}"

    # ------------------------------------------------------------------ queue helpers

    def post(self, kind: str, data: Any = None) -> None:
        """Put a message on the GUI queue."""
        self.alert_queue.put(QueueMsg(kind=kind, data=data))
