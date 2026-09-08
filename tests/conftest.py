"""Shared fixtures.

Everything here runs offline. No test may reach a real LLM provider or the network.
"""

from __future__ import annotations

import json
from datetime import date

import pytest


# ------------------------------------------------------------------------- fake LLM


@pytest.fixture
def fake_llm(tmp_path, monkeypatch):
    """Point the LLM layer at the scripted 'fake' provider.

    The mapping is {substring of the prompt: response}. The fake returns the response
    for the first key found in the prompt, and an empty list when nothing matches.
    This keeps tests stable when segmentation or batching changes.
    """

    def _install(mapping: dict[str, object]):
        path = tmp_path / "fake_responses.json"
        path.write_text(json.dumps(mapping))
        monkeypatch.setenv("LLM_PROVIDER_ORDER", "fake")
        monkeypatch.setenv("FACTLAYER_FAKE_RESPONSES", str(path))
        return path

    return _install


@pytest.fixture(autouse=True)
def no_real_providers(monkeypatch):
    """Guard: a test that forgets to install the fake must not fall through to a
    real provider if the developer happens to have keys in their shell."""
    for key in ("GROQ_API_KEY", "GEMINI_API_KEY", "OPENROUTER_API_KEY", "CEREBRAS_API_KEY"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("LLM_PROVIDER_ORDER", "fake")


@pytest.fixture(autouse=True)
def isolated_storage(tmp_path, monkeypatch):
    monkeypatch.setenv("FACTLAYER_DB", str(tmp_path / "test.db"))
    monkeypatch.setenv("FACTLAYER_UPLOADS", str(tmp_path / "uploads"))


# ----------------------------------------------------------------------------- PDFs


@pytest.fixture
def pdf_bytes():
    """Render pages of plain text into a real PDF, so tests exercise the actual parser."""
    from fpdf import FPDF

    def _build(pages: list[str]) -> bytes:
        pdf = FPDF()
        pdf.set_auto_page_break(auto=False)
        for text in pages:
            pdf.add_page()
            pdf.set_font("Helvetica", size=11)
            pdf.multi_cell(0, 7, text)
        return bytes(pdf.output())

    return _build
