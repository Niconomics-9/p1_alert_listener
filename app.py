"""
app.py – Application orchestrator.

Responsibilities:
  1. Initialise logging
  2. Load persisted settings + history
  3. Build the Tkinter root window
  4. Create AppState
  5. Create Dashboard
  6. Start the listener thread
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
from pathlib import Path

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

    # Load persisted settings and merge over defaults
    saved_settings = storage.load_settings()
    if saved_settings:
        state.settings.update(saved_settings)
        log.info(f"Loaded {len(saved_settings)} settings from disk")

    # Load history
    if state.settings.get("persist_history", True):
        state.history = storage.load_history()

    # Re-init logging with possibly-custom log file from saved settings
    log_file = state.settings.get("log_file", config.DEFAULT_LOG_FILE)
    if log_file != config.DEFAULT_LOG_FILE:
        # Re-configure with the custom path
        logger = logging.getLogger("p1alert")
        logger.handlers.clear()
        utils.setup_logging(log_file)

    # ── 3. Tkinter root ───────────────────────────────────────────────────────
    root = tk.Tk()
    root.withdraw()  # Hide until dashboard is built

    # Set taskbar icon (best-effort – needs .ico on Windows)
    _set_icon(root)

    # ── 4. Dashboard ──────────────────────────────────────────────────────────
    dashboard = Dashboard(root, state)
    # Start minimised – the web board is the main UI.
    # The Tkinter window stays alive for P1 alert popups and sound.
    root.deiconify()
    root.after(50, root.iconify)

    # ── 5. System tray (optional) ─────────────────────────────────────────────
    _try_init_tray(root, state)

    # ── 6. Auto-start listener ────────────────────────────────────────────────
    _start_listener(state)

    # ── 7. Mainloop ───────────────────────────────────────────────────────────
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
    """Open the NOC board in the default browser ~2.5s after startup."""
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


def _try_init_tray(root: tk.Tk, state: AppState) -> None:
    """
    Optional system tray support via pystray.

    pystray is NOT in the default requirements because:
      - It adds a dependency (pystray + Pillow)
      - It requires a separate icon image
      - For a dedicated alert monitor, tray minimisation is less critical

    To enable: pip install pystray pillow
    Then set ENABLE_TRAY = True below.

    Tray menu options: Show Dashboard | Silence | Test Alert | Quit
    """
    ENABLE_TRAY = False  # Change to True after installing pystray + pillow

    if not ENABLE_TRAY:
        return

    try:
        import pystray
        from PIL import Image, ImageDraw

        # Create a simple red square icon
        img = Image.new("RGB", (64, 64), color="#CC0000")
        draw = ImageDraw.Draw(img)
        draw.text((16, 20), "P1", fill="white")

        def _show(icon, item):
            root.after(0, root.deiconify)

        def _silence(icon, item):
            root.after(0, state.silence_active)

        def _test(icon, item):
            state.post("log", "🧪 Tray: test alert")
            # Inject directly since we may not have a running listener
            from listener import _build_test_payload
            from parser import build_alert
            alert = build_alert(_build_test_payload())
            if not state.check_dedupe(alert):
                placement = state.push_alert(alert)
                state.post("alert" if placement == "active" else "queued_alert", alert)

        def _quit(icon, item):
            icon.stop()
            root.after(0, root.destroy)

        menu = pystray.Menu(
            pystray.MenuItem("Show Dashboard", _show, default=True),
            pystray.MenuItem("Silence Current", _silence),
            pystray.MenuItem("Trigger Test Alert", _test),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Quit", _quit),
        )

        icon = pystray.Icon("P1Alert", img, config.APP_TITLE, menu)

        # Minimise to tray
        def _on_minimize(event):
            if root.state() == "iconic":
                root.withdraw()
                icon.notify("NetWatch is running in the tray.")

        root.bind("<Unmap>", _on_minimize)

        import threading
        threading.Thread(target=icon.run, daemon=True, name="TrayThread").start()
        log.info("System tray icon started")

    except ImportError:
        log.debug("pystray not installed – tray disabled (pip install pystray pillow to enable)")
    except Exception as exc:
        log.warning(f"Tray init failed: {exc}")
