"""Per-process request caps, not a spend ceiling. State resets on restart."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from ipaddress import ip_address
import logging
import math
import os
from threading import Lock
import time
from typing import Callable

from fastapi import HTTPException, Request

logger = logging.getLogger(__name__)
WINDOW_SECONDS = 600
MAX_CLIENTS = 4096
SHARED_CLIENT = "unknown-client"


def client_key(request: Request) -> str:
    """Leftmost XFF IP; any invalid chain, duplicate or absent header shares a bucket.

    This is an untrusted abuse signal, not authenticated identity. Never use it
    for ownership. Rotating/spoofing it cannot evade the process-wide daily cap.
    """
    headers = request.headers.getlist("x-forwarded-for")
    if len(headers) != 1 or len(headers[0]) > 4096:
        return SHARED_CLIENT
    parts = headers[0].split(",")
    if len(parts) > 32:
        return SHARED_CLIENT
    try:
        # Reject ports, zone IDs, empty entries and non-IP tokens anywhere.
        if any("%" in part for part in parts):
            return SHARED_CLIENT
        addresses = [ip_address(part.strip()) for part in parts]
    except ValueError:
        return SHARED_CLIENT
    first = addresses[0]
    return str(getattr(first, "ipv4_mapped", None) or first)


def _limit(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        value = int(raw)
        if 1 <= value <= 1_000_000:
            return value
    except ValueError:
        pass
    # Do not echo arbitrary environment contents into logs.
    logger.warning("Invalid %s; using default %d (expected 1..1000000)", name, default)
    return default


@dataclass
class _Window:
    expires: float
    count: int = 0


class ProviderUsage:
    """Fixed client windows start at first counted request, independently per route.

    check() is advisory and never reserves usage. consume() atomically rechecks
    both limits and increments both counters, after validation and before work.
    No refunds for cache hits, unavailable providers or failed provider attempts.
    """

    def __init__(self, label: str, client_limit: int, daily_limit: int, *,
                 monotonic: Callable[[], float] = time.monotonic,
                 utcnow: Callable[[], datetime] = lambda: datetime.now(timezone.utc)):
        self.label = label
        self.client_limit = client_limit
        self.daily_limit = daily_limit
        self._monotonic = monotonic
        self._utcnow = utcnow
        self._lock = Lock()
        self._clients: dict[str, _Window] = {}
        self._day = None
        self._daily_count = 0

    def check(self, key: str) -> None:
        self._admit(key, consume=False)

    def consume(self, key: str) -> None:
        self._admit(key, consume=True)

    def _reject(self, message: str, seconds: float) -> None:
        raise HTTPException(429, detail=f"{self.label}: {message}",
                            headers={"Retry-After": str(max(1, math.ceil(seconds)))})

    def _admit(self, key: str, *, consume: bool) -> None:
        with self._lock:
            now = self._monotonic()
            utc = self._utcnow().astimezone(timezone.utc)
            day = utc.date()
            # A backwards wall-clock correction must not replenish today's cap.
            if self._day is None or day > self._day:
                self._day = day
                self._daily_count = 0
            # Bounded scan, no background worker and no state for rejected keys.
            self._clients = {k: v for k, v in self._clients.items() if v.expires > now}
            if self._daily_count >= self.daily_limit:
                midnight = datetime.combine(self._day + timedelta(days=1),
                                            datetime.min.time(), tzinfo=timezone.utc)
                self._reject("Daily usage limit reached. Try again after 00:00 UTC.",
                             (midnight - utc).total_seconds())
            window = self._clients.get(key)
            if window is not None and window.count >= self.client_limit:
                self._reject("Usage limit reached. Please try again shortly.", window.expires - now)
            if window is None and len(self._clients) >= MAX_CLIENTS:
                # Fail closed instead of evicting an active user's counter.
                self._reject("Usage limiter is busy. Please try again shortly.",
                             min(v.expires for v in self._clients.values()) - now)
            if consume:
                if window is None:
                    window = self._clients[key] = _Window(now + WINDOW_SECONDS)
                window.count += 1
                self._daily_count += 1


photo_usage = ProviderUsage("Photo analysis", _limit("PHOTO_REHAB_CLIENT_LIMIT", 3),
                            _limit("PHOTO_REHAB_DAILY_LIMIT", 20))
address_usage = ProviderUsage("Address lookup", _limit("RENTCAST_CLIENT_LIMIT", 10),
                              _limit("RENTCAST_DAILY_LIMIT", 30))
