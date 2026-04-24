"""
config.py – Default configuration for P1 Alert Listener.

All values here are baseline defaults.
Override via .env file or the in-app Settings dialog.
The in-app settings are persisted to data/settings.json.
"""
import os
from pathlib import Path

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
BASE_DIR = Path(__file__).parent.resolve()
ASSETS_DIR = BASE_DIR / "assets"
LOGS_DIR = BASE_DIR / "logs"
DATA_DIR = BASE_DIR / "data"

# ---------------------------------------------------------------------------
# HTTP Listener
# ---------------------------------------------------------------------------
DEFAULT_HOST = os.environ.get("P1_HOST", "127.0.0.1")
DEFAULT_PORT = int(os.environ.get("P1_PORT", "8787"))
MAX_CONTENT_LENGTH = 1 * 1024 * 1024  # 1 MB – protects against oversized payloads

# ---------------------------------------------------------------------------
# Alert behaviour
# ---------------------------------------------------------------------------
DEFAULT_COOLDOWN_SECONDS = int(os.environ.get("P1_COOLDOWN", "60"))
DEFAULT_FLASH_INTERVAL_MS = int(os.environ.get("P1_FLASH_MS", "600"))
DEFAULT_AUTO_OPEN_FULLSCREEN = True   # Open full-screen window automatically
DEFAULT_ALWAYS_ON_TOP = True          # Keep alert window above all others

# ---------------------------------------------------------------------------
# Sound
# ---------------------------------------------------------------------------
# "beep"   – winsound.Beep() loop (stdlib, no install needed)
# "wav"    – winsound.PlaySound() with SND_LOOP|SND_ASYNC (stdlib, Windows only)
# "silent" – no sound (useful for testing / quiet hours)
DEFAULT_SOUND_MODE = os.environ.get("P1_SOUND_MODE", "beep")
DEFAULT_WAV_PATH = os.environ.get(
    "P1_WAV_PATH", str(ASSETS_DIR / "alert.wav")
)

# ---------------------------------------------------------------------------
# Authentication
# ---------------------------------------------------------------------------
DEFAULT_AUTH_ENABLED = os.environ.get("P1_AUTH_ENABLED", "false").lower() == "true"
DEFAULT_SHARED_SECRET = os.environ.get("P1_SHARED_SECRET", "")
AUTH_HEADER = "X-Webhook-Token"  # Header name expected from callers

# ---------------------------------------------------------------------------
# History / persistence
# ---------------------------------------------------------------------------
DEFAULT_MAX_HISTORY = int(os.environ.get("P1_MAX_HISTORY", "100"))
DEFAULT_PERSIST_HISTORY = True
HISTORY_FILE = DATA_DIR / "alert_history.json"
SETTINGS_FILE = DATA_DIR / "settings.json"

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
DEFAULT_LOG_FILE = str(LOGS_DIR / "p1_alert.log")
LOG_MAX_BYTES = 5 * 1024 * 1024  # 5 MB per log file
LOG_BACKUP_COUNT = 3              # Keep 3 rotated files

# ---------------------------------------------------------------------------
# UI colours and labels
# ---------------------------------------------------------------------------
APP_TITLE = "NetWatch"
APP_SUBTITLE = "by Niconomics"

# Full-screen alert colours
ALERT_BG_1 = "#CC0000"   # Bright red  (flash state A)
ALERT_BG_2 = "#1A0000"   # Near-black  (flash state B)
ALERT_FG = "#FFFFFF"      # White text

# Dashboard theme (dark)
DASH_BG = "#1E1E2E"
DASH_FG = "#CDD6F4"
DASH_BTN_BG = "#313244"
DASH_BTN_FG = "#CDD6F4"
DASH_ACCENT = "#89B4FA"  # Blue accent
DASH_LOG_BG = "#11111B"
DASH_LOG_FG = "#A6ADC8"

# Status pill colours
COLOR_OK = "#A6E3A1"    # Green
COLOR_WARN = "#F9E2AF"  # Yellow
COLOR_ERR = "#F38BA8"   # Red
COLOR_MUTED = "#6C7086" # Grey

# ---------------------------------------------------------------------------
# Halo PSA poller
# ---------------------------------------------------------------------------
HALO_BASE_URL = ""
HALO_CLIENT_ID = ""
HALO_CLIENT_SECRET = ""
HALO_POLL_INTERVAL = 120        # seconds between Halo API polls
STATUS_POLL_INTERVAL = 300      # seconds between external status page polls
MONITOR_M365 = True
MONITOR_AWS = True
OUTAGE_DEVICE_KEYWORDS = "firewall,router,gateway,switch,network,offline,unreachable"
