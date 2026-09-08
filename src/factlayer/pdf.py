"""Reading PDFs and deciding which pages are worth spending a model call on."""

from __future__ import annotations

import io
import re
from dataclasses import dataclass

import pdfplumber


class PdfError(ValueError):
    """The file cannot be read as a PDF, or holds no text to read."""


@dataclass(frozen=True)
class Page:
    number: int  # 1-based, as printed in a citation
    text: str


def read_pages(data: bytes) -> list[Page]:
    if not data:
        raise PdfError("The uploaded file is empty.")
    if not data.lstrip()[:5].startswith(b"%PDF"):
        raise PdfError("This does not look like a PDF file.")

    pages: list[Page] = []
    try:
        with pdfplumber.open(io.BytesIO(data)) as document:
            for index, page in enumerate(document.pages, start=1):
                pages.append(Page(index, page.extract_text() or ""))
                page.flush_cache()  # keeps memory flat on a 500-page report
    except PdfError:
        raise
    except Exception as exc:  # pdfminer raises a wide family of parse errors
        raise PdfError(f"The PDF could not be parsed: {exc}") from exc

    if not pages:
        raise PdfError("The PDF has no pages.")
    if not any(page.text.strip() for page in pages):
        raise PdfError(
            "The PDF has no text layer. It is probably a scan; this system does not do OCR."
        )
    return pages


# A page earns a model call if it states something. Measured on the starter set,
# this keeps about 90% of pages: annual reports and statistical bulletins really are
# that dense. It earns its keep on covers, contents pages and narrative sections.
_QUANTITY = re.compile(
    r"(?:[₹$€£]\s*\d)"
    r"|(?:\d[\d,.\s]*\s*(?:%|per\s?cent\w*|bps|basis\s+points|crore|crores|lakh|lakhs"
    r"|million|mn|billion|bn|trillion|tn|thousand)\b)"
    r"|(?:\b(?:rs|inr|usd|eur|gbp)\b\.?\s*\d)",
    re.I,
)
# Tables state their units in the header rather than beside every number, so a page
# dense with bare numbers is a table and worth reading.
_NUMBER_TOKEN = re.compile(r"(?<![A-Za-z])-?\d+(?:[.,]\d+)*")
_TABLE_DENSITY = 12

# Facts that carry no digits at all: who holds which role, where a company sits.
_SEMANTIC_SIGNALS = re.compile(
    r"\b(?:chair(?:man|person)?|director|officer|ceo|cfo|coo|cto|president|secretary"
    r"|auditor|appointed|resigned|retired|effective\s+from|incorporated"
    r"|registered\s+office|headquarter\w*|subsidiar\w+|acquired|merger)\b",
    re.I,
)

MIN_PAGE_CHARS = 40


def looks_factual(text: str) -> bool:
    """Skip covers, blank pages and pure narrative before paying for a model call."""
    stripped = text.strip()
    if len(stripped) < MIN_PAGE_CHARS:
        return False
    return bool(
        _QUANTITY.search(stripped)
        or _SEMANTIC_SIGNALS.search(stripped)
        or len(_NUMBER_TOKEN.findall(stripped)) >= _TABLE_DENSITY
    )


def find_quote(page_text: str, quote: str) -> tuple[int, int] | None:
    """Locate a quoted sentence in its page and return its exact character span.

    Falls back to a whitespace-insensitive search because PDF text carries line
    breaks that a model reproduces as spaces. The caller stores the page's own
    substring rather than the model's copy, so stored evidence is always verbatim.
    """
    quote = (quote or "").strip()
    if not quote:
        return None

    index = page_text.find(quote)
    if index != -1:
        return index, index + len(quote)

    tokens = quote.split()
    if not tokens:
        return None
    flexible = re.compile(r"\s+".join(re.escape(token) for token in tokens))
    match = flexible.search(page_text)
    return match.span() if match else None
