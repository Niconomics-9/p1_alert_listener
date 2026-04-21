"""
auth.py – Request authentication helpers.

Two layers of protection:
  1. IP allowlist  – only known Halo / Datto source IPs accepted
  2. Shared secret – X-Webhook-Token header must match configured value

Both must pass when enabled. IP check runs first so bad actors are
dropped before we even look at the token.
"""
from __future__ import annotations

import hmac
import ipaddress
import logging

from flask import Request

log = logging.getLogger("p1alert.auth")

# ---------------------------------------------------------------------------
# Known source IP ranges
# Cloudflare forwards the real sender IP in X-Forwarded-For.
# These are the egress ranges for Halo PSA cloud and Datto RMM.
# Update if your vendor publishes new ranges.
# ---------------------------------------------------------------------------

# Halo PSA – add their published egress IPs here when known.
# Contact Halo support or check their network docs for current ranges.
HALO_ALLOWED_IPS: list[str] = [
    # "203.0.113.10",   # example – replace with real Halo egress IPs
]

# Datto RMM – add their published egress IPs here when known.
# See: Datto support docs → Webhook source IPs
DATTO_ALLOWED_IPS: list[str] = [
    # "198.51.100.0/24",  # example – replace with real Datto egress ranges
]

# Cloudflare tunnel proxy IPs – requests arrive via Cloudflare edge nodes.
# Full list: https://www.cloudflare.com/ips/
CLOUDFLARE_IPV4: list[str] = [
    "173.245.48.0/20",
    "103.21.244.0/22",
    "103.22.200.0/22",
    "103.31.4.0/22",
    "141.101.64.0/18",
    "108.162.192.0/18",
    "190.93.240.0/20",
    "188.114.96.0/20",
    "197.234.240.0/22",
    "198.41.128.0/17",
    "162.158.0.0/15",
    "104.16.0.0/13",
    "104.24.0.0/14",
    "172.64.0.0/13",
    "131.0.72.0/22",
]

CLOUDFLARE_IPV6: list[str] = [
    "2400:cb00::/32",
    "2606:4700::/32",
    "2803:f800::/32",
    "2405:b500::/32",
    "2405:8100::/32",
    "2a06:98c0::/29",
    "2c0f:f248::/32",
]

_CF_NETWORKS = [
    ipaddress.ip_network(r) for r in CLOUDFLARE_IPV4 + CLOUDFLARE_IPV6
]


def validate_request(request: Request, settings: dict) -> tuple[bool, str]:
    """
    Return (ok: bool, reason: str).

    Runs two checks in order:
      1. IP allowlist (if ip_allowlist_enabled)
      2. Shared secret (if auth_enabled)
    """
    # ── Layer 1: IP allowlist ─────────────────────────────────────────────
    if settings.get("ip_allowlist_enabled", False):
        ok, reason = _check_ip(request, settings)
        if not ok:
            return False, reason

    # ── Layer 2: Shared secret ────────────────────────────────────────────
    if not settings.get("auth_enabled", False):
        return True, "auth disabled"

    secret = settings.get("shared_secret", "").strip()
    if not secret:
        log.warning("AUTH: auth_enabled=True but shared_secret is empty – allowing request")
        return True, "no secret configured"

    token = request.headers.get("X-Webhook-Token", "").strip()
    if not token:
        log.warning("AUTH FAIL: missing X-Webhook-Token header")
        return False, "missing token"

    # Timing-safe comparison prevents timing attacks
    if not hmac.compare_digest(token.encode(), secret.encode()):
        log.warning("AUTH FAIL: token mismatch")
        return False, "invalid token"

    log.debug("AUTH OK")
    return True, "ok"


# ---------------------------------------------------------------------------
# IP helpers
# ---------------------------------------------------------------------------

def _check_ip(request: Request, settings: dict) -> tuple[bool, str]:
    """
    Verify the request originates from an allowed IP.

    When running behind Cloudflare tunnel, the real sender IP is in
    X-Forwarded-For. We check both the direct IP and the forwarded IP.
    """
    # Build the full allowed set: Cloudflare ranges + configured vendor IPs
    allowed_cidrs = list(CLOUDFLARE_IPV4) + list(CLOUDFLARE_IPV6)
    allowed_cidrs += settings.get("allowed_ips", [])

    # Direct connection IP
    remote_ip = request.remote_addr or ""

    # Forwarded IP (Cloudflare sets this to the real sender)
    forwarded_for = request.headers.get("X-Forwarded-For", "").split(",")[0].strip()

    for ip_str in filter(None, [remote_ip, forwarded_for]):
        if _ip_allowed(ip_str, allowed_cidrs):
            log.debug(f"IP OK: {ip_str}")
            return True, "ok"

    log.warning(f"IP BLOCKED: remote={remote_ip!r} forwarded={forwarded_for!r}")
    return False, f"IP not allowed: {forwarded_for or remote_ip}"


def _ip_allowed(ip_str: str, cidrs: list[str]) -> bool:
    """Return True if ip_str falls within any of the CIDR ranges."""
    try:
        addr = ipaddress.ip_address(ip_str)
    except ValueError:
        return False
    for cidr in cidrs:
        try:
            if addr in ipaddress.ip_network(cidr, strict=False):
                return True
        except ValueError:
            continue
    return False
