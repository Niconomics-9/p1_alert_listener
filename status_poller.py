"""
status_poller.py – Polls external status pages (M365, AWS) for active incidents.
"""
from __future__ import annotations

import json
import logging
import threading
import time
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from state import AppState

log = logging.getLogger("p1alert.status_poller")


class StatusPollerThread(threading.Thread):
    def __init__(self, state: "AppState") -> None:
        super().__init__(daemon=True, name="StatusPollerThread")
        self._state = state
        self._prev_services: set[str] = set()

    def run(self) -> None:
        while True:
            s = self._state.settings
            interval = s.get("status_poll_interval", 300)
            events: list = []

            if s.get("monitor_m365", True):
                try:
                    events.extend(_poll_m365())
                except Exception as exc:
                    log.warning(f"M365 status poll failed: {exc}")

            if s.get("monitor_aws", True):
                try:
                    events.extend(_poll_aws())
                except Exception as exc:
                    log.warning(f"AWS status poll failed: {exc}")

            cur_services = {e.service for e in events}
            if cur_services != self._prev_services:
                from models import QueueMsg, MSG_OUTAGE_UPDATE
                halo = [e for e in self._state.active_outages if e.source == "Halo"]
                merged = halo + events
                self._state.active_outages = merged
                self._state.alert_queue.put(QueueMsg(kind=MSG_OUTAGE_UPDATE, data=merged))
                self._prev_services = cur_services

            time.sleep(interval)


# ---------------------------------------------------------------------------
# Per-source poll functions (module-level so they're easy to unit-test later)
# ---------------------------------------------------------------------------

def _poll_m365() -> list:
    from models import OutageEvent
    url = "https://status.office.com/api/feed/mac"
    with urllib.request.urlopen(url, timeout=15) as r:
        xml_data = r.read()

    root = ET.fromstring(xml_data)
    cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
    events = []

    for item in root.findall(".//item"):
        title_el = item.find("title")
        desc_el = item.find("description")
        pub_el = item.find("pubDate")

        title = (title_el.text or "") if title_el is not None else ""
        desc = (desc_el.text or "") if desc_el is not None else ""
        pub = (pub_el.text or "") if pub_el is not None else ""

        if "resolved" in title.lower():
            continue

        if pub:
            try:
                # RFC 2822 – e.g. "Thu, 24 Apr 2025 10:00:00 +0000"
                from email.utils import parsedate_to_datetime
                pub_dt = parsedate_to_datetime(pub)
                if pub_dt.tzinfo is None:
                    pub_dt = pub_dt.replace(tzinfo=timezone.utc)
                if pub_dt < cutoff:
                    continue
            except Exception:
                pass

        events.append(OutageEvent(
            source="M365",
            service=title or "Microsoft 365",
            summary=desc[:120],
            detected_at=datetime.now(timezone.utc).isoformat(),
        ))

    return events


def _poll_aws() -> list:
    from models import OutageEvent
    url = "https://health.aws.amazon.com/health/status"
    with urllib.request.urlopen(url, timeout=15) as r:
        data = json.loads(r.read())

    items = data if isinstance(data, list) else data.get("items", [])
    events = []
    for entry in items:
        if str(entry.get("status", "ok")).lower() == "ok":
            continue
        events.append(OutageEvent(
            source="AWS",
            service=entry.get("service", "AWS Service"),
            summary=str(entry.get("summary", ""))[:120],
            detected_at=datetime.now(timezone.utc).isoformat(),
        ))
    return events
