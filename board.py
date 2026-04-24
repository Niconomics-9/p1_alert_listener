"""
board.py – NOC Status Board Flask blueprint.

Routes:
  GET  /board                         Full-page HTML NOC status board (main UI)
  GET  /api/board-data                JSON snapshot (services, ISPs, tickets, app state)
  POST /api/board/isp-toggle          Toggle manual outage flag for a named ISP
  POST /api/action/test-alert         Fire a test P1 alert
  POST /api/action/acknowledge        Acknowledge the active alert
  POST /api/action/silence            Toggle silence for the current alert
  POST /api/action/open-alert         Open the native Tkinter alert window
  POST /api/action/restart-listener   Restart the Flask listener thread
  POST /api/action/clear-history      Clear alert history
  POST /api/action/test-sound         Play a test sound
"""
from __future__ import annotations

import logging
import threading
import time
import uuid
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from flask import Blueprint, Response, jsonify, request

if TYPE_CHECKING:
    from state import AppState

log = logging.getLogger("p1alert.board")


# ---------------------------------------------------------------------------
# Blueprint factory
# ---------------------------------------------------------------------------

def create_board_blueprint(app_state: "AppState") -> Blueprint:
    bp = Blueprint("board", __name__)

    # ── Main page ─────────────────────────────────────────────────────────
    @bp.route("/board")
    def board():
        return Response(_BOARD_HTML, mimetype="text/html; charset=utf-8")

    # ── Board data API ────────────────────────────────────────────────────
    @bp.route("/api/board-data")
    def board_data():
        return jsonify(_build_board_data(app_state))

    # ── ISP manual outage toggle ──────────────────────────────────────────
    @bp.route("/api/board/isp-toggle", methods=["POST"])
    def isp_toggle():
        body = request.get_json(silent=True) or {}
        name = body.get("isp", "").strip()
        if not name:
            return jsonify({"error": "isp name required"}), 400
        with app_state._lock:
            isp = dict(app_state.isp_status.get(name, {}))
            isp["manual_outage"] = not isp.get("manual_outage", False)
            app_state.isp_status[name] = isp
        manual = app_state.isp_status[name]["manual_outage"]
        log.info("ISP manual toggle: %s -> %s", name, manual)
        return jsonify({"isp": name, "manual_outage": manual})

    # ── Alert actions ─────────────────────────────────────────────────────
    @bp.route("/api/action/test-alert", methods=["POST"])
    def action_test_alert():
        from listener import _build_test_payload
        from parser import build_alert
        payload = _build_test_payload()
        alert = build_alert(payload)
        alert.dedupe_key = f"test:{uuid.uuid4().hex[:8]}"
        placement = app_state.push_alert(alert)
        app_state.post("alert" if placement == "active" else "queued_alert", alert)
        app_state.post("log", f"🧪 Test alert via board ({placement})")
        return jsonify({"status": "ok", "message": f"Test alert fired ({placement})"})

    @bp.route("/api/action/acknowledge", methods=["POST"])
    def action_acknowledge():
        import sound as _sound
        old = app_state.acknowledge_active()
        if old is None:
            return jsonify({"status": "no_active_alert", "message": "No active alert"})
        _sound.stop_alert_sound()
        app_state.post("log", f"✔ Acknowledged via board: {old.ticket_id}")
        if app_state.active_alert:
            s = app_state.settings
            _sound.start_alert_sound(s.get("sound_mode", "beep"), s.get("wav_path", ""))
        return jsonify({"status": "ok", "message": f"Acknowledged {old.ticket_id}"})

    @bp.route("/api/action/silence", methods=["POST"])
    def action_silence():
        import sound as _sound
        app_state.silence_active()
        if app_state.sound_silenced:
            _sound.stop_alert_sound()
            app_state.post("log", "🔇 Silenced via board")
            return jsonify({"status": "ok", "message": "Silenced", "silenced": True})
        else:
            if app_state.active_alert:
                s = app_state.settings
                _sound.start_alert_sound(s.get("sound_mode", "beep"), s.get("wav_path", ""))
            app_state.post("log", "🔊 Un-silenced via board")
            return jsonify({"status": "ok", "message": "Un-silenced", "silenced": False})

    @bp.route("/api/action/open-alert", methods=["POST"])
    def action_open_alert():
        app_state.post("open_alert_request", None)
        return jsonify({"status": "ok", "message": "Alert window requested"})

    # ── Listener restart (posts to Tkinter queue so main thread handles it) ──
    @bp.route("/api/action/restart-listener", methods=["POST"])
    def action_restart_listener():
        app_state.post("restart_listener", None)
        app_state.post("log", "↺ Listener restart requested via board")
        return jsonify({"status": "ok", "message": "Restart requested"})

    # ── History ───────────────────────────────────────────────────────────
    @bp.route("/api/action/clear-history", methods=["POST"])
    def action_clear_history():
        app_state.clear_history()
        app_state.post("log", "🗑 History cleared via board")
        return jsonify({"status": "ok", "message": "History cleared"})

    # ── Sound ─────────────────────────────────────────────────────────────
    @bp.route("/api/action/test-sound", methods=["POST"])
    def action_test_sound():
        import sound as _sound
        s = app_state.settings
        threading.Thread(
            target=_sound.test_sound,
            args=(s.get("sound_mode", "beep"), s.get("wav_path", "")),
            daemon=True,
        ).start()
        return jsonify({"status": "ok", "message": "Playing test sound"})

    return bp


# ---------------------------------------------------------------------------
# Board data builder
# ---------------------------------------------------------------------------

def _build_board_data(app_state: "AppState") -> dict:
    now_str = datetime.now(timezone.utc).isoformat()

    services = [
        {
            "name": name,
            "cat": d.get("cat", "Other"),
            "status": d.get("status", "unknown"),
            "description": d.get("description", ""),
            "page": d.get("page", ""),
            "method": d.get("method", "statuspage"),
            "updated_at": d.get("updated_at", now_str),
        }
        for name, d in app_state.service_status.items()
    ]

    isps = []
    for name, d in app_state.isp_status.items():
        probe = d.get("probe_status", "unknown")
        manual = d.get("manual_outage", False)
        combined = "outage" if manual else probe
        isps.append({
            "name": name,
            "color": d.get("color", "#6C7086"),
            "status": combined,
            "probe_status": probe,
            "latency_ms": d.get("latency_ms"),
            "manual_outage": manual,
            "status_url": d.get("status_url", ""),
            "lat": d.get("lat", 40.6),
            "lng": d.get("lng", -75.5),
            "updated_at": d.get("updated_at", now_str),
        })

    active_alert = None
    with app_state._lock:
        if app_state.active_alert:
            a = app_state.active_alert
            active_alert = {
                "ticket_id": a.ticket_id, "client": a.client,
                "summary": a.short_summary(80), "priority": a.priority,
                "source": a.source, "received": a.display_received(),
            }

    return {
        "services": services,
        "isps": isps,
        "tickets": _get_critical_tickets(app_state),
        "active_alert": active_alert,
        "queue_count": app_state.queue_count(),
        "listener_running": app_state.listener_running,
        "sound_silenced": app_state.sound_silenced,
        "last_updated": now_str,
    }


def _get_critical_tickets(app_state: "AppState") -> list:
    P1 = {"p1", "critical", "1"}
    tickets = []

    with app_state._lock:
        if app_state.active_alert:
            a = app_state.active_alert
            tickets.append({
                "ticket_id": a.ticket_id, "client": a.client,
                "summary": a.short_summary(80), "priority": a.priority,
                "source": a.source, "received": a.display_received(),
                "type": "webhook", "sla": None, "sla_overdue": False,
            })
        for a in app_state.alert_queue_list:
            tickets.append({
                "ticket_id": a.ticket_id, "client": a.client,
                "summary": a.short_summary(80), "priority": a.priority,
                "source": a.source, "received": a.display_received(),
                "type": "webhook", "sla": None, "sla_overdue": False,
            })

    for r in app_state.ticket_queue:
        pri = (r.priority or "").strip().lower()
        if not any(kw in pri for kw in P1):
            continue
        sla_str = None
        sla_overdue = False
        if r.sla_remaining_minutes is not None:
            sla_overdue = r.sla_remaining_minutes < 0
            h, m = divmod(abs(r.sla_remaining_minutes), 60)
            sla_str = f"{'-' if sla_overdue else ''}{h}h{m:02d}m"
        tickets.append({
            "ticket_id": str(r.ticket_id), "client": r.client,
            "summary": (r.subject or "")[:80], "priority": r.priority,
            "source": "Halo", "received": "", "type": "halo",
            "sla": sla_str, "sla_overdue": sla_overdue,
        })

    return tickets


# ---------------------------------------------------------------------------
# HTML template
# ---------------------------------------------------------------------------

_BOARD_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>NOC Status Board – NetWatch</title>
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css">
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<style>
:root{
  --bg:#11111B; --surface:#1E1E2E; --card:#242436; --border:#313244;
  --text:#CDD6F4; --muted:#6C7086; --deep:#0D0D1A;
  --green:#A6E3A1; --amber:#F9E2AF; --red:#F38BA8; --blue:#89B4FA;
  --menu-bg:#181825; --menu-btn:#1E1E30;
  --font:'Segoe UI',system-ui,sans-serif;
}
*{box-sizing:border-box;margin:0;padding:0}
html,body{height:100%;overflow:hidden}
body{background:var(--bg);color:var(--text);font-family:var(--font);
     display:flex;flex-direction:column}

/* ── Header ──────────────────────────────────────────────────── */
header{
  background:var(--deep);border-bottom:2px solid var(--blue);
  height:50px;flex-shrink:0;padding:0 14px;
  display:flex;align-items:center;gap:10px;z-index:10;position:relative
}
#ham{
  background:none;border:none;color:var(--blue);font-size:1.2rem;
  cursor:pointer;padding:4px 8px;border-radius:4px;line-height:1;
  transition:background .15s;flex-shrink:0
}
#ham:hover{background:rgba(137,180,250,.15)}
.brand{font-size:1rem;font-weight:700;color:var(--blue);
       letter-spacing:.06em;white-space:nowrap;flex-shrink:0}
.pills{display:flex;gap:5px;align-items:center;flex-shrink:0}
.pill{
  font-size:.65rem;border-radius:10px;padding:2px 7px;
  display:inline-flex;align-items:center;gap:3px
}
.plbl{color:rgba(255,255,255,.45);font-weight:400}
.pval{font-weight:700;color:#11111B;background:var(--muted);
      padding:1px 5px;border-radius:8px}
.pval-green{background:var(--green)}
.pval-red{background:var(--red)}
.pval-amber{background:var(--amber)}
.pval-gray{background:var(--muted)}
.spacer{flex:1}
.legend{display:flex;gap:8px;align-items:center}
.leg{display:flex;align-items:center;gap:3px;font-size:.63rem;color:var(--muted)}
.leg-dot{width:7px;height:7px;border-radius:50%}
#last-updated{font-size:.68rem;color:var(--muted);white-space:nowrap}
.refresh-btn{
  background:none;border:1px solid var(--blue);color:var(--blue);
  border-radius:4px;padding:3px 9px;font-size:.72rem;cursor:pointer;
  transition:background .15s;white-space:nowrap
}
.refresh-btn:hover{background:rgba(137,180,250,.15)}

/* ── Active alert banner ─────────────────────────────────────── */
#alert-banner{
  background:linear-gradient(90deg,#8B0000,#5C0000);
  border-bottom:2px solid #FF4444;
  padding:8px 16px;flex-shrink:0;
  display:flex;align-items:center;justify-content:space-between;gap:12px;
  animation:abflash 1.5s ease-in-out infinite
}
#alert-banner.hidden{display:none!important}
@keyframes abflash{0%,100%{opacity:1}50%{opacity:.78}}
.ab-info{display:flex;align-items:center;gap:10px;min-width:0;flex:1}
.ab-tag{
  background:#FF4444;color:#fff;font-size:.7rem;font-weight:700;
  padding:2px 8px;border-radius:3px;white-space:nowrap;flex-shrink:0
}
.ab-id{font-family:Consolas,monospace;font-size:.78rem;
       color:var(--blue);font-weight:700;flex-shrink:0}
.ab-client{font-weight:700;font-size:.82rem;flex-shrink:0}
.ab-summary{font-size:.75rem;color:rgba(255,255,255,.75);
            overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.ab-actions{display:flex;align-items:center;gap:6px;flex-shrink:0}
.ab-time{font-size:.67rem;color:rgba(255,255,255,.55);white-space:nowrap}
.ab-btn{
  border:none;border-radius:4px;padding:4px 12px;font-size:.72rem;
  cursor:pointer;font-weight:600;transition:opacity .15s
}
.ab-btn:hover{opacity:.85}
.ab-btn-ack{background:var(--green);color:#11111B}
.ab-btn-sil{background:rgba(255,255,255,.2);color:#fff}

/* ── Sidebar overlay ─────────────────────────────────────────── */
#overlay{
  display:none;position:fixed;inset:0;
  background:rgba(0,0,0,.55);z-index:100
}
#overlay.open{display:block}

/* ── Sidebar nav ─────────────────────────────────────────────── */
#sidebar{
  position:fixed;left:0;top:0;bottom:0;width:220px;
  background:var(--menu-bg);z-index:101;
  transform:translateX(-100%);transition:transform .22s ease;
  display:flex;flex-direction:column;overflow-y:auto
}
#sidebar.open{transform:translateX(0)}
.menu-top-bar{
  background:var(--blue);height:3px;flex-shrink:0
}
.menu-hdr{
  display:flex;align-items:center;justify-content:space-between;
  padding:14px 14px 10px;flex-shrink:0
}
.menu-hdr-title{
  font-size:.67rem;font-weight:700;color:var(--muted);letter-spacing:.1em
}
.menu-close{
  background:none;border:none;color:var(--muted);font-size:.95rem;
  cursor:pointer;padding:2px 6px;border-radius:3px;transition:color .15s
}
.menu-close:hover{color:var(--text)}
.msec{
  font-size:.62rem;font-weight:700;color:var(--muted);letter-spacing:.1em;
  padding:10px 14px 4px;text-transform:uppercase
}
.mdiv{height:1px;background:#2A2A40;margin:8px 10px}
.mbtn{
  display:block;width:calc(100% - 20px);margin:2px 10px;
  background:var(--menu-btn);color:var(--text);border:none;
  text-align:left;padding:8px 14px;font-size:.88rem;border-radius:4px;
  cursor:pointer;transition:background .15s;font-family:var(--font)
}
.mbtn:hover{background:#282840}
.mbtn-primary{background:#1A3060;color:var(--blue)}
.mbtn-primary:hover{background:#1E3870}
.mbtn-success{background:#1A3020;color:var(--green)}
.mbtn-success:hover{background:#1E3825}
.mbtn-danger{background:#3A1520;color:var(--red)}
.mbtn-danger:hover{background:#441820}

/* ── Toast ───────────────────────────────────────────────────── */
#toast{
  position:fixed;bottom:18px;right:18px;z-index:200;
  background:var(--card);border:1px solid var(--border);
  border-left:3px solid var(--green);border-radius:5px;
  padding:9px 15px;font-size:.78rem;max-width:280px;
  opacity:0;transform:translateY(8px);
  transition:opacity .18s ease,transform .18s ease;pointer-events:none
}
#toast.show{opacity:1;transform:translateY(0)}
#toast.toast-err{border-left-color:var(--red)}
#toast.toast-warn{border-left-color:var(--amber)}

/* ── Main grid ───────────────────────────────────────────────── */
main{
  flex:1;min-height:0;
  display:grid;
  grid-template-rows:auto 1fr;
  grid-template-columns:1fr 340px;
  gap:8px;padding:8px
}
#svc-section{grid-column:1/-1}

/* ── Cards ───────────────────────────────────────────────────── */
.card{background:var(--card);border-radius:6px;overflow:hidden;
      display:flex;flex-direction:column}
.card-hdr{
  background:var(--surface);border-bottom:1px solid var(--border);
  padding:6px 12px;flex-shrink:0;
  display:flex;align-items:center;justify-content:space-between;
  font-size:.67rem;font-weight:700;color:var(--muted);letter-spacing:.1em
}
.card-body{flex:1;min-height:0;overflow:auto;padding:8px}

/* ── Service grid ────────────────────────────────────────────── */
#svc-grid{display:flex;flex-direction:column;gap:4px}
.cat-row{display:flex;align-items:center;gap:6px;flex-wrap:wrap;padding:2px 0}
.cat-lbl{
  font-size:.62rem;font-weight:700;color:var(--muted);letter-spacing:.08em;
  text-transform:uppercase;min-width:82px;flex-shrink:0
}
.chip{
  display:inline-flex;align-items:center;gap:5px;
  padding:2px 9px;border-radius:12px;font-size:.73rem;
  background:var(--surface);border:1px solid var(--border);
  color:var(--text);text-decoration:none;cursor:pointer;
  transition:background .15s
}
.chip:hover{background:var(--border)}
.dot{width:8px;height:8px;border-radius:50%;flex-shrink:0}
.s-operational{background:var(--green)}
.s-degraded{background:var(--amber)}
.s-outage{background:var(--red);animation:pulse 1.4s ease-in-out infinite}
.s-unknown{background:var(--muted)}
@keyframes pulse{0%,100%{opacity:1}50%{opacity:.35}}

/* ── ISP section ─────────────────────────────────────────────── */
#map-section{display:flex;flex-direction:column;overflow:hidden}
#map{flex:1;min-height:0}
.isp-bar{
  flex-shrink:0;padding:6px 8px;
  display:flex;flex-wrap:wrap;gap:5px;border-top:1px solid var(--border)
}
.isp-btn{
  display:inline-flex;align-items:center;gap:5px;padding:4px 10px;
  border-radius:4px;font-size:.7rem;cursor:pointer;
  border:1px solid var(--border);background:var(--surface);color:var(--text);
  transition:all .15s
}
.isp-btn:hover{background:var(--border)}
.isp-btn.confirmed{border-color:var(--red);background:rgba(243,139,168,.18)}
.isp-dot{width:9px;height:9px;border-radius:50%;flex-shrink:0}
.isp-probe{font-size:.62rem;color:var(--muted)}
.isp-flag{font-size:.62rem;color:var(--red);font-weight:700}

/* ── Ticket queue ────────────────────────────────────────────── */
#tix-section{display:flex;flex-direction:column;overflow:hidden}
#tix-list{flex:1;min-height:0;overflow-y:auto}
.trow{
  padding:7px 10px;border-bottom:1px solid var(--border);
  display:grid;grid-template-columns:auto 1fr auto;
  align-items:start;column-gap:7px;row-gap:2px;font-size:.76rem
}
.trow:last-child{border-bottom:none}
.trow:hover{background:var(--surface)}
.tid{font-family:Consolas,monospace;font-size:.7rem;
     color:var(--blue);font-weight:700}
.tclient{font-weight:600;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.pbadge{
  font-size:.63rem;font-weight:700;padding:1px 7px;border-radius:10px;
  text-transform:uppercase;white-space:nowrap
}
.p-p1,.p-critical{background:var(--red);color:#11111B}
.tsummary{grid-column:1/-1;color:var(--muted);font-size:.7rem;
          white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.tmeta{grid-column:1/-1;font-size:.65rem;color:var(--muted)}
.sla-over{color:var(--red);font-weight:700}
.no-tix{padding:24px;text-align:center;color:var(--muted);font-size:.85rem}
#tix-count{color:var(--red);font-size:.82rem}

/* ── Leaflet overrides ───────────────────────────────────────── */
.leaflet-container{background:#11111B}
.leaflet-control-attribution{display:none}
</style>
</head>
<body>

<!-- ── Header ──────────────────────────────────────────────────── -->
<header>
  <button id="ham" onclick="toggleMenu()" title="Menu">☰</button>
  <div class="brand">⚡ NOC STATUS BOARD · LEHIGH VALLEY</div>

  <div class="pills">
    <span class="pill"><span class="plbl">Listener</span><span class="pval pval-gray" id="pv-listener">—</span></span>
    <span class="pill"><span class="plbl">Sound</span><span class="pval pval-gray" id="pv-sound">—</span></span>
    <span class="pill"><span class="plbl">Active</span><span class="pval pval-gray" id="pv-active">—</span></span>
    <span class="pill"><span class="plbl">Queue</span><span class="pval pval-gray" id="pv-queue">—</span></span>
  </div>

  <div class="spacer"></div>

  <div class="legend">
    <span class="leg"><span class="leg-dot s-operational"></span>Operational</span>
    <span class="leg"><span class="leg-dot s-degraded"></span>Degraded</span>
    <span class="leg"><span class="leg-dot s-outage"></span>Outage</span>
    <span class="leg"><span class="leg-dot s-unknown"></span>Unknown</span>
  </div>

  <span id="last-updated">Loading…</span>
  <button class="refresh-btn" onclick="fetchData()">↺ Refresh</button>
</header>

<!-- ── Active alert banner ─────────────────────────────────────── -->
<div id="alert-banner" class="hidden">
  <div class="ab-info">
    <span class="ab-tag">🚨 P1 ALERT</span>
    <span class="ab-id" id="ab-id"></span>
    <span class="ab-client" id="ab-client"></span>
    <span class="ab-summary" id="ab-summary"></span>
  </div>
  <div class="ab-actions">
    <span class="ab-time" id="ab-time"></span>
    <button class="ab-btn ab-btn-sil" onclick="doAction('silence')">🔇 Silence</button>
    <button class="ab-btn ab-btn-ack" onclick="doAction('acknowledge')">✔ Acknowledge</button>
  </div>
</div>

<!-- ── Sidebar overlay ─────────────────────────────────────────── -->
<div id="overlay" onclick="closeMenu()"></div>

<!-- ── Sidebar nav ─────────────────────────────────────────────── -->
<nav id="sidebar">
  <div class="menu-top-bar"></div>

  <div class="menu-hdr">
    <span class="menu-hdr-title">NETWATCH</span>
    <button class="menu-close" onclick="closeMenu()">✕</button>
  </div>

  <div class="msec">ALERT ACTIONS</div>
  <button class="mbtn mbtn-primary" onclick="doAction('test-alert')">🧪&nbsp; Test Alert</button>
  <button class="mbtn" onclick="doAction('open-alert')">🖥&nbsp; Open Alert Window</button>
  <button class="mbtn" id="btn-silence" onclick="doAction('silence')">🔇&nbsp; Silence</button>
  <button class="mbtn mbtn-success" onclick="doAction('acknowledge')">✔&nbsp; Acknowledge</button>

  <div class="mdiv"></div>

  <div class="msec">LISTENER</div>
  <button class="mbtn" onclick="doAction('restart-listener')">↺&nbsp; Restart Listener</button>

  <div class="mdiv"></div>

  <div class="msec">HISTORY</div>
  <button class="mbtn" onclick="copyLastJson()">📋&nbsp; Copy Last JSON</button>
  <button class="mbtn mbtn-danger" onclick="doAction('clear-history',true)">🗑&nbsp; Clear History</button>

  <div class="mdiv"></div>

  <div class="msec">APP</div>
  <button class="mbtn" onclick="doAction('test-sound')">🔊&nbsp; Test Sound</button>
</nav>

<!-- ── Main content ────────────────────────────────────────────── -->
<main>
  <section id="svc-section" class="card">
    <div class="card-hdr">SERVICE STATUS</div>
    <div class="card-body">
      <div id="svc-grid" style="color:var(--muted);font-size:.8rem;padding:4px">
        Loading services…
      </div>
    </div>
  </section>

  <section id="map-section" class="card">
    <div class="card-hdr">ISP NETWORK STATUS · LEHIGH VALLEY, PA</div>
    <div id="map"></div>
    <div class="isp-bar" id="isp-bar"></div>
  </section>

  <section id="tix-section" class="card">
    <div class="card-hdr">
      CRITICAL TICKET QUEUE
      <span id="tix-count">—</span>
    </div>
    <div id="tix-list"><div class="no-tix">Loading…</div></div>
  </section>
</main>

<!-- ── Toast ────────────────────────────────────────────────────── -->
<div id="toast"></div>

<script>
// ── Leaflet map ──────────────────────────────────────────────────────────────
const map = L.map('map',{center:[40.635,-75.42],zoom:10,
  zoomControl:true,attributionControl:false});
L.tileLayer('https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png',
  {maxZoom:14}).addTo(map);
const ispLayers = {};
setTimeout(()=>map.invalidateSize(), 250);

// ── Sidebar ──────────────────────────────────────────────────────────────────
function toggleMenu(){
  document.getElementById('sidebar').classList.toggle('open');
  document.getElementById('overlay').classList.toggle('open');
}
function closeMenu(){
  document.getElementById('sidebar').classList.remove('open');
  document.getElementById('overlay').classList.remove('open');
}

// ── Toast ────────────────────────────────────────────────────────────────────
let _toastTimer=null;
function toast(msg,type='ok'){
  const el=document.getElementById('toast');
  el.textContent=msg;
  el.className='show'+(type==='error'?' toast-err':type==='warn'?' toast-warn':'');
  if(_toastTimer) clearTimeout(_toastTimer);
  _toastTimer=setTimeout(()=>el.classList.remove('show'),3200);
}

// ── Pill helpers ─────────────────────────────────────────────────────────────
function setPill(id,value,color){
  const el=document.getElementById('pv-'+id);
  if(!el) return;
  el.textContent=value;
  el.className='pval pval-'+color;
}

// ── Render ───────────────────────────────────────────────────────────────────
function sClass(s){
  if(s==='operational'||s==='up') return 's-operational';
  if(s==='degraded'||s==='slow')  return 's-degraded';
  if(s==='outage'||s==='down')    return 's-outage';
  return 's-unknown';
}

const CAT_ORDER=['Microsoft','Collaboration','Infrastructure',
                 'Networking','Identity','Backup','Security','Tooling'];

function renderServices(svcs){
  const cats={};
  svcs.forEach(s=>{(cats[s.cat]=cats[s.cat]||[]).push(s)});
  const order=[...CAT_ORDER.filter(c=>cats[c]),
               ...Object.keys(cats).filter(c=>!CAT_ORDER.includes(c))];
  const grid=document.getElementById('svc-grid');
  grid.innerHTML='';
  order.forEach(cat=>{
    const row=document.createElement('div');
    row.className='cat-row';
    const lbl=document.createElement('span');
    lbl.className='cat-lbl';lbl.textContent=cat;
    row.appendChild(lbl);
    cats[cat].forEach(svc=>{
      const a=document.createElement('a');
      a.className='chip';a.href=svc.page||'#';
      a.target='_blank';a.rel='noopener';
      a.title=svc.description||svc.status;
      const d=document.createElement('span');
      d.className='dot '+sClass(svc.status);
      a.appendChild(d);
      a.appendChild(document.createTextNode(svc.name));
      row.appendChild(a);
    });
    grid.appendChild(row);
  });
}

function renderISPs(isps){
  Object.values(ispLayers).forEach(l=>l.remove());
  Object.keys(ispLayers).forEach(k=>delete ispLayers[k]);
  const bar=document.getElementById('isp-bar');
  bar.innerHTML='';
  isps.forEach(isp=>{
    const down=(isp.status==='outage'||isp.status==='down');
    const slow=(isp.status==='slow'||isp.status==='degraded');
    const cc=down?'#F38BA8':slow?'#F9E2AF':isp.color;
    const fo=down?.60:slow?.40:.20;
    const circle=L.circle([isp.lat,isp.lng],{
      radius:14000,color:cc,fillColor:cc,fillOpacity:fo,weight:down?3:1.5
    }).addTo(map);
    const probe=isp.latency_ms!=null?isp.latency_ms+'ms':isp.probe_status;
    circle.bindTooltip(
      `<strong>${isp.name}</strong><br>Probe: ${probe}${isp.manual_outage?'<br>⚠ OUTAGE CONFIRMED':''}`,
      {sticky:true}
    );
    ispLayers[isp.name]=circle;
    const btn=document.createElement('button');
    btn.className='isp-btn'+(isp.manual_outage?' confirmed':'');
    btn.title='Click to confirm / clear manual outage';
    btn.innerHTML=
      `<span class="isp-dot" style="background:${isp.color}"></span>`+
      `<span>${isp.name}</span>`+
      `<span class="isp-probe">${probe}</span>`+
      (isp.manual_outage?'<span class="isp-flag">⚠ OUTAGE</span>':'');
    btn.onclick=()=>toggleISP(isp.name);
    bar.appendChild(btn);
  });
}

function renderTickets(tickets){
  const list=document.getElementById('tix-list');
  document.getElementById('tix-count').textContent=tickets.length||'0';
  if(!tickets.length){
    list.innerHTML='<div class="no-tix">No critical tickets open</div>';
    return;
  }
  list.innerHTML='';
  tickets.forEach(t=>{
    const pc=(t.priority||'').toLowerCase().replace(/\s+/g,'');
    const row=document.createElement('div');
    row.className='trow';
    row.innerHTML=
      `<span class="tid">${h(t.ticket_id)}</span>`+
      `<span class="tclient">${h(t.client)}</span>`+
      `<span class="pbadge p-${pc}">${h(t.priority)}</span>`+
      `<span class="tsummary">${h(t.summary)}</span>`;
    let meta='';
    if(t.sla) meta=t.sla_overdue
      ?`<span class="sla-over">⚠ SLA: ${h(t.sla)}</span>`
      :`SLA: ${h(t.sla)}`;
    else if(t.received) meta=h(t.received);
    if(t.source) meta+=(meta?' · ':'')+h(t.source);
    if(meta) row.innerHTML+=`<span class="tmeta">${meta}</span>`;
    list.appendChild(row);
  });
}

function renderAlertBanner(a,silenced){
  const banner=document.getElementById('alert-banner');
  if(!a){banner.classList.add('hidden');return;}
  banner.classList.remove('hidden');
  document.getElementById('ab-id').textContent=a.ticket_id;
  document.getElementById('ab-client').textContent=a.client;
  document.getElementById('ab-summary').textContent=a.summary;
  document.getElementById('ab-time').textContent=a.received;
  // Update silence button text in banner
  banner.querySelector('.ab-btn-sil').textContent=silenced?'🔊 Unmute':'🔇 Silence';
}

function renderPills(data){
  setPill('listener',data.listener_running?'RUNNING':'STOPPED',
          data.listener_running?'green':'red');
  setPill('sound',data.sound_silenced?'MUTED':'ON',
          data.sound_silenced?'gray':'green');
  const act=data.active_alert?1:0;
  setPill('active',act,act?'red':'gray');
  const q=data.queue_count||0;
  setPill('queue',q,q?'amber':'gray');
  // Sync sidebar silence button label
  const sBtn=document.getElementById('btn-silence');
  if(sBtn) sBtn.textContent=(data.sound_silenced?'🔊':'🔇')+'  '+(data.sound_silenced?'Unmute':'Silence');
}

// ── Data fetch ───────────────────────────────────────────────────────────────
async function fetchData(){
  try{
    const r=await fetch('/api/board-data');
    if(!r.ok) throw new Error('HTTP '+r.status);
    const d=await r.json();
    renderServices(d.services||[]);
    renderISPs(d.isps||[]);
    renderTickets(d.tickets||[]);
    renderAlertBanner(d.active_alert||null,d.sound_silenced);
    renderPills(d);
    const dt=new Date(d.last_updated);
    document.getElementById('last-updated').textContent=
      'Updated '+dt.toLocaleTimeString([],{hour:'2-digit',minute:'2-digit',second:'2-digit'});
  }catch(e){
    console.error(e);
    document.getElementById('last-updated').textContent='Update failed — retrying…';
  }
}

// ── Actions ──────────────────────────────────────────────────────────────────
async function doAction(name,confirm_first=false){
  if(confirm_first&&!confirm('Are you sure?')) return;
  closeMenu();
  try{
    const r=await fetch('/api/action/'+name,{method:'POST',
      headers:{'Content-Type':'application/json'},body:'{}'});
    const d=await r.json();
    if(d.status==='no_active_alert'){toast('No active alert','warn');return;}
    toast(d.message||'Done');
    setTimeout(fetchData,400);
  }catch(e){
    toast('Action failed: '+e.message,'error');
  }
}

async function toggleISP(name){
  try{
    await fetch('/api/board/isp-toggle',{
      method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({isp:name})
    });
    fetchData();
  }catch(e){toast('Toggle failed','error');}
}

async function copyLastJson(){
  closeMenu();
  try{
    const r=await fetch('/recent-alerts?n=1');
    const d=await r.json();
    if(!d.alerts||!d.alerts.length){toast('No alerts in history','warn');return;}
    await navigator.clipboard.writeText(JSON.stringify(d.alerts[0],null,2));
    toast('📋 Copied last alert JSON');
  }catch(e){toast('Copy failed: '+e.message,'error');}
}

// ── Escape helper ────────────────────────────────────────────────────────────
function h(s){
  return String(s||'')
    .replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;')
    .replace(/"/g,'&quot;').replace(/'/g,'&#39;');
}

// ── Boot ─────────────────────────────────────────────────────────────────────
fetchData();
setInterval(fetchData,15000);   // refresh every 15s so pills + alert banner stay current
</script>
</body>
</html>
"""
