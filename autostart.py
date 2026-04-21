"""
autostart.py – Windows startup registry helper.

Adds or removes the app from HKCU Run so it launches with Windows.
Uses the current Python executable and main.py path.
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

log = logging.getLogger("p1alert.autostart")

_REG_KEY  = r"Software\Microsoft\Windows\CurrentVersion\Run"
_APP_NAME = "P1AlertListener"


def _build_command() -> str:
    exe  = sys.executable
    main = Path(__file__).parent / "main.py"
    return f'"{exe}" "{main}"'


def is_enabled() -> bool:
    """Return True if the autostart entry exists in the registry."""
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _REG_KEY) as key:
            winreg.QueryValueEx(key, _APP_NAME)
            return True
    except FileNotFoundError:
        return False
    except Exception as exc:
        log.warning(f"Autostart check failed: {exc}")
        return False


def enable() -> tuple[bool, str]:
    """Add app to Windows startup. Returns (success, message)."""
    try:
        import winreg
        cmd = _build_command()
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, _REG_KEY, 0, winreg.KEY_SET_VALUE
        ) as key:
            winreg.SetValueEx(key, _APP_NAME, 0, winreg.REG_SZ, cmd)
        log.info(f"Autostart enabled: {cmd}")
        return True, "Added to Windows startup"
    except Exception as exc:
        log.error(f"Autostart enable failed: {exc}")
        return False, str(exc)


def disable() -> tuple[bool, str]:
    """Remove app from Windows startup. Returns (success, message)."""
    try:
        import winreg
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, _REG_KEY, 0, winreg.KEY_SET_VALUE
        ) as key:
            winreg.DeleteValue(key, _APP_NAME)
        log.info("Autostart disabled")
        return True, "Removed from Windows startup"
    except FileNotFoundError:
        return True, "Was not in startup"
    except Exception as exc:
        log.error(f"Autostart disable failed: {exc}")
        return False, str(exc)
