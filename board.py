"""
board.py – NOC Status Board Flask blueprint.

Routes:
  GET  /board                 Full-page HTML NOC status board
  GET  /api/board-data        JSON: services, ISPs, P1 tickets, last_updated
  POST /api/board/isp-toggle  Toggle manual outage flag for a named ISP
"""
from __future__ import annotations

import logging
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

    @bp.route("/board")
    def board():
        return Response(_BOARD_HTML, mimetype="text/html; charset=utf-8")

    @bp.route("/api/board-data")
    def board_data():
        return jsonify(_build_board_data(app_state))

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
        log.info("ISP manual toggle: %s -> manual_outage=%s", name, manual)
        return jsonify({"isp": name, "manual_outage": manual})

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

    return {
        "services": services,
        "isps": isps,
        "tickets": _get_critical_tickets(app_state),
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
:root {
  --bg:      #11111B;
  --surface: #1E1E2E;
  --card:    #242436;
  --border:  #313244;
  --text:    #CDD6F4;
  --muted:   #6C7086;
  --green:   #A6E3A1;
  --amber:   #F9E2AF;
  --red:     #F38BA8;
  --blue:    #89B4FA;
  --font: 'Segoe UI', system-ui, sans-serif;
}
*{box-sizing:border-box;margin:0;padding:0}
html,body{height:100%;overflow:hidden}
body{background:var(--bg);color:var(--text);font-family:var(--font);
     display:flex;flex-direction:column}

/* ── Header ─────────────────────────────────────────────────────── */
header{
  background:#0D0D1A;border-bottom:2px solid var(--blue);
  padding:0 16px;height:48px;flex-shrink:0;
  display:flex;align-items:center;justify-content:space-between;gap:12px
}
.brand{font-size:1rem;font-weight:700;color:var(--blue);letter-spacing:.06em;white-space:nowrap}
.header-meta{display:flex;align-items:center;gap:16px;font-size:.72rem;color:var(--muted)}
#last-updated{white-space:nowrap}
.legend{display:flex;gap:10px;align-items:center}
.leg{display:flex;align-items:center;gap:4px;font-size:.67rem}
.leg-dot{width:8px;height:8px;border-radius:50%}
.refresh-btn{
  font-size:.72rem;color:var(--blue);cursor:pointer;
  background:none;border:1px solid var(--blue);border-radius:4px;
  padding:4px 10px;white-space:nowrap;transition:background .15s
}
.refresh-btn:hover{background:rgba(137,180,250,.15)}

/* ── Main grid ───────────────────────────────────────────────────── */
main{
  flex:1;min-height:0;
  display:grid;
  grid-template-rows:auto 1fr;
  grid-template-columns:1fr 340px;
  gap:8px;padding:8px
}
#svc-section{grid-column:1/-1}

/* ── Cards ───────────────────────────────────────────────────────── */
.card{background:var(--card);border-radius:6px;overflow:hidden;
      display:flex;flex-direction:column}
.card-hdr{
  background:var(--surface);border-bottom:1px solid var(--border);
  padding:6px 12px;flex-shrink:0;
  display:flex;align-items:center;justify-content:space-between;
  font-size:.67rem;font-weight:700;color:var(--muted);letter-spacing:.1em
}
.card-body{flex:1;min-height:0;overflow:auto;padding:8px}

/* ── Service grid ────────────────────────────────────────────────── */
#svc-grid{display:flex;flex-direction:column;gap:4px}
.cat-row{display:flex;align-items:center;gap:6px;flex-wrap:wrap;padding:3px 0}
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

/* ── ISP section ─────────────────────────────────────────────────── */
#map-section{display:flex;flex-direction:column;overflow:hidden}
#map{flex:1;min-height:0;border-radius:0}
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

/* ── Ticket queue ────────────────────────────────────────────────── */
#tix-section{display:flex;flex-direction:column;overflow:hidden}
#tix-list{flex:1;min-height:0;overflow-y:auto}
.trow{
  padding:8px 10px;border-bottom:1px solid var(--border);
  display:grid;grid-template-columns:auto 1fr auto;
  align-items:start;column-gap:7px;row-gap:2px;
  font-size:.76rem
}
.trow:last-child{border-bottom:none}
.trow:hover{background:var(--surface)}
.tid{font-family:Consolas,monospace;font-size:.7rem;color:var(--blue);font-weight:700}
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

/* ── Leaflet dark overrides ──────────────────────────────────────── */
.leaflet-container{background:#11111B}
.leaflet-control-attribution{display:none}
</style>
</head>
<body>
<header>
  <div class="brand">⚡ NOC STATUS BOARD &nbsp;·&nbsp; LEHIGH VALLEY</div>
  <div class="header-meta">
    <div class="legend">
      <span class="leg"><span class="leg-dot s-operational"></span>Operational</span>
      <span class="leg"><span class="leg-dot s-degraded"></span>Degraded</span>
      <span class="leg"><span class="leg-dot s-outage"></span>Outage</span>
      <span class="leg"><span class="leg-dot s-unknown"></span>Unknown</span>
    </div>
    <span id="last-updated">Loading…</span>
  </div>
  <button class="refresh-btn" onclick="fetchData()">↺ Refresh</button>
</header>

<main>
  <!-- Service status — full width top row -->
  <section id="svc-section" class="card">
    <div class="card-hdr">SERVICE STATUS</div>
    <div class="card-body" id="svc-body">
      <div id="svc-grid" style="color:var(--muted);font-size:.8rem;padding:4px">Loading services…</div>
    </div>
  </section>

  <!-- ISP map — bottom left -->
  <section id="map-section" class="card">
    <div class="card-hdr">ISP NETWORK STATUS &nbsp;·&nbsp; LEHIGH VALLEY, PA</div>
    <div id="map"></div>
    <div class="isp-bar" id="isp-bar"></div>
  </section>

  <!-- Critical ticket queue — bottom right -->
  <section id="tix-section" class="card">
    <div class="card-hdr">
      CRITICAL TICKET QUEUE
      <span id="tix-count">—</span>
    </div>
    <div id="tix-list"><div class="no-tix">Loading…</div></div>
  </section>
</main>

<script>
// ── Leaflet map ──────────────────────────────────────────────────────────────
const map = L.map('map', {
  center: [40.635, -75.42],
  zoom: 10,
  zoomControl: true,
  attributionControl: false,
});
L.tileLayer('https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png', {
  maxZoom: 14,
}).addTo(map);

const ispLayers = {};

// ── Render functions ─────────────────────────────────────────────────────────

function statusClass(s) {
  if (s === 'operational' || s === 'up') return 's-operational';
  if (s === 'degraded' || s === 'slow') return 's-degraded';
  if (s === 'outage' || s === 'down') return 's-outage';
  return 's-unknown';
}

function renderServices(services) {
  const cats = {};
  const catOrder = ['Microsoft','Collaboration','Infrastructure','Networking','Identity','Backup','Security','Tooling'];
  services.forEach(s => { (cats[s.cat] = cats[s.cat] || []).push(s); });

  const grid = document.getElementById('svc-grid');
  grid.innerHTML = '';

  const ordered = [...catOrder.filter(c => cats[c]), ...Object.keys(cats).filter(c => !catOrder.includes(c))];
  ordered.forEach(cat => {
    const row = document.createElement('div');
    row.className = 'cat-row';
    const lbl = document.createElement('span');
    lbl.className = 'cat-lbl';
    lbl.textContent = cat;
    row.appendChild(lbl);

    cats[cat].forEach(svc => {
      const chip = document.createElement('a');
      chip.className = 'chip';
      chip.href = svc.page || '#';
      chip.target = '_blank';
      chip.rel = 'noopener';
      chip.title = svc.description || svc.status;
      const dot = document.createElement('span');
      dot.className = 'dot ' + statusClass(svc.status);
      chip.appendChild(dot);
      chip.appendChild(document.createTextNode(svc.name));
      row.appendChild(chip);
    });

    grid.appendChild(row);
  });
}

function renderISPs(isps) {
  // Clear old map layers
  Object.values(ispLayers).forEach(l => l.remove());
  const bar = document.getElementById('isp-bar');
  bar.innerHTML = '';

  isps.forEach(isp => {
    const isDown = (isp.status === 'outage' || isp.status === 'down');
    const isSlow = (isp.status === 'slow' || isp.status === 'degraded');
    const circleColor = isDown ? '#F38BA8' : isSlow ? '#F9E2AF' : isp.color;
    const fillOp = isDown ? 0.60 : isSlow ? 0.45 : 0.22;

    const circle = L.circle([isp.lat, isp.lng], {
      radius: 14000,
      color: circleColor,
      fillColor: circleColor,
      fillOpacity: fillOp,
      weight: isDown ? 3 : 1.5,
    }).addTo(map);

    const probe = isp.latency_ms != null ? isp.latency_ms + 'ms' : isp.probe_status;
    circle.bindTooltip(
      `<strong>${isp.name}</strong><br>Probe: ${probe}${isp.manual_outage ? '<br>⚠ OUTAGE CONFIRMED' : ''}`,
      { sticky: true }
    );
    ispLayers[isp.name] = circle;

    // Control button
    const btn = document.createElement('button');
    btn.className = 'isp-btn' + (isp.manual_outage ? ' confirmed' : '');
    btn.title = 'Click to toggle manual outage confirmation';
    btn.innerHTML =
      `<span class="isp-dot" style="background:${isp.color}"></span>` +
      `<span>${isp.name}</span>` +
      `<span class="isp-probe">${probe}</span>` +
      (isp.manual_outage ? '<span class="isp-flag">⚠ OUTAGE</span>' : '');
    btn.onclick = () => toggleISP(isp.name);
    bar.appendChild(btn);
  });
}

function renderTickets(tickets) {
  const list = document.getElementById('tix-list');
  const countEl = document.getElementById('tix-count');
  countEl.textContent = tickets.length ? tickets.length : '0';

  if (!tickets.length) {
    list.innerHTML = '<div class="no-tix">No critical tickets open</div>';
    return;
  }

  list.innerHTML = '';
  tickets.forEach(t => {
    const priLow = (t.priority || '').toLowerCase().replace(/\s+/g, '');
    const pClass = priLow === 'critical' ? 'p-critical' : 'p-p1';

    const row = document.createElement('div');
    row.className = 'trow';

    row.innerHTML =
      `<span class="tid">${escHtml(t.ticket_id)}</span>` +
      `<span class="tclient">${escHtml(t.client)}</span>` +
      `<span class="pbadge ${pClass}">${escHtml(t.priority)}</span>` +
      `<span class="tsummary">${escHtml(t.summary)}</span>`;

    // SLA / received line
    let meta = '';
    if (t.sla) {
      meta = t.sla_overdue
        ? `<span class="sla-over">⚠ SLA: ${escHtml(t.sla)}</span>`
        : `SLA: ${escHtml(t.sla)}`;
    } else if (t.received) {
      meta = escHtml(t.received);
    }
    if (t.source) meta += (meta ? '  ·  ' : '') + escHtml(t.source);
    if (meta) row.innerHTML += `<span class="tmeta">${meta}</span>`;

    list.appendChild(row);
  });
}

// ── Data fetch ───────────────────────────────────────────────────────────────

async function fetchData() {
  try {
    const r = await fetch('/api/board-data');
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    const data = await r.json();

    renderServices(data.services || []);
    renderISPs(data.isps || []);
    renderTickets(data.tickets || []);

    const dt = new Date(data.last_updated);
    document.getElementById('last-updated').textContent =
      'Updated ' + dt.toLocaleTimeString([], {hour:'2-digit', minute:'2-digit', second:'2-digit'});
  } catch (e) {
    console.error('Board data fetch failed:', e);
    document.getElementById('last-updated').textContent = 'Update failed — retrying…';
  }
}

async function toggleISP(name) {
  try {
    await fetch('/api/board/isp-toggle', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({isp: name}),
    });
    fetchData();
  } catch (e) {
    console.error('ISP toggle failed:', e);
  }
}

function escHtml(s) {
  return String(s || '')
    .replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;')
    .replace(/"/g,'&quot;').replace(/'/g,'&#39;');
}

// ── Boot ─────────────────────────────────────────────────────────────────────
fetchData();
setInterval(fetchData, 60000);

// Leaflet needs a size hint after DOM paint
setTimeout(() => map.invalidateSize(), 200);
</script>
</body>
</html>
"""
