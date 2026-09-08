"""Normalisation contract.

These cases are written from the specification, not from the implementation. They
describe how the documents in this domain actually write numbers and periods:
Indian magnitudes (lakh, crore), accounting negatives in parentheses, fiscal years
that end in March, and basis points.
"""

from __future__ import annotations

from datetime import date

import pytest

from factlayer.normalize import (
    normalize_entity,
    normalize_metric,
    parse_period,
    parse_quantity,
)

# --------------------------------------------------------------------- quantities

QUANTITIES = [
    # text,                        value,            unit
    ("Rs. 8,142 crore",            8.142e10,         "INR"),
    ("₹8,142 Cr",             8.142e10,         "INR"),
    ("INR 81,420 million",         8.142e10,         "INR"),
    ("1.2 lakh crore",             1.2e12,           None),
    ("USD 3.5 bn",                 3.5e9,            "USD"),
    ("$3.5 billion",               3.5e9,            "USD"),
    ("3,45,678",                   345678.0,         None),   # Indian digit grouping
    ("(1,234)",                    -1234.0,          None),   # accounting negative
    ("-1,234",                     -1234.0,          None),
    ("6.4%",                       6.4,              "percent"),
    ("6.4 per cent",               6.4,              "percent"),
    ("25 bps",                     0.25,             "percent"),
    ("0.5 lakh",                   50000.0,          None),
]


@pytest.mark.parametrize("text,value,unit", QUANTITIES)
def test_parse_quantity(text, value, unit):
    q = parse_quantity(text)
    assert q is not None, f"failed to parse {text!r}"
    assert q.value == pytest.approx(value, rel=1e-9)
    assert q.unit == unit


@pytest.mark.parametrize("text", ["", "no numbers at all", "N.A.", "—"])
def test_parse_quantity_rejects_non_numbers(text):
    assert parse_quantity(text) is None


def test_parse_quantity_keeps_the_raw_text():
    """Evidence must survive normalisation, so the original string is retained."""
    assert parse_quantity("Rs. 8,142 crore").raw == "Rs. 8,142 crore"


# ------------------------------------------------------------------------ periods

PERIODS = [
    # text,                    start,              end,                 label,        basis
    ("FY24",                   date(2023, 4, 1),   date(2024, 3, 31),   "FY2024",     "FY"),
    ("FY 2023-24",             date(2023, 4, 1),   date(2024, 3, 31),   "FY2024",     "FY"),
    ("fiscal year 2024",       date(2023, 4, 1),   date(2024, 3, 31),   "FY2024",     "FY"),
    ("2024-25",                date(2024, 4, 1),   date(2025, 3, 31),   "FY2025",     "FY"),
    ("Q4 FY24",                date(2024, 1, 1),   date(2024, 3, 31),   "Q4 FY2024",  "QUARTER"),
    ("Q1 FY25",                date(2024, 4, 1),   date(2024, 6, 30),   "Q1 FY2025",  "QUARTER"),
    ("CY2024",                 date(2024, 1, 1),   date(2024, 12, 31),  "CY2024",     "CY"),
    ("calendar year 2023",     date(2023, 1, 1),   date(2023, 12, 31),  "CY2023",     "CY"),
    ("as of March 31, 2024",   date(2024, 3, 31),  date(2024, 3, 31),   "2024-03-31", "POINT"),
    ("31 March 2024",          date(2024, 3, 31),  date(2024, 3, 31),   "2024-03-31", "POINT"),
]


@pytest.mark.parametrize("text,start,end,label,basis", PERIODS)
def test_parse_period(text, start, end, label, basis):
    p = parse_period(text)
    assert p is not None, f"failed to parse {text!r}"
    assert (p.start, p.end, p.label, p.basis) == (start, end, label, basis)


@pytest.mark.parametrize("text", ["", "no period here", "revenue grew"])
def test_parse_period_rejects_non_periods(text):
    assert parse_period(text) is None


def test_indian_fiscal_year_is_the_year_it_ends_in():
    """FY24 in these documents means April 2023 to March 2024. Getting this backwards
    would silently mis-align every cross-document comparison."""
    assert parse_period("FY24").end.year == 2024
    assert parse_period("FY24").start.year == 2023


# ----------------------------------------------------------------------- names

@pytest.mark.parametrize(
    "raw,expected",
    [
        ("Delhivery Limited", "delhivery"),
        ("Delhivery Ltd.", "delhivery"),
        ("DELHIVERY  LIMITED", "delhivery"),
        ("Reserve Bank of India (RBI)", "reserve bank of india"),
        ("  Acme Inc. ", "acme"),
    ],
)
def test_normalize_entity(raw, expected):
    assert normalize_entity(raw) == expected


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("Revenue from Operations", "revenue_from_operations"),
        ("revenue  from operations ", "revenue_from_operations"),
        ("Revenue-from-operations", "revenue_from_operations"),
        ("Total income", "total_income"),
    ],
)
def test_normalize_metric(raw, expected):
    assert normalize_metric(raw) == expected


def test_normalize_metric_keeps_qualifiers():
    """'Total income' and 'income' are different line items. Stripping words like
    'total' would silently merge distinct metrics."""
    assert normalize_metric("Total income") != normalize_metric("Income")


# ------------------------------------------------------- labels people read

from factlayer.normalize import readable


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("shares_offered", "Shares offered"),
        ("corporate_identity_number", "Corporate identity number"),
        ("revenue_from_operations", "Revenue from operations"),
        ("EBITDA_margin", "EBITDA margin"),
        ("real_gdp_growth", "Real gdp growth"),
    ],
)
def test_a_snake_case_key_is_made_readable(raw, expected):
    """Models return the canonical key in the human-readable field too. Showing
    'shares_offered' to a reader is showing them the plumbing."""
    assert readable(raw) == expected


@pytest.mark.parametrize(
    "raw",
    ["Revenue from operations", "Delhivery Limited", "EBITDA", "Q4 FY24 revenue", ""],
)
def test_a_label_the_document_actually_wrote_is_left_alone(raw):
    """Anything with a space is already how the page words it, and must not be
    reformatted."""
    assert readable(raw) == raw


from factlayer.normalize import readable_name


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("sahil_barua", "Sahil Barua"),
        ("delhivery_limited", "Delhivery Limited"),
        ("reserve_bank_of_india", "Reserve Bank of India"),
        ("india", "India"),
        ("svf_doorbell", "SVF Doorbell"),
    ],
)
def test_an_entity_key_is_presented_as_a_name(raw, expected):
    """Entities are people, companies and places. Sentence case turns 'Sahil Barua'
    into 'Sahil barua', which reads as a typo rather than a name."""
    assert readable_name(raw) == expected


@pytest.mark.parametrize("raw", ["Delhivery Limited", "Reserve Bank of India", "IMF", ""])
def test_a_name_the_document_wrote_is_left_alone(raw):
    assert readable_name(raw) == raw
