"""Staying inside a free tier.

Free providers publish a requests-per-minute limit. Hitting it wastes the call and
the wait, so the client paces itself instead of discovering the limit by being
refused. The limiter is tested against a fake clock: a test that really slept would
be slow and flaky, and would not tell us anything more.
"""

from __future__ import annotations

import pytest

from factlayer.llm import RateLimiter


class FakeClock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


@pytest.fixture
def clock():
    return FakeClock()


def test_requests_under_the_limit_are_not_delayed(clock):
    limiter = RateLimiter(per_minute=5, clock=clock)
    for _ in range(5):
        assert limiter.delay_for_next() == 0
        limiter.record()


def test_the_request_over_the_limit_waits_for_the_window_to_move(clock):
    limiter = RateLimiter(per_minute=5, clock=clock)
    for _ in range(5):
        limiter.record()
    assert limiter.delay_for_next() == pytest.approx(60.0)


def test_the_window_slides_rather_than_resetting(clock):
    """A fixed window lets six requests through at the boundary. A sliding one does not."""
    limiter = RateLimiter(per_minute=5, clock=clock)
    for _ in range(5):
        limiter.record()
        clock.advance(1)
    # 5 requests sent between t=0 and t=4; at t=5 the oldest is 5s old.
    assert limiter.delay_for_next() == pytest.approx(55.0)
    clock.advance(56)
    assert limiter.delay_for_next() == 0


def test_a_limit_of_zero_disables_pacing(clock):
    limiter = RateLimiter(per_minute=0, clock=clock)
    for _ in range(100):
        limiter.record()
    assert limiter.delay_for_next() == 0


def test_the_limiter_is_safe_to_share_between_threads(clock):
    """Pages are read in parallel, so every worker consults the same limiter."""
    from concurrent.futures import ThreadPoolExecutor

    limiter = RateLimiter(per_minute=1000, clock=clock)
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(lambda _: limiter.record(), range(400)))
    assert limiter.used() == 400


# --------------------------------------------------- what is worth retrying

from factlayer.llm import is_transient


@pytest.mark.parametrize(
    "message",
    [
        "Error code: 429 - rate limit exceeded",
        "Error code: 503 - This model is currently experiencing high demand.",
        "Error code: 500 - internal error",
        "Error code: 502 - bad gateway",
        "The model is overloaded. Please try again later.",
        "status: UNAVAILABLE",
        "Request timed out.",
        "Connection reset by peer",
    ],
)
def test_busy_and_flaky_providers_are_retried(message):
    """A free tier says no in several different ways. Treating only 429 as transient
    throws away a page for what was a two-second hiccup."""
    assert is_transient(Exception(message))


@pytest.mark.parametrize(
    "message",
    [
        "Error code: 401 - invalid api key",
        "Error code: 400 - model not found",
        "Error code: 404 - no such model",
        "Error code: 403 - permission denied",
    ],
)
def test_configuration_errors_are_not_retried(message):
    """Retrying a bad key just wastes the wait and hides the real problem."""
    assert not is_transient(Exception(message))
