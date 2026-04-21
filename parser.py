"""
parser.py – Payload parsing and P1 trigger logic.

P1 detection is per-source. Each source (halo_psa, datto_rmm, generic)
has its own configurable trigger rules stored in settings["source_rules"].

Field extraction lives in extract_alert_fields(). Add new field mappings
here without touching the rest of the app.
"""
from __future__ import annotations

import logging
from typing import Any

from models import Alert
from utils import now_iso

log = logging.getLogger("p1alert.parser")

# ---------------------------------------------------------------------------
# Source detection fingerprints
# ---------------------------------------------------------------------------

# Payload keys that uniquely identify each source
_DATTO_FINGERPRINTS = {"alertUid", "alertTypeId", "alertMessage", "siteName"}
_HALO_FINGERPRINTS  = {"client_name", "priority_name", "tickettypeid", "ref"}


def detect_source(payload: dict[str, Any]) -> str:
    """
    Identify which system sent the payload.
    Returns "datto_rmm", "halo_psa", or "generic".
    """
    keys = set(payload.keys())
    if keys & _DATTO_FINGERPRINTS:
        return "datto_rmm"
    if keys & _HALO_FINGERPRINTS:
        return "halo_psa"
    return "generic"


# ---------------------------------------------------------------------------
# Default per-source rules
# ---------------------------------------------------------------------------

def default_source_rules() -> dict[str, Any]:
    return {
        "halo_psa": {
            "enabled": True,
            # Trigger if priority contains any of these strings (case-insensitive)
            "trigger_priorities": ["p1"],
            # Trigger if severity contains any of these strings
            "trigger_severities": ["critical"],
            # Trigger if priority is exactly 1 (integer)
            "trigger_priority_id_1": True,
        },
        "datto_rmm": {
            "enabled": True,
            "trigger_priorities": ["critical"],
            "trigger_severities": [],
            # Trigger on Datto alertTypeId 1003 (device offline) regardless of priority
            "trigger_offline_alerts": True,
        },
        "generic": {
            "enabled": True,
            "trigger_priorities": ["p1"],
            "trigger_severities": ["critical"],
            "trigger_priority_id_1": True,
        },
    }


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def is_p1_alert(payload: dict[str, Any], settings: dict[str, Any] | None = None) -> bool:
    """
    Return True if the payload describes a P1 / critical incident.

    Uses per-source rules from settings["source_rules"] when provided,
    falling back to default_source_rules() if not configured.
    """
    source = detect_source(payload)
    rules_map = (settings or {}).get("source_rules", default_source_rules())

    # Fall back to defaults for any missing source
    defaults = default_source_rules()
    rules: dict[str, Any] = {**defaults.get(source, defaults["generic"]),
                              **rules_map.get(source, {})}

    if not rules.get("enabled", True):
        log.debug(f"Source '{source}' is disabled – ignoring payload")
        return False

    log.debug(f"Checking P1 rules for source='{source}'")

    # ── Priority string match ─────────────────────────────────────────────
    priority_raw = (
        payload.get("priority")
        or payload.get("priority_id")
        or payload.get("priority_name")
        or ""
    )
    priority_str = str(priority_raw).strip().lower()

    for trigger in rules.get("trigger_priorities", []):
        if trigger.lower() in priority_str:
            log.debug(f"MATCH [{source}]: priority '{priority_str}' contains '{trigger}'")
            return True

    # ── Priority == 1 (integer / Halo style) ─────────────────────────────
    if rules.get("trigger_priority_id_1", False):
        if priority_raw == 1 or priority_str == "1":
            log.debug(f"MATCH [{source}]: priority == 1")
            return True

    # ── Severity string match ─────────────────────────────────────────────
    severity = str(payload.get("severity", "")).strip().lower()
    for trigger in rules.get("trigger_severities", []):
        if trigger.lower() in severity:
            log.debug(f"MATCH [{source}]: severity '{severity}' contains '{trigger}'")
            return True

    # ── Datto: device offline alert type ─────────────────────────────────
    if source == "datto_rmm" and rules.get("trigger_offline_alerts", True):
        if str(payload.get("alertTypeId", "")) == "1003":
            log.debug("MATCH [datto_rmm]: alertTypeId 1003 (device offline)")
            return True

    log.debug(f"NO MATCH [{source}]: payload is not P1")
    return False


def extract_alert_fields(payload: dict[str, Any]) -> dict[str, Any]:
    """
    Extract well-known fields from the payload into a normalised dict.
    Supports Halo PSA, Datto RMM, and generic webhook formats.
    """
    def _first(*keys: str, default: str = "") -> str:
        for k in keys:
            val = _deep_get(payload, k)
            if val is not None and str(val).strip():
                return str(val).strip()
        return default

    source = detect_source(payload)

    ticket_id = _first(
        "ticket_id", "id", "ref", "incident_id",
        "ticketId", "number", "ticket_number",
        "alertUid",                               # Datto
        default="Unknown Ticket",
    )

    client = _first(
        "client", "client_name", "customer", "organization",
        "account", "tenant", "company",
        "siteName",                               # Datto
        default="Unknown Client",
    )

    # Datto: combine alertMessage + hostname into a useful summary
    if source == "datto_rmm":
        datto_msg  = payload.get("alertMessage", "")
        datto_host = payload.get("hostname", "")
        if datto_msg and datto_host:
            payload = {**payload, "_summary": f"{datto_msg} [{datto_host}]"}
        elif datto_msg or datto_host:
            payload = {**payload, "_summary": datto_msg or datto_host}

    summary = _first(
        "summary", "title", "subject", "description",
        "message", "short_description",
        "_summary",                               # Datto synthesised
        default="No summary provided",
    )

    source_label = _first(
        "source", "system", "integration", "source_system", "origin", "tool",
        default={"datto_rmm": "Datto RMM", "halo_psa": "Halo PSA"}.get(source, "Unknown Source"),
    )

    priority = _first(
        "priority", "priority_name", "priority_id",
        default="P1",
    )

    severity = _first("severity", "impact_name", default="")

    assigned_team = _first(
        "assigned_team", "team", "assignment_group", "assignee_team", "group",
        default="",
    )

    created_time = _first(
        "created_time", "created_at", "dateoccurred",
        "opened_at", "created", "timestamp",
        default="",
    )

    return {
        "ticket_id":     ticket_id,
        "client":        client,
        "summary":       summary,
        "source":        source_label,
        "priority":      priority,
        "severity":      severity,
        "assigned_team": assigned_team,
        "created_time":  created_time,
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
    if key in d:
        return d[key]
    camel = _to_camel(key)
    if camel in d:
        return d[camel]
    parts = key.split("_", 1)
    if len(parts) == 2:
        parent, child = parts
        if isinstance(d.get(parent), dict):
            return d[parent].get(child, default)
    return default


def _to_camel(snake: str) -> str:
    parts = snake.split("_")
    return parts[0] + "".join(p.capitalize() for p in parts[1:])
