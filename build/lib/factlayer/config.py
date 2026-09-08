"""Settings and the provider table.

Every provider here speaks the OpenAI wire format, so switching between them is a
base URL and a key — not a different client. Values are read at call time rather
than import time so tests and a running server can change them.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()


@dataclass(frozen=True)
class Provider:
    name: str
    base_url: str
    key_env: str
    model_env: str
    default_model: str


PROVIDERS: dict[str, Provider] = {
    p.name: p
    for p in [
        Provider("groq", "https://api.groq.com/openai/v1",
                 "GROQ_API_KEY", "GROQ_MODEL", "llama-3.3-70b-versatile"),
        Provider("gemini", "https://generativelanguage.googleapis.com/v1beta/openai/",
                 "GEMINI_API_KEY", "GEMINI_MODEL", "gemini-2.0-flash"),
        Provider("openrouter", "https://openrouter.ai/api/v1",
                 "OPENROUTER_API_KEY", "OPENROUTER_MODEL",
                 "meta-llama/llama-3.3-70b-instruct:free"),
        Provider("cerebras", "https://api.cerebras.ai/v1",
                 "CEREBRAS_API_KEY", "CEREBRAS_MODEL", "llama-3.3-70b"),
    ]
}

DEFAULT_ORDER = "groq,gemini,openrouter,cerebras"


def provider_order() -> list[str]:
    raw = os.getenv("LLM_PROVIDER_ORDER")
    if raw is None:
        raw = DEFAULT_ORDER
    return [name.strip() for name in raw.split(",") if name.strip()]


def models_for(provider: str) -> list[str]:
    """Models to try on one provider, in order.

    Free quotas are counted per model, so naming a second model on the same key
    both doubles the allowance and gives somewhere to go when the first is busy.
    """
    entry = PROVIDERS[provider]
    raw = os.getenv(entry.model_env) or ""
    models = [name.strip() for name in raw.split(",") if name.strip()]
    return models or [entry.default_model]


def db_path() -> Path:
    return Path(os.getenv("FACTLAYER_DB", "factlayer.db"))


def uploads_dir() -> Path:
    path = Path(os.getenv("FACTLAYER_UPLOADS", "uploads"))
    path.mkdir(parents=True, exist_ok=True)
    return path


def value_tolerance() -> float:
    """Relative difference below which two numbers are treated as the same."""
    return float(os.getenv("FACTLAYER_VALUE_TOLERANCE", "0.005"))


def percent_tolerance() -> float:
    """Percentages are compared in percentage points, not relatively: 6.4% and 6.5%
    are a real disagreement even though they are only 1.5% apart."""
    return float(os.getenv("FACTLAYER_PERCENT_TOLERANCE", "0.05"))


def requests_per_minute() -> int:
    """Requests per minute per provider. Set below the free tier's published limit;
    0 turns pacing off."""
    return int(os.getenv("FACTLAYER_RPM", "25"))


def provider_attempts() -> int:
    """How many times to re-send a request a provider refused for a transient
    reason before falling through to the next provider."""
    return max(1, int(os.getenv("FACTLAYER_ATTEMPTS", "3")))


def max_pages_per_document() -> int:
    """0 means no cap. Useful when a free tier is close to its daily limit."""
    return int(os.getenv("FACTLAYER_MAX_PAGES", "0"))


def extraction_workers() -> int:
    return int(os.getenv("FACTLAYER_WORKERS", "4"))
