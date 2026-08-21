"""Tests for the abuse limits on the public web endpoint.

The service accepts an anonymous upload and answers it by starting a JVM that
runs for minutes. That is an expensive thing to hand to the internet for free,
so the limits here are not decoration: without them one client can occupy both
worker slots indefinitely and fill the disk while doing it.

These test the mechanisms directly rather than through FastAPI, so they run
without the web extra installed.
"""

from __future__ import annotations

import pytest

from transposer.web.limits import RateLimiter, client_key


class FakeClock:
    """A clock the test drives, so a window can pass without waiting for it."""

    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


# -- the limiter -----------------------------------------------------------


def test_requests_under_the_limit_are_allowed():
    clock = FakeClock()
    limiter = RateLimiter(limit=3, window=60, clock=clock)
    assert all(limiter.check("1.2.3.4").allowed for _ in range(3))


def test_the_request_over_the_limit_is_refused():
    clock = FakeClock()
    limiter = RateLimiter(limit=3, window=60, clock=clock)
    for _ in range(3):
        limiter.check("1.2.3.4")

    verdict = limiter.check("1.2.3.4")
    assert not verdict.allowed
    assert verdict.retry_after > 0


def test_clients_are_counted_separately():
    clock = FakeClock()
    limiter = RateLimiter(limit=1, window=60, clock=clock)
    assert limiter.check("1.2.3.4").allowed
    assert not limiter.check("1.2.3.4").allowed
    assert limiter.check("5.6.7.8").allowed


def test_the_window_slides():
    clock = FakeClock()
    limiter = RateLimiter(limit=2, window=60, clock=clock)
    limiter.check("1.2.3.4")
    limiter.check("1.2.3.4")
    assert not limiter.check("1.2.3.4").allowed

    clock.advance(61)
    assert limiter.check("1.2.3.4").allowed


def test_retry_after_counts_down_to_the_oldest_request():
    clock = FakeClock()
    limiter = RateLimiter(limit=1, window=60, clock=clock)
    limiter.check("1.2.3.4")
    clock.advance(20)

    verdict = limiter.check("1.2.3.4")
    assert not verdict.allowed
    assert verdict.retry_after == pytest.approx(40, abs=1)


def test_a_refused_request_does_not_extend_the_ban():
    """Hammering the endpoint must not push the unblock time further out."""
    clock = FakeClock()
    limiter = RateLimiter(limit=1, window=60, clock=clock)
    limiter.check("1.2.3.4")

    clock.advance(30)
    for _ in range(10):
        limiter.check("1.2.3.4")

    clock.advance(31)
    assert limiter.check("1.2.3.4").allowed


def test_idle_clients_are_forgotten():
    """The limiter must not become a memory leak keyed by every IP that ever
    connected."""
    clock = FakeClock()
    limiter = RateLimiter(limit=5, window=60, clock=clock)
    for index in range(50):
        limiter.check(f"10.0.0.{index}")
    assert limiter.tracked() == 50

    clock.advance(3600)
    limiter.check("10.0.0.1")
    assert limiter.tracked() == 1


# -- identifying the client ------------------------------------------------


class FakeRequest:
    def __init__(self, peer: str | None, headers: dict | None = None) -> None:
        self.client = type("C", (), {"host": peer})() if peer else None
        self.headers = headers or {}


def test_the_peer_address_identifies_the_client():
    request = FakeRequest("203.0.113.7")
    assert client_key(request, trust_forwarded=False) == "203.0.113.7"


def test_a_forwarded_header_is_ignored_unless_trusted():
    """Otherwise anyone can set it and get a fresh quota per request."""
    request = FakeRequest("203.0.113.7", {"x-forwarded-for": "1.1.1.1"})
    assert client_key(request, trust_forwarded=False) == "203.0.113.7"


def test_a_forwarded_header_is_used_behind_a_proxy():
    """Behind Caddy every request arrives from localhost, so without this the
    whole internet shares one quota."""
    request = FakeRequest("127.0.0.1", {"x-forwarded-for": "203.0.113.7"})
    assert client_key(request, trust_forwarded=True) == "203.0.113.7"


def test_the_first_hop_of_a_forwarded_chain_wins():
    request = FakeRequest(
        "127.0.0.1", {"x-forwarded-for": "203.0.113.7, 10.0.0.1, 10.0.0.2"}
    )
    assert client_key(request, trust_forwarded=True) == "203.0.113.7"


def test_a_client_with_no_address_still_gets_a_key():
    assert client_key(FakeRequest(None), trust_forwarded=False)
