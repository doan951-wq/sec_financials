"""Tests for the Long Term Debt fallback fix.

Root cause (confirmed live against real SEC companyconcept data, see
EXECUTION_LOG.md-equivalent debug-solver report): GOOGL has ZERO
`us-gaap:LongTermDebt` facts across its entire filing history. Historically
(through 2025-Q1) it instead reports `LongTermDebtAndCapitalLeaseObligations`
for nearly every quarter; from 2025-Q2 onward it drops both total tags
entirely and only reports the current/noncurrent split.

Two independent fallback layers, verified live to each add real coverage:
1. Tag-priority fallback: `LongTermDebt` -> `LongTermDebtAndCapitalLeaseObligations`
   (reuses the existing tag_priority mechanism, same shape as
   Revenue/Cost-of-Sales).
2. Derived fallback: long_term_debt_current + long_term_debt_noncurrent,
   read only via those two concepts' DIRECT-TAG values (single-tag
   ConceptSpecs, so `vals[...]` for them is inherently a direct-tag read),
   following the same circularity-safety discipline as the Milestone 7.3
   Gross-Profit/Cost-of-Sales cross-fallback -- only used when NEITHER
   direct total tag is present.

Fixtures are pinned to real GOOGL/AAPL companyconcept API data confirmed
live during debugging, not synthetic guesses (except the AAPL
current/noncurrent sub-values in the "direct tag wins" test, which are
deliberately fake and mismatched from the real LongTermDebt value -- that
mismatch is the point: it proves the fallback sum is never consulted when
the direct tag is present, since a bug that mistakenly fell through to the
sum would produce a materially different, easily-detected wrong value)."""

from __future__ import annotations

import json
from pathlib import Path

from sec_financials import facts_parser

FIXTURES_DIR = Path(__file__).parent / "fixtures"


def _load_fixture(name: str) -> dict:
    with (FIXTURES_DIR / name).open("r", encoding="utf-8") as f:
        return json.load(f)


def test_long_term_debt_and_capital_lease_obligations_tag_fallback():
    """Real GOOGL Q1 2023 10-Q shape: no LongTermDebt tag at all, no
    LongTermDebtNoncurrent either, but LongTermDebtAndCapitalLeaseObligations
    is present. Tag-priority fallback must pick it up."""
    facts_json = _load_fixture("facts_long_term_debt_and_capital_lease_fallback.json")
    records = facts_parser.parse_company_facts(
        facts_json, "Alphabet Inc.", "GOOGL", "0001652044"
    )
    assert len(records) == 1
    record = records[0]
    assert record.period_end == "2023-03-31"
    assert record.long_term_debt == 13697000000
    # The current-only value must not leak through on its own.
    assert record.long_term_debt_current == 999000000
    assert record.long_term_debt_noncurrent is None


def test_long_term_debt_current_plus_noncurrent_sum_fallback():
    """Real GOOGL Q2 2025 10-Q shape: neither LongTermDebt nor
    LongTermDebtAndCapitalLeaseObligations present at all (confirmed live,
    both empty for this period) -- only the current/noncurrent split is
    reported. Must fall back to current + noncurrent."""
    facts_json = _load_fixture(
        "facts_long_term_debt_current_noncurrent_sum_fallback.json"
    )
    records = facts_parser.parse_company_facts(
        facts_json, "Alphabet Inc.", "GOOGL", "0001652044"
    )
    assert len(records) == 1
    record = records[0]
    assert record.period_end == "2025-06-30"
    assert record.long_term_debt_current == 1000000000
    assert record.long_term_debt_noncurrent == 23607000000
    assert record.long_term_debt == 1000000000 + 23607000000


def test_long_term_debt_direct_tag_wins_no_regression_for_apple():
    """Apple already reports plain LongTermDebt directly for every
    quarter (confirmed live: 28/28 10-Q instants covered, zero
    LongTermDebtAndCapitalLeaseObligations facts at all). The direct tag
    must win over both fallback layers -- this is the no-regression
    check for a company the concept already worked correctly for."""
    facts_json = _load_fixture(
        "facts_long_term_debt_direct_tag_wins_over_fallbacks.json"
    )
    records = facts_parser.parse_company_facts(
        facts_json, "Apple Inc.", "AAPL", "0000320193"
    )
    assert len(records) == 1
    record = records[0]
    # Real value. The current/noncurrent fixture values are deliberately
    # fake and would sum to a different, wrong number if the fallback
    # were mistakenly consulted here.
    assert record.long_term_debt == 82700000000
    assert record.long_term_debt != (1111111111 + 2222222222)


def test_long_term_debt_blank_when_no_tag_and_incomplete_sum_inputs():
    """Circularity/edge-case guard: if neither total tag is present AND
    only one of current/noncurrent is present (not both), long_term_debt
    must stay blank rather than silently treating the missing half as
    zero."""
    facts_json = {
        "cik": 999099,
        "entityName": "PartialDebt Co",
        "facts": {
            "us-gaap": {
                "LongTermDebtCurrent": {
                    "units": {
                        "USD": [
                            {
                                "end": "2021-12-31",
                                "val": 500,
                                "accn": "0000999099-22-000001",
                                "fy": 2021,
                                "fp": "FY",
                                "form": "10-K",
                                "filed": "2022-02-15",
                            }
                        ]
                    }
                }
            }
        },
    }
    records = facts_parser.parse_company_facts(
        facts_json, "PartialDebt Co", "PDC", "0000999099"
    )
    assert len(records) == 1
    record = records[0]
    assert record.long_term_debt_current == 500
    assert record.long_term_debt_noncurrent is None
    assert record.long_term_debt is None
