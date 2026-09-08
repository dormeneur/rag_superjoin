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


# ------------------------------------- the provider knows better than the client

from factlayer.llm import retry_after


def test_a_stated_retry_delay_is_used():
    """Gemini answers a 429 with exactly how long to wait. Guessing 2s when it said
    52s just burns another request and another refusal."""
    message = ("Error code: 429 - Quota exceeded ... Please retry in 52.424603739s. "
               "'retryDelay': '52s'")
    assert retry_after(Exception(message)) == pytest.approx(52.4246, rel=1e-4)


def test_a_retry_delay_field_is_used_when_there_is_no_prose():
    assert retry_after(Exception("{'retryDelay': '7s'}")) == pytest.approx(7.0)


def test_no_stated_delay_returns_nothing():
    assert retry_after(Exception("Error code: 503 - high demand")) is None


def test_an_absurd_delay_is_capped():
    """A provider asking for an hour must not stall the whole run."""
    assert retry_after(Exception("Please retry in 3600s.")) == 120.0


# -------------------------------------------------- more than one model per key

from factlayer import config


def test_a_provider_can_be_given_several_models(monkeypatch):
    """Free quotas are per model, so a second model on the same key is a second
    allowance and a fallback when the first is busy."""
    monkeypatch.setenv("GEMINI_MODEL", "gemini-flash-lite-latest, gemini-3.8-flash")
    assert config.models_for("gemini") == ["gemini-flash-lite-latest", "gemini-3.8-flash"]


def test_a_provider_falls_back_to_its_default_model(monkeypatch):
    monkeypatch.delenv("GEMINI_MODEL", raising=False)
    assert config.models_for("gemini") == [config.PROVIDERS["gemini"].default_model]


def test_the_least_busy_model_is_tried_first(monkeypatch):
    """Two models on one key are two quotas, but only if the client moves to the
    second when the first is full. Trying them in a fixed order means the client
    sits waiting on model one while model two is idle."""
    from factlayer.llm import limiter_for, models_by_availability

    monkeypatch.setenv("GEMINI_MODEL", "busy-model, idle-model")
    monkeypatch.setenv("FACTLAYER_RPM", "2")

    busy = limiter_for("gemini:busy-model")
    for _ in range(2):
        busy.record()

    assert models_by_availability("gemini")[0] == "idle-model"


def test_model_order_is_kept_when_neither_is_busy(monkeypatch):
    from factlayer.llm import models_by_availability

    monkeypatch.setenv("GEMINI_MODEL", "first-model, second-model")
    monkeypatch.setenv("FACTLAYER_RPM", "50")
    assert models_by_availability("gemini") == ["first-model", "second-model"]
