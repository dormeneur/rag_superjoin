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


# How much text a page needs before it is worth a model call. Covers, section
# dividers, and pages holding only a page number fall below it.
MIN_PAGE_CHARS = 40


def looks_factual(text: str) -> bool:
    """Whether a page has enough on it to state anything.

    This used to also require a currency amount, a percentage, a dense table or a
    role keyword. Measured against the starter documents that only skipped 9% of
    pages, because annual reports and statistical bulletins state facts nearly
    everywhere — and it silently skipped pages whose only fact carried no digits
    ("the long-term credit rating is AAA"). Nine percent is not worth losing facts
    for, so the bar is now simply whether there is text to read.
    """
    return len(text.strip()) >= MIN_PAGE_CHARS


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
