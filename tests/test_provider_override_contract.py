"""Bringing your own key.

A caller who supplies their own provider and key gets it tried first, ahead of
whatever the server itself has configured — and if it fails, the server's own
providers are still tried afterwards rather than giving up outright. The key
itself never appears in a stored fact, a log line, or a raised exception's
message.
"""

from __future__ import annotations

import pytest

from factlayer import llm
from factlayer.llm import LLMUnavailable, ProviderOverride


def test_the_override_is_tried_first(monkeypatch):
    calls = []

    def fake_call(name, system, user, max_tokens, *, api_key=None):
        calls.append((name, api_key))
        return "[]"

    monkeypatch.setattr(llm, "_call", fake_call)
    monkeypatch.setenv("LLM_PROVIDER_ORDER", "fake")

    llm.complete_json("sys", "usr", provider_override=ProviderOverride("groq", "secret-key"))

    assert calls[0] == ("groq", "secret-key")


def test_the_configured_order_still_follows_the_override(monkeypatch):
    calls = []

    def fake_call(name, system, user, max_tokens, *, api_key=None):
        calls.append((name, api_key))
        if name == "fake":
            return "[]"
        raise LLMUnavailable("not this one")

    monkeypatch.setattr(llm, "_call", fake_call)
    monkeypatch.setenv("LLM_PROVIDER_ORDER", "gemini,fake")

    llm.complete_json("sys", "usr", provider_override=ProviderOverride("groq", "secret-key"))

    assert calls == [("groq", "secret-key"), ("gemini", None), ("fake", None)]


def test_a_failing_override_falls_back_to_the_configured_providers(monkeypatch):
    def fake_call(name, system, user, max_tokens, *, api_key=None):
        if api_key is not None:
            raise LLMUnavailable("their key was refused")
        return '[{"ok": true}]'

    monkeypatch.setattr(llm, "_call", fake_call)
    monkeypatch.setenv("LLM_PROVIDER_ORDER", "fake")

    result = llm.complete_json(
        "sys", "usr", provider_override=ProviderOverride("groq", "bad-key")
    )
    assert result == [{"ok": True}]


def test_the_key_never_appears_in_the_failure_message(monkeypatch):
    def fake_call(name, system, user, max_tokens, *, api_key=None):
        raise LLMUnavailable("refused")

    monkeypatch.setattr(llm, "_call", fake_call)
    monkeypatch.setenv("LLM_PROVIDER_ORDER", "")

    with pytest.raises(LLMUnavailable) as excinfo:
        llm.complete_json(
            "sys", "usr",
            provider_override=ProviderOverride("groq", "sk-do-not-leak-this"),
        )
    assert "sk-do-not-leak-this" not in str(excinfo.value)
    assert "your key on groq" in str(excinfo.value)


def test_no_override_behaves_exactly_as_before(monkeypatch):
    """The plumbing added for bring-your-own-key must not change the plain path."""
    calls = []

    def fake_call(name, system, user, max_tokens, *, api_key=None):
        calls.append((name, api_key))
        if name == "fake":
            return "[]"
        raise LLMUnavailable("not this one")

    monkeypatch.setattr(llm, "_call", fake_call)
    monkeypatch.setenv("LLM_PROVIDER_ORDER", "gemini,fake")

    llm.complete_json("sys", "usr")

    assert calls == [("gemini", None), ("fake", None)]


def test_fake_provider_ignores_the_override_key(fake_llm):
    """The scripted provider used throughout the suite must stay reachable through
    the override path too, with the key simply ignored rather than checked."""
    fake_llm({"usr": [{"ok": True}]})
    result = llm.complete_json(
        "sys", "usr", provider_override=ProviderOverride("fake", "anything-at-all")
    )
    assert result == [{"ok": True}]


def test_fingerprint_is_stable_and_distinguishes_keys():
    from factlayer.llm import _fingerprint

    assert _fingerprint("key-a") == _fingerprint("key-a")
    assert _fingerprint("key-a") != _fingerprint("key-b")
    assert "key-a" not in _fingerprint("key-a")
