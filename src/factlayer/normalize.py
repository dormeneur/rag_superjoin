"""Turning what a document says into something comparable.

The language model is asked for verbatim strings only. Every conversion — magnitudes,
currencies, fiscal years — happens here, in code, so a model that is careless with
arithmetic cannot corrupt a stored value.

The vocabulary below is about how numbers and dates are *written* in Indian financial
and institutional English. None of it is specific to any document in the dataset.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date

# Written magnitudes. Deliberately no bare single letters ("m", "b", "k"): they
# collide with units of measure and turn "8,142 m" of road into 8.142 billion.
MAGNITUDES = {
    "thousand": 1e3, "thousands": 1e3,
    "lakh": 1e5, "lakhs": 1e5, "lac": 1e5, "lacs": 1e5,
    "mn": 1e6, "million": 1e6, "millions": 1e6,
    "cr": 1e7, "crore": 1e7, "crores": 1e7,
    "bn": 1e9, "billion": 1e9, "billions": 1e9,
    "tn": 1e12, "trillion": 1e12, "trillions": 1e12,
}

CURRENCY_SYMBOLS = {"₹": "INR", "$": "USD", "€": "EUR", "£": "GBP"}
CURRENCY_WORDS = {
    "rs": "INR", "inr": "INR", "rupee": "INR", "rupees": "INR",
    "usd": "USD", "us": "USD", "dollar": "USD", "dollars": "USD",
    "eur": "EUR", "euro": "EUR", "euros": "EUR",
    "gbp": "GBP", "pound": "GBP", "pounds": "GBP",
}

PERCENT_WORDS = ("%", "percent", "per cent", "percentage")
BASIS_POINT_WORDS = ("bps", "basis point", "basis points")

# Legal forms that make the same company look like two entities.
LEGAL_SUFFIXES = {
    "limited", "ltd", "inc", "incorporated", "plc", "llp", "llc",
    "pvt", "private", "corp", "corporation", "company", "co",
}

NUMBER = re.compile(r"\d[\d,\s]*(?:\.\d+)?|\.\d+")


@dataclass(frozen=True)
class Quantity:
    value: float
    unit: str | None
    raw: str


@dataclass(frozen=True)
class Period:
    start: date
    end: date
    label: str
    basis: str  # FY | CY | QUARTER | POINT


# --------------------------------------------------------------------- quantities


def parse_quantity(text: str) -> Quantity | None:
    """Read a written amount into a base unit: rupees, dollars, or percent.

    Returns None when there is no number to read, which is the common case for
    prose and must not be mistaken for zero.
    """
    if not text:
        return None
    raw = text
    lowered = text.lower()

    match = NUMBER.search(lowered)
    if not match:
        return None
    value = float(match.group(0).replace(",", "").replace(" ", ""))

    tail = lowered[match.end():]
    for word in _words(tail):
        if word in MAGNITUDES:
            value *= MAGNITUDES[word]
        elif word not in {"a", "of", "and"}:
            break  # magnitudes are written immediately after the number

    if _is_negative(lowered, match.start(), match.end()):
        value = -value

    if any(word in lowered for word in BASIS_POINT_WORDS):
        return Quantity(value / 100.0, "percent", raw)
    if any(word in lowered for word in PERCENT_WORDS):
        return Quantity(value, "percent", raw)

    return Quantity(value, _currency(lowered), raw)


def _words(text: str) -> list[str]:
    return re.findall(r"[a-z]+", text)


def _is_negative(text: str, start: int, end: int) -> bool:
    """A leading minus, or the accounting convention of wrapping a negative in
    parentheses. Anything looser would read "revenue (consolidated) 8,142" as -8,142."""
    before = text[:start].rstrip()
    if before.endswith("-"):
        return True
    return before.endswith("(") and ")" in text[end:]


def _currency(text: str) -> str | None:
    for symbol, code in CURRENCY_SYMBOLS.items():
        if symbol in text:
            return code
    for word in _words(text):
        if word in CURRENCY_WORDS:
            return CURRENCY_WORDS[word]
    return None


def is_currency(unit: str | None) -> bool:
    return unit in {"INR", "USD", "EUR", "GBP"}


# ------------------------------------------------------------------------ periods

MONTHS = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}

_QUARTER = re.compile(r"\bq([1-4])\s*(?:of\s*)?(?:fy|f\.y\.)\s*'?(\d{2,4})", re.I)
_FY_RANGE = re.compile(r"\b(?:fy|f\.y\.|fiscal(?:\s+year)?)\s*'?(\d{4})\s*[-/]\s*(\d{2,4})", re.I)
_FY_SINGLE = re.compile(r"\b(?:fy|f\.y\.|fiscal(?:\s+year)?)\s*'?(\d{2,4})\b", re.I)
_CY = re.compile(r"\b(?:cy|calendar\s+year)\s*'?(\d{4})\b", re.I)
_ISO_DATE = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")
_DMY = re.compile(r"\b(\d{1,2})[\s./-]+([a-z]{3,9}|\d{1,2})[\s./-]+(\d{4})\b", re.I)
_MDY = re.compile(r"\b([a-z]{3,9})\s+(\d{1,2}),?\s+(\d{4})\b", re.I)
_BARE_RANGE = re.compile(r"\b(\d{4})\s*[-/]\s*(\d{2,4})\b")

# Indian fiscal quarters, keyed by quarter number: (month, offset from FY end year).
_QUARTER_MONTHS = {1: (4, -1), 2: (7, -1), 3: (10, -1), 4: (1, 0)}


def parse_period(text: str) -> Period | None:
    """Read a period label. Fiscal years are named by the year they *end* in, which
    is the Indian convention: FY24 runs April 2023 to March 2024."""
    if not text:
        return None

    if m := _QUARTER.search(text):
        quarter, year = int(m.group(1)), _expand_year(m.group(2))
        month, offset = _QUARTER_MONTHS[quarter]
        start = date(year + offset, month, 1)
        end = _end_of_month(_add_months(start, 2))
        return Period(start, end, f"Q{quarter} FY{year}", "QUARTER")

    if m := _FY_RANGE.search(text):
        return _fiscal_year(_expand_year(m.group(2), century_from=m.group(1)))
    if m := _FY_SINGLE.search(text):
        return _fiscal_year(_expand_year(m.group(1)))
    if m := _CY.search(text):
        year = int(m.group(1))
        return Period(date(year, 1, 1), date(year, 12, 31), f"CY{year}", "CY")

    if point := _parse_point_date(text):
        return Period(point, point, point.isoformat(), "POINT")

    if m := _BARE_RANGE.search(text):
        return _fiscal_year(_expand_year(m.group(2), century_from=m.group(1)))
    return None


def _parse_point_date(text: str) -> date | None:
    if m := _ISO_DATE.search(text):
        return _safe_date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    if m := _MDY.search(text):
        month = MONTHS.get(m.group(1)[:3].lower())
        if month:
            return _safe_date(int(m.group(3)), month, int(m.group(2)))
    if m := _DMY.search(text):
        raw_month = m.group(2)
        month = MONTHS.get(raw_month[:3].lower()) if raw_month.isalpha() else int(raw_month)
        if month:
            return _safe_date(int(m.group(3)), month, int(m.group(1)))
    return None


def _safe_date(year: int, month: int, day: int) -> date | None:
    try:
        return date(year, month, day)
    except ValueError:
        return None


def _fiscal_year(end_year: int) -> Period:
    return Period(date(end_year - 1, 4, 1), date(end_year, 3, 31), f"FY{end_year}", "FY")


def _expand_year(raw: str, century_from: str | None = None) -> int:
    year = int(raw)
    if len(raw) == 4:
        return year
    if century_from:  # "2023-24" -> the 24 belongs to the same century as 2023
        return int(century_from[:2]) * 100 + year
    return 2000 + year


def _add_months(value: date, months: int) -> date:
    month = value.month + months
    return date(value.year + (month - 1) // 12, (month - 1) % 12 + 1, 1)


def _end_of_month(value: date) -> date:
    return date.fromordinal(_add_months(value, 1).toordinal() - 1)


def periods_overlap(a: Period | tuple, b: Period | tuple) -> bool:
    a_start, a_end = _bounds(a)
    b_start, b_end = _bounds(b)
    return a_start <= b_end and b_start <= a_end


def period_contains(outer, inner) -> bool:
    o_start, o_end = _bounds(outer)
    i_start, i_end = _bounds(inner)
    return o_start <= i_start and i_end <= o_end


def _bounds(value):
    if isinstance(value, Period):
        return value.start, value.end
    return value


# -------------------------------------------------------------------------- names


def normalize_entity(name: str) -> str:
    """Strip the decoration that makes one organisation look like several."""
    if not name:
        return ""
    text = re.sub(r"\(.*?\)", " ", name.lower())
    text = re.sub(r"[^a-z0-9&\s]", " ", text)
    words = [w for w in text.split() if w and w not in LEGAL_SUFFIXES]
    return " ".join(words)


def normalize_metric(name: str) -> str:
    """A stable key for a metric label. Qualifiers are kept: 'total income' and
    'income' are different line items and must not collapse into one."""
    if not name:
        return ""
    return re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")


def readable(label: str) -> str:
    """Present a canonical key as something a person would read.

    Models return the snake_case key in the human-readable field as well as the key
    field, so "shares_offered" reaches the page. Labels the document actually wrote
    contain spaces and are left exactly as they are. Tokens that are already
    upper-case, like EBITDA, keep their case.
    """
    if not label or " " in label or "_" not in label:
        return label
    words = [word for word in label.split("_") if word]
    if not words:
        return label
    first = words[0] if words[0].isupper() else words[0].capitalize()
    return " ".join([first, *words[1:]])


def normalize_value_text(value: str) -> str:
    """Loose comparison for written values, so 'Director & CEO' matches 'Director and
    CEO' without treating a genuinely different role as the same."""
    if not value:
        return ""
    text = value.lower().replace("&", " and ")
    return " ".join(re.sub(r"[^a-z0-9]+", " ", text).split())
