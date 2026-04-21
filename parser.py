"""
parser.py – Payload parsing and P1 trigger logic.

ALL matching decisions live in is_p1_alert().  Edit that function to
change what qualifies as an alert-worthy event.

Field extraction lives in extract_alert_fields().  Add new field mappings
here without touching the rest of the app.
"""
from __future__ import annotations

import logging
from typing import Any

from models import Alert
from utils import now_iso

log = logging.getLogger("p1alert.parser")

# ---------------------------------------------------------------------------
# Impact / urgency value maps (adjust to match your tool's terminology)
# ---------------------------------------------------------------------------
CRITICAL_IMPACT_VALUES = {"1", "highest", "critical", "high"}
CRITICAL_URGENCY_VALUES = {"1", "highest", "critical", "high"}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def is_p1_alert(payload: dict[str, Any]) -> bool:
    """
    Return True if the payload describes a P1 / critical incident.

    Matching rules (any match → True):
      1. priority == "P1" (case-insensitive, also matches "P1 - Critical" etc.)
      2. priority == 1   (integer)
      3. priority_name contains "P1"
      4. severity contains "critical"
      5. impact   in CRITICAL_IMPACT_VALUES
      6. urgency  in CRITICAL_URGENCY_VALUES

    Add more rules below; keep them explicit and easy to read.
    """
    # ── Rule 1 & 2: priority field ────────────────────────────────────────
    priority_raw = (
        payload.get("priority")
        or payload.get("priority_id")
        or ""
    )
    priority_str = str(priority_raw).strip().lower()

    if "p1" in priority_str:
        log.debug(f"MATCH: priority field contains 'p1' → {priority_raw!r}")
        return True

    if priority_raw == 1 or priority_str == "1":
        log.debug(f"MATCH: priority == 1 → {priority_raw!r}")
        return True

    # ── Rule 3: priority_name ─────────────────────────────────────────────
    priority_name = str(payload.get("priority_name", "")).strip().lower()
    if "p1" in priority_name:
        log.debug(f"MATCH: priority_name contains 'p1' → {priority_name!r}")
        return True

    # ── Rule 4: severity ──────────────────────────────────────────────────
    severity = str(payload.get("severity", "")).strip().lower()
    if "critical" in severity:
        log.debug(f"MATCH: severity contains 'critical' → {severity!r}")
        return True

    # ── Rule 5: impact ────────────────────────────────────────────────────
    impact = str(payload.get("impact", "")).strip().lower()
    if impact in CRITICAL_IMPACT_VALUES:
        log.debug(f"MATCH: impact is critical → {impact!r}")
        return True

    # ── Rule 6: urgency ───────────────────────────────────────────────────
    urgency = str(payload.get("urgency", "")).strip().lower()
    if urgency in CRITICAL_URGENCY_VALUES:
        log.debug(f"MATCH: urgency is critical → {urgency!r}")
        return True

    log.debug("NO MATCH: payload is not P1/critical")
    return False


def extract_alert_fields(payload: dict[str, Any]) -> dict[str, Any]:
    """
    Extract well-known fields from the payload into a normalised dict.

    Supports generic webhook format AND HaloPSA-style payloads.
    Add more field aliases below without changing anything else.

    HaloPSA field mappings (example):
      id          → ticket_id
      ref         → ticket_id (fallback)
      summary     → summary
      client_name → client
      team        → assigned_team
      priority_id → priority
      priority_name → priority display
    """
    def _first(*keys: str, default: str = "") -> str:
        """Return the first non-empty value found among the given keys."""
        for k in keys:
            val = _deep_get(payload, k)
            if val is not None and str(val).strip():
                return str(val).strip()
        return default

    ticket_id = _first(
        "ticket_id", "id", "ref", "incident_id",
        "ticketId", "number", "ticket_number",
        default="Unknown Ticket",
    )

    client = _first(
        "client", "client_name", "customer", "organization",
        "account", "tenant", "company",
        default="Unknown Client",
    )

    summary = _first(
        "summary", "title", "subject", "description",
        "message", "short_description",
        default="No summary provided",
    )

    source = _first(
        "source", "system", "integration", "source_system",
        "origin", "tool",
        default="Unknown Source",
    )

    priority = _first(
        "priority", "priority_name", "priority_id",
        default="P1",
    )

    severity = _first("severity", "impact_name", default="")

    assigned_team = _first(
        "assigned_team", "team", "assignment_group",
        "assignee_team", "group",
        default="",
    )

    created_time = _first(
        "created_time", "created_at", "dateoccurred",
        "opened_at", "created", "timestamp",
        default="",
    )

    return {
        "ticket_id": ticket_id,
        "client": client,
        "summary": summary,
        "source": source,
        "priority": priority,
        "severity": severity,
        "assigned_team": assigned_team,
        "created_time": created_time,
    }


def build_alert(payload: dict[str, Any]) -> Alert:
    """Parse a payload dict into an Alert object."""
    fields = extract_alert_fields(payload)
    alert = Alert(
        ticket_id=fields["ticket_id"],
        client=fields["client"],
        summary=fields["summary"],
        source=fields["source"],
        priority=fields["priority"],
        severity=fields["severity"],
        assigned_team=fields["assigned_team"],
        created_time=fields["created_time"],
        received_time=now_iso(),
        raw_payload=payload,
    )
    # Build dedupe key: prefer ticket_id; fall back to client+summary hash
    if alert.ticket_id not in ("Unknown Ticket", ""):
        alert.dedupe_key = f"ticket:{alert.ticket_id}"
    else:
        import hashlib
        composite = f"{alert.client}|{alert.summary}"
        alert.dedupe_key = "composite:" + hashlib.md5(composite.encode()).hexdigest()[:12]

    log.debug(f"Built alert dedupe_key={alert.dedupe_key!r} ticket={alert.ticket_id!r}")
    return alert


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _deep_get(d: dict, key: str, default: Any = None) -> Any:
    """
    Simple key lookup that also checks one level of nesting.
    e.g. _deep_get(d, "client_name") also tries d["client"]["name"].
    """
    if key in d:
        return d[key]
    # Try camelCase variant
    camel = _to_camel(key)
    if camel in d:
        return d[camel]
    # Try nested: "client_name" → d["client"]["name"]
    parts = key.split("_", 1)
    if len(parts) == 2:
        parent, child = parts
        if isinstance(d.get(parent), dict):
            return d[parent].get(child, default)
    return default


def _to_camel(snake: str) -> str:
    parts = snake.split("_")
    return parts[0] + "".join(p.capitalize() for p in parts[1:])
