"""
app.py – Application orchestrator.

Responsibilities:
  1. Initialise logging
  2. Load persisted settings + history
  3. Build the Tkinter root window
  4. Create AppState
  5. Create Dashboard (starts minimised — web board is the main UI)
  6. Start background threads and open /board in browser
  7. Run the Tkinter mainloop
  8. Graceful shutdown on exit

Tkinter thread safety rule:
  ONLY the main thread touches any Tkinter widget.
  Background threads post QueueMsg objects to state.alert_queue.
  Dashboard polls the queue via root.after().
"""
from __future__ import annotations

import logging
import sys
import tkinter as tk

import config
import storage
import utils
from dashboard import Dashboard
from halo_poller import HaloPollerThread
from listener import ListenerThread
from service_poller import ServicePollerThread
from state import AppState
from status_poller import StatusPollerThread

log = logging.getLogger("p1alert.app")


def run() -> None:
    """Entry point – called from main.py."""

    # ── 1. Logging ────────────────────────────────────────────────────────────
    utils.setup_logging()
    log.info(f"{'=' * 60}")
    log.info(f"Starting {config.APP_TITLE}")
    log.info(f"Python {sys.version.split()[0]} | Base dir: {config.BASE_DIR}")

    # ── 2. State ──────────────────────────────────────────────────────────────
    state = AppState()

    saved_settings = storage.load_settings()
    if saved_settings:
        state.settings.update(saved_settings)
        log.info(f"Loaded {len(saved_settings)} settings from disk")

    if state.settings.get("persist_history", True):
        state.history = storage.load_history()

    # Re-init logging if a custom log path was saved in settings
    log_file = state.settings.get("log_file", config.DEFAULT_LOG_FILE)
    if log_file != config.DEFAULT_LOG_FILE:
        logger = logging.getLogger("p1alert")
        logger.handlers.clear()
        utils.setup_logging(log_file)

    # ── 3. Tkinter root ───────────────────────────────────────────────────────
    root = tk.Tk()
    root.withdraw()

    _set_icon(root)

    # ── 4. Dashboard ──────────────────────────────────────────────────────────
    dashboard = Dashboard(root, state)
    # Deiconify briefly then minimise — web board is the primary UI;
    # Tkinter stays alive for full-screen P1 alert popups and sound.
    root.deiconify()
    root.after(50, root.iconify)

    # ── 5. Start background threads + open board in browser ───────────────────
    _start_listener(state)

    # ── 6. Mainloop ───────────────────────────────────────────────────────────
    log.info("Entering Tkinter mainloop")
    try:
        root.mainloop()
    except KeyboardInterrupt:
        log.info("KeyboardInterrupt received")
    finally:
        _shutdown(state)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _start_listener(state: AppState) -> None:
    t = ListenerThread(state)
    state.listener_thread = t
    t.start()
    log.info("Listener thread started")
    HaloPollerThread(state).start()
    log.info("Halo poller thread started")
    StatusPollerThread(state).start()
    log.info("Status poller thread started")
    ServicePollerThread(state).start()
    log.info("Service poller thread started")
    _open_board_in_browser(state)


def _open_board_in_browser(state: AppState) -> None:
    """Open the NOC board in the default browser ~1.5s after startup."""
    import threading, time, webbrowser

    def _open():
        time.sleep(1.5)
        port = state.settings.get("port", config.DEFAULT_PORT)
        webbrowser.open(f"http://127.0.0.1:{port}/board")
        log.info("NOC board opened in browser")

    threading.Thread(target=_open, daemon=True).start()


def _shutdown(state: AppState) -> None:
    log.info("Shutting down")
    import sound as _sound
    _sound.stop_alert_sound()
    if state.settings.get("persist_history"):
        storage.save_history(state.history)
        storage.save_settings(state.settings)
    log.info("Shutdown complete")


def _set_icon(root: tk.Tk) -> None:
    ico = config.ASSETS_DIR / "alert.ico"
    if ico.exists():
        try:
            root.iconbitmap(str(ico))
        except Exception:
            pass
