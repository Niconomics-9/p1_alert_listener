"""
listener.py – Flask HTTP listener running in a background daemon thread.

Endpoints:
  GET  /health          App status JSON
  POST /webhook         Receive external alert payloads
  POST /test-alert      Inject a simulated P1 alert
  GET  /recent-alerts   Last N alerts from history

Thread safety:
  All state reads/writes go through AppState which has its own lock.
  GUI updates are posted via state.alert_queue (never called directly).

Reverse proxy note:
  To expose this beyond localhost, put nginx/Caddy in front and proxy
  to 127.0.0.1:8787.  You can then keep HOST=127.0.0.1 here and let the
  proxy handle TLS + IP filtering.
"""
from __future__ import annotations

import json
import logging
import threading
import time
from datetime import datetime
from typing import Any

from flask import Flask, Response, jsonify, request

import config
from auth import validate_request
from models import QueueMsg
from parser import build_alert, is_p1_alert
from utils import now_iso

log = logging.getLogger("p1alert.listener")

# Suppress Flask's default access log spam – we do our own logging
flask_log = logging.getLogger("werkzeug")
flask_log.setLevel(logging.ERROR)


# ---------------------------------------------------------------------------
# Flask app factory
# ---------------------------------------------------------------------------

def create_flask_app(app_state) -> Flask:
    """Build and return the Flask application."""
    flask_app = Flask("p1alert_listener")
    flask_app.config["MAX_CONTENT_LENGTH"] = config.MAX_CONTENT_LENGTH

    # ── /health ──────────────────────────────────────────────────────────────

    @flask_app.route("/health", methods=["GET"])
    def health():
        s = app_state.settings
        return jsonify({
            "status": "ok",
            "app": config.APP_TITLE,
            "uptime": app_state.uptime_str(),
            "listener": "running",
            "host": s.get("host", config.DEFAULT_HOST),
            "port": s.get("port", config.DEFAULT_PORT),
            "auth_enabled": s.get("auth_enabled", False),
            "sound_mode": s.get("sound_mode", "beep"),
            "active_alert": bool(app_state.active_alert),
            "queued_alerts": app_state.queue_count(),
            "history_count": len(app_state.history),
            "timestamp": now_iso(),
        })

    # ── /webhook ─────────────────────────────────────────────────────────────

    @flask_app.route("/webhook", methods=["POST"])
    def webhook():
        client_ip = request.remote_addr
        log.info(f"WEBHOOK received from {client_ip}")

        # Auth check
        ok, reason = validate_request(request, app_state.settings)
        if not ok:
            log.warning(f"WEBHOOK auth fail [{reason}] from {client_ip}")
            app_state.post("log", f"⛔ Auth fail: {reason} ({client_ip})")
            return jsonify({"error": "unauthorized", "reason": reason}), 401

        # Parse JSON
        payload = _parse_json(request)
        if payload is None:
            log.warning(f"WEBHOOK bad JSON from {client_ip}")
            app_state.post("log", f"⚠️ Bad JSON from {client_ip}")
            return jsonify({"error": "invalid json"}), 400

        log.info(f"WEBHOOK payload parsed OK – {len(str(payload))} chars")
        app_state.post("log", f"📩 Webhook received from {client_ip}")

        # P1 check
        if not is_p1_alert(payload, app_state.settings):
            log.info("WEBHOOK payload not P1 – ignored")
            app_state.post("log", "ℹ️ Payload received but not P1 – ignored")
            return jsonify({"status": "ignored", "reason": "not p1"}), 200

        log.info("WEBHOOK payload IS P1 – processing alert")
        return _process_alert_payload(payload, app_state)

    # ── /test-alert ───────────────────────────────────────────────────────────

    @flask_app.route("/test-alert", methods=["POST"])
    def test_alert():
        log.info("TEST ALERT triggered via /test-alert endpoint")
        app_state.post("log", "🧪 Test alert triggered via /test-alert")
        payload = _build_test_payload()
        return _process_alert_payload(payload, app_state)

    # ── /recent-alerts ────────────────────────────────────────────────────────

    @flask_app.route("/recent-alerts", methods=["GET"])
    def recent_alerts():
        n = min(int(request.args.get("n", 20)), 100)
        alerts = app_state.get_recent_history(n)
        return jsonify({
            "count": len(alerts),
            "alerts": [a.to_dict() for a in alerts],
        })

    # ── error handlers ────────────────────────────────────────────────────────

    @flask_app.errorhandler(413)
    def too_large(e):
        log.warning("Request entity too large – rejected")
        return jsonify({"error": "payload too large"}), 413

    @flask_app.errorhandler(Exception)
    def handle_exception(e):
        log.error(f"Unhandled Flask exception: {e}", exc_info=True)
        return jsonify({"error": "internal server error"}), 500

    return flask_app


# ---------------------------------------------------------------------------
# Listener thread
# ---------------------------------------------------------------------------

class ListenerThread(threading.Thread):
    """Daemon thread that runs the Flask dev server."""

    def __init__(self, app_state) -> None:
        super().__init__(name="ListenerThread", daemon=True)
        self.app_state = app_state
        self._flask_app: Flask | None = None

    def run(self) -> None:
        s = self.app_state.settings
        host = s.get("host", config.DEFAULT_HOST)
        port = s.get("port", config.DEFAULT_PORT)

        # LAN mode warning
        if s.get("allow_lan") and host == "0.0.0.0":
            log.warning("⚠️  Listener bound to 0.0.0.0 – LAN MODE ACTIVE")
            self.app_state.post("log", "⚠️ LAN mode: listener bound to 0.0.0.0")
        elif host == "0.0.0.0":
            # allow_lan not set but host is 0.0.0.0 – enforce localhost
            host = "127.0.0.1"
            log.info("allow_lan=False – forcing host to 127.0.0.1")

        self._flask_app = create_flask_app(self.app_state)

        log.info(f"Listener starting on {host}:{port}")
        self.app_state.post("log", f"🟢 Listener started on {host}:{port}")
        self.app_state.listener_running = True
        self.app_state.post("listener_status", True)

        try:
            # use_reloader=False is REQUIRED in a thread context
            self._flask_app.run(
                host=host,
                port=port,
                use_reloader=False,
                threaded=True,
            )
        except OSError as exc:
            msg = f"Port {port} already in use or bind failed: {exc}"
            log.error(msg)
            self.app_state.post("log", f"❌ {msg}")
            self.app_state.post("error", msg)
        except Exception as exc:
            log.error(f"Listener crashed: {exc}", exc_info=True)
            self.app_state.post("log", f"❌ Listener crashed: {exc}")
        finally:
            self.app_state.listener_running = False
            self.app_state.post("listener_status", False)
            log.info("Listener thread exited")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _parse_json(req) -> dict | None:
    """Return parsed JSON dict or None on failure."""
    try:
        data = req.get_json(force=True, silent=True)
        if isinstance(data, dict):
            return data
        return None
    except Exception:
        return None


def _process_alert_payload(payload: dict, app_state) -> Response:
    """Build alert, dedupe-check, push to state, notify GUI."""
    alert = build_alert(payload)

    # Dedupe check
    if app_state.check_dedupe(alert):
        log.info(f"DEDUPE SKIP: {alert.dedupe_key} (within cooldown)")
        app_state.post("log", f"🔁 Dedupe skip: {alert.ticket_id} (cooldown active)")
        return jsonify({"status": "dedupe_skip", "key": alert.dedupe_key}), 200

    log.info(f"ALERT ACCEPTED: {alert.ticket_id} / {alert.client}")
    placement = app_state.push_alert(alert)
    log.info(f"Alert placement: {placement}")

    if placement == "active":
        app_state.post("alert", alert)
        app_state.post("log", f"🚨 P1 ALERT: {alert.ticket_id} – {alert.client}")
    else:
        app_state.post("queued_alert", alert)
        app_state.post("log", f"📋 Alert queued: {alert.ticket_id} (queue size: {app_state.queue_count()})")

    return jsonify({
        "status": "accepted",
        "placement": placement,
        "alert_id": alert.id,
        "dedupe_key": alert.dedupe_key,
    }), 200


def _build_test_payload() -> dict:
    """Return a hard-coded P1 test payload."""
    return {
        "ticket_id": "TEST-001",
        "client": "Test Client Ltd",
        "summary": "TEST: Simulated P1 Critical Incident",
        "source": "NetWatch – Test",
        "priority": "P1",
        "severity": "critical",
        "assigned_team": "NOC Team",
        "created_time": datetime.now().isoformat(),
        "_test": True,
    }
