"""
halo_poller.py – Polls Halo PSA for unassigned tickets and network outages.
"""
from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timezone
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from state import AppState

log = logging.getLogger("p1alert.halo_poller")


class HaloPollerThread(threading.Thread):
    def __init__(self, state: "AppState") -> None:
        super().__init__(daemon=True, name="HaloPollerThread")
        self._state = state
        self._token: str | None = None
        self._token_expiry: float = 0.0
        # Internal tracking for outage correlation: ticket_id -> OutageEvent
        self._outage_by_ticket: dict[int, object] = {}

    def run(self) -> None:
        while True:
            s = self._state.settings
            interval = s.get("halo_poll_interval", 120)
            base_url = (s.get("halo_base_url") or "").rstrip("/")
            if not base_url or not s.get("halo_client_id") or not s.get("halo_client_secret"):
                log.warning("Halo credentials not configured – skipping poll")
                time.sleep(interval)
                continue

            try:
                self._ensure_token(s)
            except Exception as exc:
                log.warning(f"Halo token refresh failed: {exc}")
                time.sleep(interval)
                continue

            try:
                self._poll_tickets(s)
            except Exception as exc:
                log.warning(f"Halo ticket poll failed: {exc}")

            try:
                self._poll_outages(s)
            except Exception as exc:
                log.warning(f"Halo outage poll failed: {exc}")

            time.sleep(interval)

    # ------------------------------------------------------------------ OAuth2

    def _ensure_token(self, s: dict) -> None:
        import requests
        now = time.time()
        if self._token and now < self._token_expiry - 60:
            return
        base_url = s["halo_base_url"].rstrip("/")
        resp = requests.post(
            f"{base_url}/auth/token",
            data={
                "grant_type": "client_credentials",
                "client_id": s["halo_client_id"],
                "client_secret": s["halo_client_secret"],
            },
            timeout=15,
        )
        resp.raise_for_status()
        body = resp.json()
        self._token = body["access_token"]
        self._token_expiry = now + body.get("expires_in", 3600)

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self._token}"}

    # ------------------------------------------------------------------ Poll A: unassigned tickets

    def _poll_tickets(self, s: dict) -> None:
        import requests
        from models import TicketRow, QueueMsg, MSG_TICKET_UPDATE
        base_url = s["halo_base_url"].rstrip("/")
        resp = requests.get(
            f"{base_url}/api/tickets",
            headers=self._headers(),
            params={
                "open_only": "true",
                "unassigned": "true",
                "pageinate": "true",
                "page_size": 50,
                "page_no": 1,
            },
            timeout=15,
        )
        resp.raise_for_status()
        raw = resp.json()
        tickets = raw.get("tickets", raw) if isinstance(raw, dict) else raw

        rows: list[TicketRow] = []
        for t in tickets:
            sla_remaining = _parse_sla(t)
            rows.append(TicketRow(
                ticket_id=t.get("id", 0),
                client=t.get("client_name", ""),
                subject=t.get("summary", ""),
                priority=t.get("priority_name", ""),
                sla_remaining_minutes=sla_remaining,
            ))

        rows.sort(key=lambda r: (r.sla_remaining_minutes is None, r.sla_remaining_minutes or 0))
        self._state.ticket_queue = rows
        self._state.alert_queue.put(QueueMsg(kind=MSG_TICKET_UPDATE, data=rows))

    # ------------------------------------------------------------------ Poll B: outage detection

    def _poll_outages(self, s: dict) -> None:
        import requests
        from models import OutageEvent, QueueMsg, MSG_OUTAGE_UPDATE
        base_url = s["halo_base_url"].rstrip("/")
        keywords = [
            kw.strip().lower()
            for kw in s.get("outage_device_keywords", "").split(",")
            if kw.strip()
        ]
        resp = requests.get(
            f"{base_url}/api/tickets",
            headers=self._headers(),
            params={"open_only": "true", "page_size": 25, "page_no": 1},
            timeout=15,
        )
        resp.raise_for_status()
        raw = resp.json()
        tickets = raw.get("tickets", raw) if isinstance(raw, dict) else raw

        matched_ids: set[int] = set()
        for t in tickets:
            summary = t.get("summary", "")
            ticket_type = t.get("ticket_type_name") or t.get("tickettype_name") or ""
            haystack = (summary + " " + ticket_type).lower()
            if not any(kw in haystack for kw in keywords):
                continue
            tid = t.get("id", 0)
            matched_ids.add(tid)
            if tid not in self._outage_by_ticket:
                self._outage_by_ticket[tid] = OutageEvent(
                    source="Halo",
                    service=f"{t.get('client_name', 'Unknown')}: {ticket_type or 'Network Issue'}",
                    summary=summary[:120],
                    detected_at=datetime.now(timezone.utc).isoformat(),
                )

        changed = False
        for tid, ev in list(self._outage_by_ticket.items()):
            if tid not in matched_ids and not ev.resolved:  # type: ignore[attr-defined]
                ev.resolved = True  # type: ignore[attr-defined]
                changed = True
            elif tid in matched_ids:
                changed = True  # new entry already detected above

        if changed:
            active = [ev for ev in self._outage_by_ticket.values() if not ev.resolved]  # type: ignore[attr-defined]
            self._state.active_outages = list(active)
            self._state.alert_queue.put(QueueMsg(kind=MSG_OUTAGE_UPDATE, data=list(active)))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _parse_sla(ticket: dict) -> int | None:
    raw = ticket.get("slaexpiry") or ticket.get("sla_expiry") or ticket.get("slaExpiry")
    if not raw:
        return None
    try:
        expiry_dt = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        diff_minutes = int((expiry_dt - datetime.now(timezone.utc)).total_seconds() / 60)
        return diff_minutes
    except Exception:
        return None
