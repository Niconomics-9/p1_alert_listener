"""
service_poller.py – Polls external service status APIs and ISP reachability.

Results are stored in AppState.service_status and AppState.isp_status.
The NOC board (/board) reads from these dicts via /api/board-data.
"""
from __future__ import annotations

import logging
import socket
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from state import AppState

log = logging.getLogger("p1alert.service_poller")

POLL_INTERVAL = 60  # seconds between full polls

# ---------------------------------------------------------------------------
# Service catalog
# type: "statuspage" | "slack" | "aws" | "gcp" | "http"
# ---------------------------------------------------------------------------

SERVICES = [
    # Microsoft (no unauthenticated public JSON API — HTTP reachability probe)
    {"name": "Microsoft 365", "cat": "Microsoft",      "type": "http",       "probe": "https://portal.office.com",                            "page": "https://portal.office.com/servicestatus"},
    {"name": "Azure",         "cat": "Microsoft",      "type": "http",       "probe": "https://portal.azure.com",                             "page": "https://azure.status.microsoft"},
    {"name": "Teams",         "cat": "Microsoft",      "type": "http",       "probe": "https://teams.microsoft.com",                          "page": "https://portal.office.com/servicestatus"},
    # Collaboration
    {"name": "Slack",         "cat": "Collaboration",  "type": "slack",      "probe": "https://status.slack.com/api/v2.0.0/current",          "page": "https://status.slack.com"},
    {"name": "Zoom",          "cat": "Collaboration",  "type": "statuspage", "probe": "https://status.zoom.us/api/v2/status.json",            "page": "https://status.zoom.us"},
    # Infrastructure
    {"name": "AWS",           "cat": "Infrastructure", "type": "aws",        "probe": "https://status.aws.amazon.com/data.json",              "page": "https://health.aws.amazon.com/health/status"},
    {"name": "GCP",           "cat": "Infrastructure", "type": "gcp",        "probe": "https://status.cloud.google.com/incidents.json",       "page": "https://status.cloud.google.com"},
    {"name": "Cloudflare",    "cat": "Infrastructure", "type": "statuspage", "probe": "https://www.cloudflarestatus.com/api/v2/status.json",  "page": "https://www.cloudflarestatus.com"},
    # Networking
    {"name": "Meraki",        "cat": "Networking",     "type": "statuspage", "probe": "https://meraki.statuspage.io/api/v2/status.json",      "page": "https://meraki.statuspage.io"},
    {"name": "Ubiquiti",      "cat": "Networking",     "type": "statuspage", "probe": "https://status.ui.com/api/v2/status.json",             "page": "https://status.ui.com"},
    # Identity
    {"name": "Duo",           "cat": "Identity",       "type": "statuspage", "probe": "https://status.duo.com/api/v2/status.json",            "page": "https://status.duo.com"},
    {"name": "Okta",          "cat": "Identity",       "type": "statuspage", "probe": "https://status.okta.com/api/v2/status.json",           "page": "https://status.okta.com"},
    # Backup
    {"name": "Acronis",       "cat": "Backup",         "type": "statuspage", "probe": "https://status.acronis.com/api/v2/status.json",        "page": "https://status.acronis.com"},
    {"name": "Veeam",         "cat": "Backup",         "type": "statuspage", "probe": "https://veeam.statuspage.io/api/v2/status.json",       "page": "https://veeam.statuspage.io"},
    # Security
    {"name": "Huntress",      "cat": "Security",       "type": "statuspage", "probe": "https://status.huntress.com/api/v2/status.json",       "page": "https://status.huntress.com"},
    {"name": "ThreatLocker",  "cat": "Security",       "type": "statuspage", "probe": "https://status.threatlocker.com/api/v2/status.json",   "page": "https://status.threatlocker.com"},
    {"name": "ConnectSecure", "cat": "Security",       "type": "statuspage", "probe": "https://status.connectsecure.com/api/v2/status.json",  "page": "https://status.connectsecure.com"},
    {"name": "Inky",          "cat": "Security",       "type": "statuspage", "probe": "https://status.inky.email/api/v2/status.json",         "page": "https://status.inky.email"},
    # Tooling
    {"name": "GitHub",        "cat": "Tooling",        "type": "statuspage", "probe": "https://www.githubstatus.com/api/v2/status.json",      "page": "https://www.githubstatus.com"},
    {"name": "Datto RMM",     "cat": "Tooling",        "type": "statuspage", "probe": "https://status.datto.com/api/v2/status.json",          "page": "https://status.datto.com"},
    {"name": "HaloPSA",       "cat": "Tooling",        "type": "statuspage", "probe": "https://halopsa.statuspage.io/api/v2/status.json",     "page": "https://halopsa.statuspage.io"},
]

# ---------------------------------------------------------------------------
# ISP definitions — Lehigh Valley, PA
# probe: list of (host_or_ip, port) tuples tried in order
# ---------------------------------------------------------------------------

ISPS = [
    {
        "name":       "Comcast",
        "color":      "#0070CC",
        # 75.75.75.75 and 75.75.76.76 are Comcast's well-known public DNS servers
        "probe":      [("75.75.75.75", 53), ("75.75.76.76", 53)],
        "status_url": "https://www.xfinity.com/support/status",
        "lat": 40.60, "lng": -75.47,
    },
    {
        "name":       "Service Electric",
        "color":      "#FF6600",
        "probe":      [("serviceelectric.net", 443)],
        "status_url": "https://www.serviceelectric.net",
        "lat": 40.63, "lng": -75.37,
    },
    {
        "name":       "Astound",
        "color":      "#8B3FC3",
        "probe":      [("astound.net", 443)],
        "status_url": "https://www.astound.com/support/outages",
        "lat": 40.69, "lng": -75.21,
    },
    {
        "name":       "PTD",
        "color":      "#20B2AA",
        "probe":      [("ptd.net", 443)],
        "status_url": "https://www.ptd.net",
        "lat": 40.87, "lng": -75.67,
    },
]


# ---------------------------------------------------------------------------
# Thread
# ---------------------------------------------------------------------------

class ServicePollerThread(threading.Thread):
    def __init__(self, state: "AppState") -> None:
        super().__init__(daemon=True, name="ServicePollerThread")
        self._state = state

    def run(self) -> None:
        import requests
        session = requests.Session()
        session.headers["User-Agent"] = "NetWatch/1.0 noc-status-monitor"

        self._init_defaults()

        while True:
            self._poll_services(session)
            self._probe_isps()
            time.sleep(POLL_INTERVAL)

    # ------------------------------------------------------------------ init

    def _init_defaults(self) -> None:
        now = datetime.now(timezone.utc).isoformat()
        for svc in SERVICES:
            self._state.service_status.setdefault(svc["name"], {
                "cat": svc["cat"], "status": "unknown",
                "description": "Initializing…", "page": svc["page"],
                "method": svc["type"], "updated_at": now,
            })
        for isp in ISPS:
            self._state.isp_status.setdefault(isp["name"], {
                "color": isp["color"], "status_url": isp["status_url"],
                "lat": isp["lat"], "lng": isp["lng"],
                "probe_status": "unknown", "manual_outage": False,
                "latency_ms": None, "updated_at": now,
            })

    # ------------------------------------------------------------------ services

    def _poll_services(self, session) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with ThreadPoolExecutor(max_workers=8) as ex:
            futures = {ex.submit(_poll_one_service, svc, session): svc for svc in SERVICES}
            for future in as_completed(futures):
                svc = futures[future]
                try:
                    status, desc = future.result()
                except Exception as exc:
                    log.debug(f"Service poll error [{svc['name']}]: {exc}")
                    status, desc = "unknown", "Poll failed"
                self._state.service_status[svc["name"]] = {
                    "cat": svc["cat"], "status": status, "description": desc,
                    "page": svc["page"], "method": svc["type"], "updated_at": now,
                }

    # ------------------------------------------------------------------ ISPs

    def _probe_isps(self) -> None:
        now = datetime.now(timezone.utc).isoformat()
        for isp in ISPS:
            probe_status, latency = _probe_tcp(isp["probe"])
            with self._state._lock:
                existing = self._state.isp_status.get(isp["name"], {})
                existing.update({
                    "probe_status": probe_status,
                    "latency_ms": latency,
                    "updated_at": now,
                })
                self._state.isp_status[isp["name"]] = existing


# ---------------------------------------------------------------------------
# Service poll helpers
# ---------------------------------------------------------------------------

def _poll_one_service(svc: dict, session) -> tuple[str, str]:
    url = svc["probe"]
    stype = svc["type"]
    try:
        if stype == "http":
            resp = session.head(url, timeout=10, allow_redirects=True)
            if resp.status_code < 500:
                return "operational", "Reachable (connectivity check only)"
            return "outage", f"HTTP {resp.status_code}"

        resp = session.get(url, timeout=10)
        resp.raise_for_status()
        data = resp.json()

        if stype == "statuspage":
            return _parse_statuspage(data)
        if stype == "slack":
            return _parse_slack(data)
        if stype == "aws":
            return _parse_aws(data)
        if stype == "gcp":
            return _parse_gcp(data)
        return "unknown", "Unsupported type"
    except Exception as exc:
        log.debug(f"Poll [{svc['name']}] failed: {exc}")
        return "unknown", "Unreachable"


def _parse_statuspage(data: dict) -> tuple[str, str]:
    indicator = data.get("status", {}).get("indicator", "none")
    description = data.get("status", {}).get("description", "")
    if indicator == "none":
        return "operational", description or "All Systems Operational"
    if indicator in ("minor", "maintenance"):
        return "degraded", description or "Minor Issues"
    return "outage", description or "Service Disruption"


def _parse_slack(data: dict) -> tuple[str, str]:
    status = data.get("status", "")
    incidents = data.get("active_incidents", [])
    if not incidents:
        return "operational", "All Systems Operational"
    for inc in incidents:
        if inc.get("severity") in ("SEV1", "SEV2"):
            return "outage", inc.get("title", "Major incident in progress")
    first = incidents[0]
    return "degraded", first.get("title", "Incident in progress")


def _parse_aws(data) -> tuple[str, str]:
    if isinstance(data, list):
        items = data
    elif isinstance(data, dict):
        items = data.get("current", [])
    else:
        return "unknown", "Unexpected format"
    if not items:
        return "operational", "All Systems Operational"
    for item in items:
        if isinstance(item, dict):
            s = str(item.get("status", "0"))
            if s not in ("0", "ok", "OK"):
                return "outage", item.get("service_name", "AWS") + " disruption"
    return "operational", "All Systems Operational"


def _parse_gcp(data) -> tuple[str, str]:
    if not isinstance(data, list):
        return "unknown", "Unexpected format"
    ongoing = [i for i in data if not i.get("end")]
    if not ongoing:
        return "operational", "All Systems Operational"
    high = [i for i in ongoing if i.get("severity") in ("high", "critical")]
    if high:
        inc = high[0]
        svc_name = inc.get("service_name", "Google Cloud")
        return "outage", f"{svc_name} — {inc.get('external_desc', 'Outage')[:60]}"
    return "degraded", ongoing[0].get("external_desc", "Service degraded")[:60]


# ---------------------------------------------------------------------------
# ISP TCP probe
# ---------------------------------------------------------------------------

def _probe_tcp(targets: list[tuple[str, int]]) -> tuple[str, int | None]:
    """
    Try TCP connect to each (host, port) in order.
    Returns (status, latency_ms): status is "up"|"slow"|"down"|"unknown".
    """
    for host, port in targets:
        start = time.monotonic()
        try:
            with socket.create_connection((host, port), timeout=2.5):
                latency = int((time.monotonic() - start) * 1000)
                return ("slow" if latency > 800 else "up"), latency
        except (socket.timeout, TimeoutError):
            continue
        except OSError:
            continue
    return "down", None
