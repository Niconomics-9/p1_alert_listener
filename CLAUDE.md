# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This App Does

Windows desktop app that receives webhook POSTs from Halo PSA and Datto RMM, detects P1/critical incidents, and fires a full-screen flashing red alert with audio. Runs on a dedicated office monitor PC. No web frontend — pure Tkinter GUI + Flask backend in the same process.

## Running the App

```powershell
cd C:\Users\Public\p1_alert_listener
.\.venv\Scripts\Activate.ps1
python main.py
```

## Testing Webhooks Locally

```bash
# Health check
curl http://127.0.0.1:8787/health

# Simulate P1 (bypasses parser, always triggers alert)
curl -X POST http://127.0.0.1:8787/test-alert

# Real payload (must pass is_p1_alert() check)
curl -X POST http://127.0.0.1:8787/webhook \
     -H "Content-Type: application/json" \
     -d @payloads/p1_sample.json

# With auth token
curl -X POST http://127.0.0.1:8787/webhook \
     -H "Content-Type: application/json" \
     -H "X-Webhook-Token: mysecret" \
     -d @payloads/p1_sample.json
```

There are no automated tests. Validate changes by hitting the endpoints above and watching the dashboard log panel.

## Architecture

### Thread Model
Two threads only. They communicate exclusively via `state.alert_queue` (a `queue.Queue[QueueMsg]`):

- **Main thread** — Tkinter mainloop. Owns all widgets. Polls the queue every 150ms via `root.after()` in `Dashboard._poll_queue()`. The only thread that may touch any widget.
- **ListenerThread** (daemon) — Flask HTTP server. Reads `AppState.settings`, writes to `alert_queue`. Never touches widgets.
- **BeepThread** (daemon, optional) — winsound loop, controlled by `_beep_stop_event`.

### Alert Lifecycle
```
POST /webhook
  → auth.validate_request()
  → parser.is_p1_alert(payload, settings)   ← per-source rules from settings["source_rules"]
  → parser.build_alert(payload)
  → AppState.check_dedupe()                  ← cooldown window by dedupe_key
  → AppState.push_alert()                    ← sets active_alert or appends alert_queue_list
  → state.alert_queue.put(QueueMsg)
  → Dashboard._poll_queue() picks it up
  → AlertWindow opens + sound.start_alert_sound()
```

### Settings Layering
1. Hardcoded defaults in `config.py`
2. `.env` file overrides at startup (via python-dotenv)
3. `data/settings.json` loaded at startup, overrides both
4. Settings dialog writes `data/settings.json` on Save

`AppState.settings` is the single runtime dict. All code reads from there.

### Per-Source P1 Detection
`parser.detect_source(payload)` fingerprints payloads by key presence:
- Datto RMM: keys like `alertUid`, `alertTypeId`, `siteName`
- Halo PSA: keys like `client_name`, `priority_name`, `ref`
- Generic: fallback

`parser.is_p1_alert(payload, settings)` applies the matching source's rules from `settings["source_rules"]`. Default rules: Halo triggers on `P1` only; Datto triggers on `Critical` only. Configurable in the Settings dialog without code changes.

### Adding a New Webhook Source
1. Add fingerprint keys to `_DATTO_FINGERPRINTS` / `_HALO_FINGERPRINTS` in `parser.py` or add a new detection branch in `detect_source()`
2. Add default rules for the new source in `default_source_rules()`
3. Add field aliases in `extract_alert_fields()` — the `_first()` helper tries keys in order

### Adding a New Outbound Integration
Subclass `integrations/base.py:BaseIntegration`, implement `test_connection()` and `on_p1_alert()`. The stub and base class are ready — see the `# TODO` comment in `settings_dialog.py` for where to wire in the UI.

## Key Files and What to Edit

| Goal | File |
|---|---|
| Change P1 detection logic | `parser.py` → `is_p1_alert()`, `default_source_rules()` |
| Add new payload field mappings | `parser.py` → `extract_alert_fields()` |
| Change what's stored per alert | `models.py` → `Alert` dataclass |
| Add a new settings field | `state.py` → `_default_settings()`, `settings_dialog.py` → `_build()` and `_do_save()` |
| Change auth behaviour | `auth.py` → `validate_request()` |
| Add a new HTTP endpoint | `listener.py` → `create_flask_app()` |
| Change alert window appearance | `alert_ui.py` |
| Change dashboard layout | `dashboard.py` |
| Change sound behaviour | `sound.py` |
| Windows autostart registry | `autostart.py` |

## Settings Stored in data/settings.json

Notable non-obvious keys:
- `source_rules` — nested dict of per-source P1 trigger rules; edited via Settings dialog
- `ip_allowlist_enabled` / `allowed_ips` — IP allowlist for webhook auth layer
- `integrations` — reserved for future outbound integrations (Halo write-back etc.)
- `allow_lan` — when True, listener binds to `0.0.0.0` instead of `127.0.0.1`

## Auth Layers

Two independent layers, both in `auth.py`:
1. **IP allowlist** — Cloudflare edge IP ranges are built in; extra vendor IPs added in Settings. Checks `X-Forwarded-For` (Cloudflare tunnel sets this to real sender IP).
2. **Shared secret** — `X-Webhook-Token` header; uses `hmac.compare_digest` (timing-safe).

## External Connectivity

The app listens on localhost. External sources (Halo PSA cloud, Datto RMM cloud) reach it via a Cloudflare Tunnel (`cloudflared`), which makes an outbound connection — no inbound ports needed. Both Halo and Datto POST to the same `/webhook` endpoint; source is auto-detected from payload shape.

## Sound Limitation

`winsound.SND_LOOP` (WAV mode) requires standard 16-bit PCM WAV. Compressed or 32-bit float WAV silently falls back to beep. If WAV isn't playing, check the log panel — it will say `WAV file not found` or `WAV playback failed`.

## Dependencies

`flask`, `requests`, `python-dotenv` — all stdlib otherwise (`tkinter`, `winsound`, `winreg`, `queue`, `threading`). No test framework installed.
