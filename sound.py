"""
sound.py – Sound playback for P1 Alert Listener.

Strategy:
  "wav"    winsound.PlaySound with SND_LOOP|SND_ASYNC (stdlib, Windows).
           Stops cleanly with SND_PURGE. Requires a valid WAV file.
  "beep"   Loop winsound.Beep() in a daemon thread. No file needed.
  "silent" No audio. Visual alert still works normally.

To replace the sound backend later:
  Swap _start_wav() / _start_beep() / _beep_loop().
  The rest of the app calls only start_alert_sound(), stop_alert_sound(),
  and test_sound().
"""
from __future__ import annotations

import logging
import threading
from pathlib import Path

log = logging.getLogger("p1alert.sound")

try:
    import winsound
    _HAS_WINSOUND = True
except ImportError:
    _HAS_WINSOUND = False
    log.warning("winsound not available (non-Windows?) – audio disabled")

_beep_stop_event = threading.Event()
_beep_thread: threading.Thread | None = None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def start_alert_sound(mode: str, wav_path: str = "") -> None:
    """Start repeating alert sound.  Safe to call while already playing."""
    stop_alert_sound()

    if mode == "silent":
        log.debug("Sound mode=silent – no audio")
        return

    if not _HAS_WINSOUND:
        log.warning("winsound not available – no audio")
        return

    if mode == "wav":
        _start_wav(wav_path)
    else:
        _start_beep()


def stop_alert_sound() -> None:
    """Stop any currently playing alert sound."""
    global _beep_thread

    if _HAS_WINSOUND:
        try:
            winsound.PlaySound(None, winsound.SND_PURGE)
        except Exception:
            pass

    _beep_stop_event.set()
    if _beep_thread and _beep_thread.is_alive():
        _beep_thread.join(timeout=2.0)
    _beep_thread = None
    _beep_stop_event.clear()
    log.debug("Alert sound stopped")


def test_sound(mode: str, wav_path: str = "") -> None:
    """Play a one-shot test tone (does not loop)."""
    if not _HAS_WINSOUND:
        log.warning("winsound not available – cannot play test sound")
        return

    if mode == "wav":
        path = Path(wav_path) if wav_path else Path("")
        if path.is_file():
            try:
                winsound.PlaySound(str(path), winsound.SND_FILENAME | winsound.SND_ASYNC)
                log.debug(f"Test WAV: {path}")
                return
            except Exception as exc:
                log.warning(f"WAV test failed ({exc}) – using beep fallback")

    if mode != "silent":
        try:
            winsound.Beep(1000, 350)
            winsound.Beep(800, 350)
        except Exception as exc:
            log.warning(f"Beep test failed: {exc}")


# ---------------------------------------------------------------------------
# Internal
# ---------------------------------------------------------------------------

def _start_wav(wav_path: str) -> None:
    path = Path(wav_path) if wav_path else Path("")
    if path.is_file():
        try:
            winsound.PlaySound(
                str(path),
                winsound.SND_FILENAME | winsound.SND_ASYNC | winsound.SND_LOOP,
            )
            log.info(f"WAV loop started: {path}")
            return
        except Exception as exc:
            log.warning(f"WAV playback failed ({exc}) – falling back to beep")
    else:
        log.warning(f"WAV file not found: {wav_path!r} – falling back to beep")
    _start_beep()


def _start_beep() -> None:
    global _beep_thread
    _beep_stop_event.clear()
    _beep_thread = threading.Thread(target=_beep_loop, daemon=True, name="BeepThread")
    _beep_thread.start()
    log.debug("Beep thread started")


def _beep_loop() -> None:
    """Daemon thread: beep until stop event is set."""
    if not _HAS_WINSOUND:
        return
    while not _beep_stop_event.is_set():
        try:
            winsound.Beep(1000, 300)
            winsound.Beep(800, 200)
        except Exception:
            break
        # Short-sleep loop so we can stop quickly
        for _ in range(5):
            if _beep_stop_event.is_set():
                return
            _beep_stop_event.wait(0.2)
