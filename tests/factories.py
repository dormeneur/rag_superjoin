"""Claim factory for tests.

Tests state only the dimensions they are about and inherit the rest, so a
reconciliation case reads as the one difference under test.
"""

from __future__ import annotations

from datetime import date


def make_claim(**overrides):
    from factlayer.store import Claim

    defaults = dict(
        id=None,
        kind="measurement",
        entity="Delhivery Limited",
        entity_canonical="delhivery_limited",
        metric="Revenue from operations",
        metric_canonical="revenue_from_operations",
        value_num=81_420_000_000.0,
        unit="INR",
        value_text="Rs. 8,142 crore",
        period_start=date(2023, 4, 1),
        period_end=date(2024, 3, 31),
        period_label="FY2024",
        scope={},
        quote="Revenue from operations was Rs. 8,142 crore in FY24.",
        page_no=1,
        char_start=0,
        char_end=51,
        confidence=0.9,
        doc_id="doc-a",
        doc_date=date(2024, 6, 30),
    )
    defaults.update(overrides)
    return Claim(**defaults)
