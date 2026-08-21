"""Abuse limits for the public endpoint.

The service takes an anonymous upload and answers it by starting a JVM that runs
for minutes and is allowed several gigabytes of heap. Handing that to the
internet unmetered is not a theoretical risk: two clients can hold both worker
slots indefinitely, and every upload that reaches disk stays there.

Nothing here is a substitute for putting the service behind authentication if it
holds anything private. These are the limits that keep an open instance from
being trivially expensive to abuse.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from time import monotonic

#: How long an idle client is remembered. Without this the limiter is a
#: dictionary keyed by every address that ever connected.
FORGET_AFTER = 4


@dataclass(frozen=True)
class Verdict:
    """Whether a request may proceed, and when to try again if not."""

    allowed: bool
    retry_after: float = 0.0

    def __bool__(self) -> bool:  # pragma: no cover - trivial
        return self.allowed


class RateLimiter:
    """A sliding-window limiter, counted per client.

    A sliding window rather than a fixed one because a fixed window lets a
    client spend its whole quota at 11:59 and again at 12:00. Refused requests
    are deliberately *not* recorded: counting them would push the unblock time
    further out every time an impatient client retried, which turns a rate limit
    into an escalating ban nobody intended.
    """

    def __init__(self, limit: int, window: float, clock=monotonic) -> None:
        self.limit = max(1, int(limit))
        self.window = float(window)
        self._clock = clock
        self._seen: dict[str, deque[float]] = {}

    def check(self, key: str) -> Verdict:
        now = self._clock()
        self._forget(now)

        stamps = self._seen.setdefault(key, deque())
        while stamps and now - stamps[0] >= self.window:
            stamps.popleft()

        if len(stamps) >= self.limit:
            return Verdict(False, retry_after=max(0.0, self.window - (now - stamps[0])))

        stamps.append(now)
        return Verdict(True)

    def tracked(self) -> int:
        """How many clients are currently remembered. For tests and metrics."""
        return len(self._seen)

    def _forget(self, now: float) -> None:
        stale = self.window * FORGET_AFTER
        for key in [
            key
            for key, stamps in self._seen.items()
            if not stamps or now - stamps[-1] > stale
        ]:
            del self._seen[key]


def client_key(request, trust_forwarded: bool) -> str:
    """Identify the client a request came from.

    ``X-Forwarded-For`` is only honoured when the deployment says a proxy is in
    front, because any client can send that header: trusting it unconditionally
    hands out a fresh quota per request to anyone who reads this file. Behind
    Caddy the opposite is true -- every request arrives from localhost, so
    without it the whole internet shares one quota.
    """
    if trust_forwarded:
        forwarded = request.headers.get("x-forwarded-for", "")
        first = forwarded.split(",")[0].strip()
        if first:
            return first

    client = getattr(request, "client", None)
    host = getattr(client, "host", None) if client else None
    return host or "unknown"
