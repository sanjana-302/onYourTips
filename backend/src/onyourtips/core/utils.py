"""Small dependency-free helpers shared across layers."""

from __future__ import annotations

import calendar
import time
from datetime import datetime, timezone

from dateutil import parser as dateparser


def parse_datetime(value: object) -> datetime | None:
    """Parse epoch ints, ISO strings, RFC-822 strings, struct_time or datetime into aware UTC."""
    if value is None or value == "":
        return None
    try:
        if isinstance(value, datetime):
            dt = value
        elif isinstance(value, (int, float)):
            dt = datetime.fromtimestamp(float(value), tz=timezone.utc)
        elif isinstance(value, time.struct_time):  # feedparser *_parsed fields are UTC
            dt = datetime.fromtimestamp(calendar.timegm(value), tz=timezone.utc)
        elif isinstance(value, str):
            dt = dateparser.parse(value)
        else:
            return None
    except (ValueError, OverflowError, TypeError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).replace(microsecond=0)


# Error text that means "this machine can't reach the internet" rather than "this site is broken":
# DNS lookups failing (macOS, Linux, Windows wording) or no network route at all.
NETWORK_ERROR_MARKERS = (
    "nodename nor servname",
    "name or service not known",
    "temporary failure in name resolution",
    "getaddrinfo failed",
    "no address associated with hostname",
    "network is unreachable",
    "internet connection appears to be offline",
)


def is_network_error(message: str | None) -> bool:
    """True if an error message points at the local network (DNS / no route), not the site."""
    low = (message or "").lower()
    return any(m in low for m in NETWORK_ERROR_MARKERS)


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def unique(seq: list[str]) -> list[str]:
    """Order-preserving, case-insensitive de-duplication of strings."""
    seen: set[str] = set()
    out: list[str] = []
    for s in seq:
        s = s.strip()
        if s and s.lower() not in seen:
            seen.add(s.lower())
            out.append(s)
    return out
