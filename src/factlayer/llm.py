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
import time
from typing import Any

from . import config


class LLMUnavailable(RuntimeError):
    """No configured provider could answer."""


class LLMOutputError(ValueError):
    """A provider answered, but not with usable JSON."""


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

    for attempt in range(2):
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
            transient = "429" in str(exc) or "rate" in str(exc).lower()
            if transient and attempt == 0:
                time.sleep(2)
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
    """Models wrap JSON in prose and code fences. Take the fences off, and if that
    is not enough, take the outermost array or object."""
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
                continue

    raise LLMOutputError(f"Could not read JSON from the reply: {candidate[:200]!r}")
