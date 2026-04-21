# P1 Alert Listener

A local Windows desktop application that receives webhook events, detects P1/critical incidents, and displays a full-screen flashing visual alert with audio. Designed to run on a dedicated office monitor or TV PC.

---

## What It Does

| Feature | Detail |
|---|---|
| HTTP webhook listener | Flask server on `127.0.0.1:8787` |
| P1 detection | Configurable matching rules (priority, severity, impact, urgency) |
| Full-screen alert | Flashing red/black overlay, always-on-top |
| Audio alarm | Windows beep loop or WAV file (via `winsound`, stdlib) |
| Deduplication | Cooldown window prevents repeat triggers for the same ticket |
| Alert queue | Multiple P1s queue up; cycle through them without losing any |
| Dashboard GUI | Tkinter window: status, history, controls, live event log |
| In-app settings | All config managed from within the app – no code edits needed |
| History | In-memory + optional JSON persistence |
| Logging | Rotating log file + console |

---

## Architecture Overview

```
main.py          Entry point – loads .env, calls app.run()
app.py           Orchestrator – init, listener start, Tkinter mainloop
config.py        All default values and paths
state.py         Thread-safe AppState (alerts, queue, history, settings)
models.py        Alert dataclass, QueueMsg
listener.py      Flask HTTP server in a daemon thread
parser.py        is_p1_alert() + extract_alert_fields() + build_alert()
auth.py          X-Webhook-Token header validation
dashboard.py     Tkinter main window, queue polling via root.after()
alert_ui.py      Full-screen Toplevel alert window
settings_dialog.py  Modal settings dialog
sound.py         winsound beep/WAV abstraction
storage.py       JSON persistence for history and settings
utils.py         Logging setup, helpers
```

**Thread model:**
- Main thread: Tkinter mainloop + all GUI updates
- ListenerThread (daemon): Flask HTTP server
- BeepThread (daemon, optional): winsound.Beep() loop
- Queue: `queue.Queue[QueueMsg]` from listener → GUI via `root.after(150ms)`

---

## Folder Structure

```
p1_alert_listener/
├── main.py
├── app.py
├── config.py
├── models.py
├── state.py
├── listener.py
├── parser.py
├── auth.py
├── dashboard.py
├── alert_ui.py
├── settings_dialog.py
├── sound.py
├── storage.py
├── utils.py
├── requirements.txt
├── README.md
├── .env.example
├── settings.json.example
├── payloads/
│   ├── p1_sample.json
│   ├── non_p1_sample.json
│   └── halo_psa_example.json
├── assets/
│   └── README_assets.txt   ← put alert.wav and alert.ico here
├── logs/                    ← created at runtime
│   └── p1_alert.log
└── data/                    ← created at runtime
    ├── alert_history.json
    └── settings.json
```

---

## Installation (Windows)

### Prerequisites
- Python 3.11+ (download from python.org, check "Add to PATH")
- Internet access to install pip packages

### Step-by-step

```powershell
# 1. Navigate to the project folder
cd C:\Users\Public\p1_alert_listener

# 2. Create virtual environment
python -m venv .venv

# 3. Activate it
.\.venv\Scripts\Activate.ps1

# 4. Install dependencies
pip install -r requirements.txt

# 5. (Optional) Copy .env.example → .env and edit
Copy-Item .env.example .env
notepad .env

# 6. Run the app
python main.py
```

> **PowerShell execution policy:** If you get an error running Activate.ps1, run:
> `Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy RemoteSigned`

---

## How to Run

```powershell
# With venv active:
python main.py

# Or directly (if Python is on PATH):
C:\Users\Public\p1_alert_listener\.venv\Scripts\python.exe main.py
```

The app opens the dashboard window and automatically starts the HTTP listener.

---

## How to Use the Dashboard

| Button | Action |
|---|---|
| ▶ Start Listener | Start the HTTP server (auto-started on launch) |
| ⏹ Stop Listener | Shows restart instructions |
| 🧪 Test Alert | Fires a simulated P1 alert through the full stack |
| 🖥 Open Alert Screen | Manually open the full-screen alert for the active alert |
| 🔇 Silence Current | Stop the sound for this alert session |
| ✔ Acknowledge Current | Dismiss current alert, promote next queued |
| 🗑 Clear History | Wipe the history list |
| 📋 Copy Last Alert JSON | Clipboard copy of latest alert |
| ⚙ Settings | Open settings dialog |
| 📁 Open Logs Folder | Open the logs directory in Explorer |
| 🔊 Test Sound | Play a one-shot test sound |

**Status pills** (top bar):  
- `Listener` – RUNNING (green) / STOPPED (red)  
- `Sound` – ON / MUTED  
- `Active` – count of active alerts  
- `Queued` – count of queued alerts  

**History list:** Double-click any row to see full alert details.  
**Filter:** Type in the filter box above history to search by any field.

---

## How Alert Mode Works

1. A POST arrives at `/webhook` (or `/test-alert` is called)
2. `is_p1_alert()` checks the payload against matching rules
3. If P1: `build_alert()` extracts fields → `AppState.push_alert()` → queued message
4. Dashboard receives the message on the next queue poll (≤150ms)
5. Full-screen `AlertWindow` opens (if `auto_open_fullscreen=True`)
6. Sound starts in a daemon thread
7. Window flashes between red and dark-red at `flash_interval_ms`
8. User presses **ACKNOWLEDGE** (or Esc) → alert moves to history, sound stops
9. If another alert was queued, it is promoted automatically

### Keyboard shortcuts (full-screen window)
| Key | Action |
|---|---|
| Esc | Acknowledge and close |
| S | Silence / un-silence |
| N | Next queued alert |
| D | Details popup |
| T | Test sound |

---

## How to Test Locally

### Trigger test alert (via app button)
Click **🧪 Test Alert** in the dashboard.

### Via curl
```bash
# Health check
curl http://127.0.0.1:8787/health

# Test alert (simulated P1)
curl -X POST http://127.0.0.1:8787/test-alert

# Real P1 webhook
curl -X POST http://127.0.0.1:8787/webhook \
     -H "Content-Type: application/json" \
     -d @payloads/p1_sample.json

# Non-P1 (will be ignored)
curl -X POST http://127.0.0.1:8787/webhook \
     -H "Content-Type: application/json" \
     -d @payloads/non_p1_sample.json

# With auth token
curl -X POST http://127.0.0.1:8787/webhook \
     -H "Content-Type: application/json" \
     -H "X-Webhook-Token: mysecret" \
     -d @payloads/p1_sample.json

# View recent alerts
curl http://127.0.0.1:8787/recent-alerts
curl "http://127.0.0.1:8787/recent-alerts?n=5"
```

### Via PowerShell Invoke-RestMethod
```powershell
# Health check
Invoke-RestMethod -Uri "http://127.0.0.1:8787/health"

# Test alert
Invoke-RestMethod -Uri "http://127.0.0.1:8787/test-alert" -Method Post

# P1 webhook from file
$payload = Get-Content ".\payloads\p1_sample.json" -Raw
Invoke-RestMethod -Uri "http://127.0.0.1:8787/webhook" `
    -Method Post `
    -ContentType "application/json" `
    -Body $payload

# P1 webhook with inline JSON
$body = @{
    ticket_id    = "INC-999"
    client       = "Test Corp"
    summary      = "Everything is on fire"
    priority     = "P1"
    severity     = "critical"
    assigned_team = "NOC"
} | ConvertTo-Json

Invoke-RestMethod -Uri "http://127.0.0.1:8787/webhook" `
    -Method Post `
    -ContentType "application/json" `
    -Body $body

# With auth token
Invoke-RestMethod -Uri "http://127.0.0.1:8787/webhook" `
    -Method Post `
    -ContentType "application/json" `
    -Headers @{ "X-Webhook-Token" = "mysecret" } `
    -Body $body

# View recent history
Invoke-RestMethod -Uri "http://127.0.0.1:8787/recent-alerts?n=10"
```

---

## Sample JSON Payloads

### Generic P1
```json
{
  "ticket_id": "INC-001",
  "client": "Acme Corp",
  "summary": "Production DB unreachable",
  "priority": "P1",
  "severity": "critical",
  "assigned_team": "NOC"
}
```

### Integer priority
```json
{
  "ticket_id": "INC-002",
  "priority": 1,
  "summary": "Network switch failure"
}
```

### Severity only (no priority field)
```json
{
  "ticket_id": "INC-003",
  "severity": "critical",
  "summary": "Firewall config corruption"
}
```

---

## HaloPSA Payload Adaptation

HaloPSA webhooks use `priority_id` (integer) and `priority_name` (string like "P1 - Critical").
These are handled automatically by the field mappings in `parser.py`:

```python
# In parser.py – extract_alert_fields()
ticket_id = _first("ticket_id", "id", "ref", ...)   # "id" catches HaloPSA ticket number
client    = _first("client", "client_name", ...)      # "client_name" catches HaloPSA
team      = _first("assigned_team", "team", ...)      # "team" catches HaloPSA
created   = _first("created_time", "dateoccurred", ...) # "dateoccurred" catches HaloPSA
```

And in `is_p1_alert()`:
```python
# priority_id == 1 → True
# priority_name contains "P1" → True
```

**No code changes needed** for HaloPSA payloads – they work out of the box.

To add a new field mapping, edit `extract_alert_fields()` in `parser.py`.

---

## How Auth Works

1. Set `auth_enabled = true` in Settings (or `.env`)
2. Set `shared_secret = yoursecretvalue`
3. Callers must include `X-Webhook-Token: yoursecretvalue` header
4. Requests without the token (or wrong token) get HTTP 401 and are logged

**No auth (default):** Any request from localhost can trigger alerts.  
**LAN mode + auth:** Bind to `0.0.0.0`, enable auth, share the secret with trusted callers.

---

## How Deduplication Works

Each alert gets a **dedupe key**:
- If `ticket_id` is known: `ticket:<ticket_id>`  
- Otherwise: `composite:<md5(client|summary)[:12]>`

If the same key arrives within `cooldown_seconds` (default 60s), the second request is logged and ignored. The cooldown resets after it expires, so the ticket can retrigger if it stays open.

This prevents a monitoring system sending repeated webhooks from flooding your screen.

---

## How Settings Persistence Works

1. Defaults in `config.py`
2. Optional `.env` overrides at startup
3. `data/settings.json` loaded after `.env`, overrides both
4. Settings dialog writes to `data/settings.json` on Save

The file is plain JSON – readable and editable by hand.

---

## How Logging Works

Logs go to:
- Console (stdout)
- `logs/p1_alert.log` (rotating, 5MB × 3 files)

Events logged: startup, shutdown, listener start/stop, every webhook received, auth pass/fail, JSON parse, P1 match/ignore, dedupe skip/accept, alert displayed/silenced/acknowledged, settings changed, test alert triggered.

**Change log file location:** Settings → Log file path → Save.  
**Open log folder:** Dashboard → 📁 Open Logs Folder.

---

## How to Package as EXE (PyInstaller)

```powershell
# Install PyInstaller
pip install pyinstaller

# Build one-file EXE
pyinstaller --onefile --windowed --name P1AlertListener `
    --add-data "assets;assets" `
    --add-data "payloads;payloads" `
    --hidden-import=flask `
    --hidden-import=werkzeug `
    --hidden-import=requests `
    main.py

# Output: dist\P1AlertListener.exe
```

### PyInstaller Caveats on Windows

| Issue | Fix |
|---|---|
| `--windowed` hides the console | Remove it during debugging to see errors |
| Flask not found at runtime | Add `--hidden-import=flask --hidden-import=werkzeug` |
| `winsound` not available | It's stdlib – included automatically |
| `data/` and `logs/` not in EXE | They're created at runtime next to the EXE |
| `assets/alert.wav` missing | Add `--add-data "assets;assets"` |
| Antivirus flags EXE | Common for PyInstaller; add exclusion or use `--onedir` instead |
| `--onefile` is slow to start | Use `--onedir` for faster startup on the alert PC |
| `sys._MEIPASS` path issues | `config.py` uses `Path(__file__).parent` – works with both modes |

**Recommended for production alert PC:**
```powershell
pyinstaller --onedir --windowed --name P1AlertListener `
    --add-data "assets;assets" `
    --hidden-import=flask `
    --hidden-import=werkzeug `
    --hidden-import=requests `
    main.py
```

---

## How to Auto-Start on Login

### Option 1: Windows Startup Folder (Recommended for GUI apps)

```powershell
# Open startup folder
Start-Process shell:startup

# Create a shortcut pointing to your launcher .bat:
# Target: C:\Users\Public\p1_alert_listener\start.bat
# Start in: C:\Users\Public\p1_alert_listener\
```

Create `start.bat`:
```bat
@echo off
cd /d "C:\Users\Public\p1_alert_listener"
call .venv\Scripts\activate.bat
start "" pythonw main.py
```

**Use `pythonw`** (not `python`) to suppress the console window.

### Option 2: Task Scheduler

```powershell
$action  = New-ScheduledTaskAction `
    -Execute "C:\Users\Public\p1_alert_listener\.venv\Scripts\pythonw.exe" `
    -Argument "C:\Users\Public\p1_alert_listener\main.py" `
    -WorkingDirectory "C:\Users\Public\p1_alert_listener"

$trigger = New-ScheduledTaskTrigger -AtLogon -User $env:USERNAME

$settings = New-ScheduledTaskSettingsSet `
    -ExecutionTimeLimit (New-TimeSpan -Hours 0) `
    -RestartCount 3 `
    -RestartInterval (New-TimeSpan -Minutes 1)

Register-ScheduledTask `
    -TaskName "P1AlertListener" `
    -Action $action `
    -Trigger $trigger `
    -Settings $settings `
    -RunLevel Highest `
    -Force
```

### Option 3: EXE in Startup Folder
After packaging with PyInstaller, copy `dist\P1AlertListener.exe` to the Startup folder.

### Startup Mode Guidance

| Method | Best for |
|---|---|
| Startup folder (pythonw) | Simple, reliable for GUI apps; runs after desktop loads |
| Task Scheduler | More control (delays, restart on crash, run as different user) |
| Windows Service | Background-only (no GUI); not recommended for a Tkinter app |

**Recommendation:** Use Task Scheduler with `AtLogon` trigger and a 10-second delay to ensure the desktop is fully ready before the GUI launches.

Add a delay:
```powershell
$trigger = New-ScheduledTaskTrigger -AtLogon -User $env:USERNAME
$trigger.Delay = "PT10S"  # 10-second delay
```

---

## Limitations

- **Flask dev server** is used (not production-grade). For local-only use on a controlled machine, this is fine. If you expose this over a network, put nginx/Caddy in front.
- **Listener cannot be stopped** without restarting the app (Flask dev server limitation). The Stop button shows a notice.
- **winsound WAV** requires a standard PCM WAV file. MP3/OGG files are not supported.
- **System tray** is disabled by default. Enable by installing `pystray pillow` and setting `ENABLE_TRAY = True` in `app.py`.
- **Single monitor** assumed for full-screen mode. Multi-monitor positioning can be added.
- **No HTTPS**. Use a reverse proxy (nginx + Let's Encrypt) if you need TLS.

---

## Future Enhancements

- **Webhook signature validation** (HMAC-SHA256) for stronger auth
- **Multi-monitor support** – choose which screen the alert opens on
- **Custom alert templates** – per-source field mapping configs
- **Email / SMS on alert** via SMTP or Twilio
- **REST API for alert management** (acknowledge via API)
- **Alert escalation** – notify again if unacknowledged after N minutes
- **WAV auto-download** – fetch a sound file on first run
- **Dark/light theme toggle** in the UI
- **Prometheus metrics endpoint** for observability
- **Auto-update** – check GitHub for new versions
