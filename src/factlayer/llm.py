"""One client, several free providers.

Groq, Gemini, OpenRouter and Cerebras all speak the OpenAI wire format, so a
provider is a base URL and a key rather than a separate integration. They are tried
in order and the list is the retry policy: when a free tier says 429, the next one
answers.

Nothing here decides anything. The model returns language; the rest of the system
decides what it means.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import time
from collections import deque
from typing import Any, Callable, NamedTuple

from . import config


class LLMUnavailable(RuntimeError):
    """No configured provider could answer."""


class ProviderOverride(NamedTuple):
    """A caller's own key for one request, tried ahead of the server's own.

    Never written to disk, never logged, never echoed back — it lives only for the
    duration of the call that carries it.
    """

    provider: str
    api_key: str


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


def models_by_availability(provider: str) -> list[str]:
    """The provider's models, least busy first.

    Quotas are per model, so a second model is a second allowance — but only if the
    client goes there while the first is full instead of waiting its turn. Ties keep
    the configured order, so the preferred model still wins when nothing is busy.
    """
    models = config.models_for(provider)
    return sorted(
        models,
        key=lambda model: (limiter_for(f"{provider}:{model}").delay_for_next(),
                           models.index(model)),
    )


def complete_json(
    system: str, user: str, *, max_tokens: int = 4096,
    provider_override: ProviderOverride | None = None,
) -> Any:
    """Ask the first provider that will answer, and parse its reply as JSON.

    A caller's own key goes first — they went to the trouble of providing it — and
    the server's configured providers are still tried after it, so one bad key
    degrades to the normal fallback chain rather than failing outright.
    """
    attempts: list[tuple[str, str | None]] = []
    if provider_override is not None:
        attempts.append((provider_override.provider, provider_override.api_key))
    attempts += [(name, None) for name in config.provider_order()]

    if not attempts:
        raise LLMUnavailable("No LLM providers configured. See .env.example.")

    failures: list[str] = []
    for name, key in attempts:
        try:
            text = _call(name, system, user, max_tokens, api_key=key)
        except LLMUnavailable as exc:
            failures.append(f"{'your key on ' if key else ''}{name}: {exc}")
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


_RETRY_HINT = re.compile(r"retry\s+in\s+([\d.]+)\s*s|retryDelay['\"]?\s*:\s*['\"]?([\d.]+)s", re.I)
MAX_RETRY_WAIT = 120.0


def retry_after(exc: Exception) -> float | None:
    """How long the provider itself asked us to wait, if it said.

    A per-minute quota wants roughly a minute; guessing two seconds spends another
    request to be refused again. Capped so one unlucky answer cannot stall a run.
    """
    match = _RETRY_HINT.search(str(exc))
    if not match:
        return None
    seconds = float(match.group(1) or match.group(2))
    return min(seconds, MAX_RETRY_WAIT)


def is_transient(exc: Exception) -> bool:
    """Whether the same request is worth sending again in a moment."""
    message = str(exc).lower()
    if any(marker in message for marker in _PERMANENT):
        return False
    return any(marker in message for marker in _TRANSIENT)


# ------------------------------------------------------------------- the callers


def _call(name: str, system: str, user: str, max_tokens: int, *, api_key: str | None = None) -> str:
    if name == "fake":
        return _fake(user)

    provider = config.PROVIDERS.get(name)
    if provider is None:
        raise LLMUnavailable(f"unknown provider {name!r}")
    key = api_key or os.getenv(provider.key_env)
    if not key:
        raise LLMUnavailable(f"{provider.key_env} is not set")

    from openai import OpenAI  # imported lazily so tests never need the network stack

    # 180s, not 90: CPU-only local inference (Ollama on a laptop with no GPU) can
    # genuinely take longer for an 8192-token extraction call than any of the
    # hosted providers ever do, and the extra headroom costs nothing when a call
    # actually is fast.
    client = OpenAI(api_key=key, base_url=provider.base_url, timeout=180.0)
    attempts = config.provider_attempts()
    problems: list[str] = []

    # A caller's own key is a different account with its own quota. It gets its own
    # pacing bucket and only the provider's documented default model — our env's
    # model list is our configuration, not theirs, and does not apply to their key.
    models = [provider.default_model] if api_key else models_by_availability(name)

    # Quotas are per model, so each model listed for this key is its own allowance.
    for model in models:
        bucket = f"{name}:byok:{_fingerprint(key)}" if api_key else f"{name}:{model}"
        limiter = limiter_for(bucket)
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
                    # The provider usually says how long it wants; believe it.
                    time.sleep(retry_after(exc) or 2 * 2**attempt)
                    continue
                problems.append(f"{model}: {str(exc)[:120]}")
                break

    raise LLMUnavailable("; ".join(problems) or f"{name} did not answer")


def _fingerprint(api_key: str) -> str:
    """A one-way tag for a key, just long enough to keep rate-limit buckets apart —
    never the key itself, so it is safe to use even in a bucket name."""
    return hashlib.sha256(api_key.encode()).hexdigest()[:10]


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
