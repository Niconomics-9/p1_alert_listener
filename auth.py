"""
auth.py – Request authentication helpers.

Currently implements shared-secret via X-Webhook-Token header.
To add IP allowlisting or JWT later, extend validate_request().

Reverse proxy note:
  If you place this behind nginx/Caddy, you can strip the auth header
  at the proxy and rely on network-level security instead. In that case
  set AUTH_ENABLED=false in settings and let the proxy enforce access.
"""
from __future__ import annotations

import logging

from flask import Request

log = logging.getLogger("p1alert.auth")


def validate_request(request: Request, settings: dict) -> tuple[bool, str]:
    """
    Return (ok: bool, reason: str).

    settings dict expected keys:
      auth_enabled   : bool
      shared_secret  : str
    """
    if not settings.get("auth_enabled", False):
        return True, "auth disabled"

    secret = settings.get("shared_secret", "").strip()
    if not secret:
        # Auth enabled but no secret configured – warn and allow through
        log.warning("AUTH: auth_enabled=True but shared_secret is empty – allowing request")
        return True, "no secret configured"

    token = request.headers.get("X-Webhook-Token", "").strip()
    if not token:
        log.warning("AUTH FAIL: missing X-Webhook-Token header")
        return False, "missing token"

    if token != secret:
        log.warning("AUTH FAIL: token mismatch")
        return False, "invalid token"

    log.debug("AUTH OK")
    return True, "ok"
