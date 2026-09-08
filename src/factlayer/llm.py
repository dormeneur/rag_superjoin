"""One client, several free providers.

Groq, Gemini, OpenRouter and Cerebras all speak the OpenAI wire format, so a
provider is a base URL and a key rather than a separate integration. They are tried
in order and the list is the retry policy: when a free tier says 429, the next one
answers.

Nothing here decides anything. The model returns language; the rest of the system
decides what it means.
"""

from __future__ import annotations

import json
import os
import re
import threading
import time
from collections import deque
from typing import Any, Callable

from . import config


class LLMUnavailable(RuntimeError):
    """No configured provider could answer."""


class LLMOutputError(ValueError):
    """A provider answered, but not with usable JSON."""


class RateLimiter:
    """A sliding-window pacer, one per provider.

    Free tiers publish a requests-per-minute allowance. Discovering it by being
    refused wastes both the call and the wait, so the client counts its own
    requests and waits out the window instead. A fixed window would let a burst
    through at the boundary; this one slides.
    """

    def __init__(self, per_minute: int, *, clock: Callable[[], float] = time.monotonic):
        self.per_minute = per_minute
        self._clock = clock
        self._sent: deque[float] = deque()
        self._lock = threading.Lock()

    def delay_for_next(self) -> float:
        """Seconds to wait before the next request would be inside the allowance."""
        if self.per_minute <= 0:
            return 0.0
        with self._lock:
            self._forget_old()
            if len(self._sent) < self.per_minute:
                return 0.0
            return max(0.0, 60.0 - (self._clock() - self._sent[0]))

    def record(self) -> None:
        with self._lock:
            self._sent.append(self._clock())

    def used(self) -> int:
        with self._lock:
            self._forget_old()
            return len(self._sent)

    def acquire(self) -> None:
        """Block until a request may be sent, then count it."""
        while (delay := self.delay_for_next()) > 0:
            time.sleep(min(delay, 5.0))
        self.record()

    def _forget_old(self) -> None:
        cutoff = self._clock() - 60.0
        while self._sent and self._sent[0] <= cutoff:
            self._sent.popleft()


_limiters: dict[str, RateLimiter] = {}
_limiters_lock = threading.Lock()


def limiter_for(provider: str) -> RateLimiter:
    with _limiters_lock:
        rate = config.requests_per_minute()
        limiter = _limiters.get(provider)
        if limiter is None or limiter.per_minute != rate:
            limiter = _limiters[provider] = RateLimiter(rate)
        return limiter


def complete_json(system: str, user: str, *, max_tokens: int = 4096) -> Any:
    """Ask the first provider that will answer, and parse its reply as JSON."""
    order = config.provider_order()
    if not order:
        raise LLMUnavailable("No LLM providers configured. See .env.example.")

    failures: list[str] = []
    for name in order:
        try:
            text = _call(name, system, user, max_tokens)
        except LLMUnavailable as exc:
            failures.append(f"{name}: {exc}")
            continue
        return _parse_json(text)

    raise LLMUnavailable("; ".join(failures) or "No provider answered.")


def available_providers() -> list[str]:
    """Provider names that are configured and could be used right now."""
    ready = []
    for name in config.provider_order():
        if name == "fake":
            if os.getenv("FACTLAYER_FAKE_RESPONSES"):
                ready.append(name)
        elif name in config.PROVIDERS and os.getenv(config.PROVIDERS[name].key_env):
            ready.append(name)
    return ready


# A provider can say "not now" in several ways, and each one is worth waiting out.
# Anything about the key, the model name or permissions is not: retrying that only
# hides the real problem.
_TRANSIENT = (
    "429", "500", "502", "503", "504",
    "rate", "quota", "overload", "unavailable", "timeout", "timed out",
    "temporarily", "high demand", "connection reset", "connection error",
)
_PERMANENT = ("401", "403", "404", "400", "invalid api key", "permission denied",
              "not found", "unsupported")


def is_transient(exc: Exception) -> bool:
    """Whether the same request is worth sending again in a moment."""
    message = str(exc).lower()
    if any(marker in message for marker in _PERMANENT):
        return False
    return any(marker in message for marker in _TRANSIENT)


# ------------------------------------------------------------------- the callers


def _call(name: str, system: str, user: str, max_tokens: int) -> str:
    if name == "fake":
        return _fake(user)

    provider = config.PROVIDERS.get(name)
    if provider is None:
        raise LLMUnavailable(f"unknown provider {name!r}")
    api_key = os.getenv(provider.key_env)
    if not api_key:
        raise LLMUnavailable(f"{provider.key_env} is not set")

    from openai import OpenAI  # imported lazily so tests never need the network stack

    client = OpenAI(api_key=api_key, base_url=provider.base_url, timeout=90.0)
    model = os.getenv(provider.model_env) or provider.default_model

    limiter = limiter_for(name)
    attempts = config.provider_attempts()
    for attempt in range(attempts):
        limiter.acquire()
        try:
            response = client.chat.completions.create(
                model=model,
                temperature=0,
                max_tokens=max_tokens,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
            )
            return response.choices[0].message.content or ""
        except Exception as exc:
            if attempt + 1 < attempts and is_transient(exc):
                time.sleep(2 * 2**attempt)  # 2s, 4s, 8s
                continue
            raise LLMUnavailable(str(exc)[:200]) from exc
    raise LLMUnavailable(f"{name} did not answer")


def _fake(user: str) -> str:
    """A scripted provider so the whole pipeline runs with no key.

    The script maps a substring of the prompt to the reply that should come back,
    which keeps it stable when prompts or batching change.
    """
    path = os.getenv("FACTLAYER_FAKE_RESPONSES")
    if not path or not os.path.exists(path):
        raise LLMUnavailable("FACTLAYER_FAKE_RESPONSES is not set")
    script = json.loads(open(path, encoding="utf-8").read())
    for needle, reply in script.items():
        if needle in user:
            return reply if isinstance(reply, str) else json.dumps(reply)
    return "[]"


# -------------------------------------------------------------------- json repair

_FENCE = re.compile(r"^\s*```(?:json)?\s*|\s*```\s*$", re.I)


def _parse_json(text: str) -> Any:
    """Models wrap JSON in prose and code fences, and run out of tokens mid-answer.
    Take the fences off, then the outermost array or object, then — if it was cut
    off — whatever complete objects it managed to finish."""
    candidate = _FENCE.sub("", (text or "").strip())
    try:
        return json.loads(candidate)
    except json.JSONDecodeError:
        pass

    for opener, closer in (("[", "]"), ("{", "}")):
        start, end = candidate.find(opener), candidate.rfind(closer)
        if start != -1 and end > start:
            try:
                return json.loads(candidate[start : end + 1])
            except json.JSONDecodeError:
                pass
        # An unclosed array must be salvaged before falling back to pulling a single
        # object out of it, or a truncated list collapses into its first entry.
        if opener == "[" and _is_array(candidate):
            if salvaged := _salvage_array(candidate):
                return salvaged

    if salvaged := _salvage_array(candidate):
        return salvaged

    raise LLMOutputError(f"Could not read JSON from the reply: {candidate[:200]!r}")


def _is_array(text: str) -> bool:
    opener, brace = text.find("["), text.find("{")
    return opener != -1 and (brace == -1 or opener < brace)


def _salvage_array(text: str) -> list | None:
    """Recover the complete objects from an array the model never closed.

    A page of forty table rows that stopped at row thirty-nine still holds
    thirty-nine facts, and grounding will check each of them anyway.
    """
    start = text.find("[")
    if start == -1:
        return None

    objects, depth, obj_start, in_string, escaped = [], 0, None, False, False
    for index in range(start + 1, len(text)):
        char = text[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            if depth == 0:
                obj_start = index
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0 and obj_start is not None:
                try:
                    objects.append(json.loads(text[obj_start : index + 1]))
                except json.JSONDecodeError:
                    pass
                obj_start = None
    return objects or None
