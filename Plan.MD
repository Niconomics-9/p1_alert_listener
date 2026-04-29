# Local VM Rebuild Plan with Cloudflared

## Summary

Rebuild `Niconomics-9/p1_alert_listener` on a **Windows 11 VM**, download the private repo from GitHub via **browser ZIP**, and produce a **portable Windows EXE zip**. The VM plan also includes a full **Cloudflare Tunnel (`cloudflared`)** setup using a **named persistent tunnel** so external systems can post to the app’s local `/webhook` endpoint without opening inbound ports.

Target runtime shape:

- app listens on `127.0.0.1:8787`
- `cloudflared` forwards public HTTPS traffic to `http://127.0.0.1:8787`
- webhook vendors post to `https://<stable-hostname>/webhook`
- final artifact is `dist\P1AlertListener-portable.zip`

## Implementation Plan

### 1. Prepare the Windows 11 VM

Install on the VM:

- Python `3.11.x` from [python.org](https://www.python.org/downloads/windows/)
- `cloudflared` from [Cloudflare Tunnel docs](https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/)
- GitHub browser access
- PowerShell

Verify:

```powershell
python --version
pip --version
cloudflared --version
```

Use a stable local workspace:

```powershell
C:\Builds\p1_alert_listener
```

### 2. Download and Extract the Repo

In the VM browser:

1. Sign into GitHub
2. Open [Niconomics-9/p1_alert_listener](https://github.com/Niconomics-9/p1_alert_listener)
3. `Code` -> `Download ZIP`
4. Extract to `C:\Builds\p1_alert_listener`

Confirm the extracted folder contains:

- `main.py`
- `requirements.txt`
- `assets\`
- `payloads\`
- `README.md`

### 3. Set Up the Python Build Environment

From PowerShell:

```powershell
cd C:\Builds\p1_alert_listener
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
pip install pyinstaller
```

Seed optional runtime files:

```powershell
Copy-Item .env.example .env
New-Item -ItemType Directory -Path .\data -Force | Out-Null
Copy-Item settings.json.example .\data\settings.json
```

Default local settings:

- `P1_HOST=127.0.0.1`
- `P1_PORT=8787`
- leave webhook auth disabled until end-to-end tunnel validation works
- keep secrets out of source files

### 4. Smoke Test the App from Source

Run the app first in source mode:

```powershell
.\.venv\Scripts\python.exe main.py
```

Validate:

- UI/dashboard launches
- listener starts on `127.0.0.1:8787`
- `logs\` and `data\` are created locally

From a second PowerShell window:

```powershell
curl http://127.0.0.1:8787/health
curl -X POST http://127.0.0.1:8787/test-alert
curl http://127.0.0.1:8787/recent-alerts
```

Required result:
- `/health` succeeds
- `/test-alert` triggers the alert flow
- the app is stable before tunnel work begins

### 5. Create the Named Cloudflare Tunnel

Use a **named persistent tunnel**, not a quick tunnel.

Cloudflare-side prerequisites:

- a Cloudflare account
- a zone you control
- a hostname reserved for this listener, for example:
  `p1-listener.<your-domain>`

On the VM:

```powershell
cloudflared login
cloudflared tunnel create p1-alert-listener
cloudflared tunnel route dns p1-alert-listener p1-listener.<your-domain>
```

Create the config file at:

```text
%USERPROFILE%\.cloudflared\config.yml
```

Config contents:

```yaml
tunnel: p1-alert-listener
credentials-file: C:\Users\<VM-USER>\.cloudflared\<TUNNEL-UUID>.json

ingress:
  - hostname: p1-listener.<your-domain>
    service: http://127.0.0.1:8787
  - service: http_status:404
```

Important defaults:

- the app stays bound to localhost only
- Cloudflare handles public TLS
- the stable public webhook URL is:
  `https://p1-listener.<your-domain>/webhook`

### 6. Validate Tunnel Reachability

Start the tunnel manually first:

```powershell
cloudflared tunnel run p1-alert-listener
```

Validation checks:

- local app is already running
- `cloudflared` shows successful edge connection
- opening `https://p1-listener.<your-domain>/health` returns app health JSON
- posting to `https://p1-listener.<your-domain>/test-alert` triggers the same alert path

Remote validation command from the VM or another machine:

```powershell
curl https://p1-listener.<your-domain>/health
curl -X POST https://p1-listener.<your-domain>/test-alert
```

Only after this passes should vendor webhook endpoints be pointed at the tunnel hostname.

### 7. Enable Cloudflared on Startup

Install the tunnel as a Windows service on the VM:

```powershell
cloudflared service install
```

Acceptance criteria:

- tunnel reconnects after reboot
- it forwards to the app on `127.0.0.1:8787`
- no inbound firewall rule is required for public access

If service install does not pick up the intended config, the implementation should correct the local `config.yml` path and rerun service install before proceeding.

### 8. Rebuild and Clean Up Locally

Use the VM as the rebuild box:

1. inspect current source behavior
2. make code changes locally as needed
3. re-run source smoke tests after each meaningful change
4. keep the tunnel pointed at the local source app until source mode is stable

Required local acceptance before packaging:

- source app launches cleanly
- tunnel can reach `/health` and `/webhook`
- settings persist in `data\settings.json`
- logs write under `logs\`
- no hardcoded machine-specific paths remain

### 9. Build the Portable Bundle

Preferred build path:

```powershell
.\build_portable.ps1
```

Fallback if needed:

```powershell
python -m PyInstaller --noconfirm P1AlertListener.spec
```

Expected outputs:

- `dist\P1AlertListener-portable\`
- `dist\P1AlertListener-portable.zip`

Portable bundle requirements:

- `P1AlertListener.exe`
- `assets\`
- `payloads\`
- writable `data\`
- writable `logs\`
- no dependency on Python being installed on the destination machine

### 10. Validate the Portable Build with Cloudflared

Extract the zip to a clean folder and run the packaged app, not the source app.

Validation sequence:

1. stop the source-run app
2. launch `P1AlertListener.exe`
3. keep `cloudflared` pointing at `127.0.0.1:8787`
4. re-test:
   - `https://p1-listener.<your-domain>/health`
   - `https://p1-listener.<your-domain>/test-alert`

Required result:
- the tunnel works unchanged against the packaged app
- logs and settings are written next to the packaged executable
- packaged mode behaves the same as source mode for webhook intake

## Public Interfaces / Operational Endpoints

- Source repo: `https://github.com/Niconomics-9/p1_alert_listener`
- Local listener endpoint: `http://127.0.0.1:8787/webhook`
- Tunnel health endpoint: `https://p1-listener.<your-domain>/health`
- Tunnel webhook endpoint: `https://p1-listener.<your-domain>/webhook`
- Final build artifact: `dist\P1AlertListener-portable.zip`

## Test Plan

1. `python --version`, `pip --version`, and `cloudflared --version` succeed
2. `pip install -r requirements.txt` succeeds in the venv
3. source app launches with `python main.py`
4. local `GET /health` succeeds
5. local `POST /test-alert` succeeds
6. named tunnel connects successfully
7. tunneled `GET /health` succeeds
8. tunneled `POST /test-alert` succeeds
9. `.\build_portable.ps1` completes
10. packaged app launches without venv activation
11. tunneled `GET /health` still succeeds against the packaged app
12. tunneled `POST /test-alert` still succeeds against the packaged app
13. app writes runtime state to local `data\` and `logs\`

## Assumptions and Defaults

- VM OS is **Windows 11**
- repo is downloaded via **GitHub browser ZIP**
- Cloudflare Tunnel is part of the local rebuild, not a separate follow-up
- tunnel type is **named persistent tunnel**
- public webhook URL will be stable and DNS-backed
- the app remains bound to `127.0.0.1`; no direct public bind is planned
- webhook vendor configuration will use the Cloudflare hostname, not the VM IP
- the empty repo `Niconomics-9/P1-Alert-Listener` is ignored for this rebuild
