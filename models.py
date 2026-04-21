"""
models.py – Data models for P1 Alert Listener.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


# ---------------------------------------------------------------------------
# Alert
# ---------------------------------------------------------------------------

@dataclass
class Alert:
    """Represents one incoming alert event."""

    # Unique internal ID (UUID)
    id: str = field(default_factory=lambda: str(uuid.uuid4()))

    # Extracted fields – populated by the payload parser
    ticket_id: str = "Unknown Ticket"
    client: str = "Unknown Client"
    summary: str = "No summary provided"
    source: str = "Unknown Source"
    priority: str = "P1"
    severity: str = ""
    assigned_team: str = ""
    created_time: str = ""

    # Timestamps
    received_time: str = field(
        default_factory=lambda: datetime.now().isoformat()
    )

    # Original payload (kept for "Copy as JSON" / debugging)
    raw_payload: dict = field(default_factory=dict)

    # State flags
    acknowledged: bool = False
    silenced: bool = False

    # Key used for deduplication logic
    dedupe_key: str = ""

    # ------------------------------------------------------------------ helpers

    def display_received(self) -> str:
        """Human-readable received timestamp."""
        try:
            dt = datetime.fromisoformat(self.received_time)
            return dt.strftime("%Y-%m-%d %H:%M:%S")
        except Exception:
            return self.received_time

    def short_summary(self, max_len: int = 60) -> str:
        s = self.summary or "No summary"
        return s if len(s) <= max_len else s[: max_len - 3] + "..."

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "ticket_id": self.ticket_id,
            "client": self.client,
            "summary": self.summary,
            "source": self.source,
            "priority": self.priority,
            "severity": self.severity,
            "assigned_team": self.assigned_team,
            "created_time": self.created_time,
            "received_time": self.received_time,
            "acknowledged": self.acknowledged,
            "silenced": self.silenced,
            "dedupe_key": self.dedupe_key,
            # Omit raw_payload from history to keep files small.
            # Add "raw_payload": self.raw_payload if you want full payloads.
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Alert:
        obj = cls()
        for k, v in d.items():
            if hasattr(obj, k):
                setattr(obj, k, v)
        return obj


# ---------------------------------------------------------------------------
# Queue message types passed from listener → GUI via queue.Queue
# ---------------------------------------------------------------------------

@dataclass
class QueueMsg:
    """Lightweight message passed between threads."""

    kind: str          # "alert" | "log" | "listener_status" | "error"
    data: Any = None   # Alert, str, bool, etc.
