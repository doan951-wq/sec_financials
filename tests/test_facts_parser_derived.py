"""Tests for Milestone 7.3: derived metrics (Cost of Revenue/Gross Profit
cross-fallback with the circularity guard, Free Cash Flow), the extended
FilingRecord/parse_company_facts covering all ~26 concepts, and the
extended _blank_records_from_any_tag. Fixtures only -- no live network
calls."""

from __future__ import annotations

import json
from pathlib import Path

from sec_financials import facts_parser

FIXTURES_DIR = Path(__file__).parent / "fixtures"


def _load_fixture(name: str) -> dict:
    with (FIXTURES_DIR / name).open("r", encoding="utf-8") as f:
        return json.load(f)


# -- missing concept -> blank cell, not an error -----------------------------


def test_company_missing_a_concept_gets_blank_cell_not_error():
    """A company with Revenue data but no ShortTermBorrowings (or any of
    the other ~25 concepts) tag at all must get a blank cell for that
    concept, not an error."""
    facts_json = _load_fixture("facts_revenues_tag.json")
    records = facts_parser.parse_company_facts(
        facts_json, "Revenues Tag Co", "RTC", "0000999001"
    )
    assert len(records) == 1
    record = records[0]
    assert record.short_term_debt is None
    assert record.finance_lease_liabilities is None
    assert record.total_assets is None
    assert record.eps_basic is None
    assert record.rsu_count is None
    # Sanity: the concepts that ARE present in this fixture still work.
    assert record.revenue == 1100
    assert record.cost_of_sales == 450
    assert record.gross_profit == 650


# -- derived metric fallback paths ------------------------------------------


def test_gross_profit_direct_tag_and_cost_of_sales_fallback():
    """Gross Profit has a direct tag; Cost of Sales has none, so it falls
    back to Revenue - Gross Profit (using Gross Profit's direct-tag
    value)."""
    facts_json = _load_fixture("facts_gross_profit_fallback_only.json")
    records = facts_parser.parse_company_facts(
        facts_json, "GrossProfitFallback Co", "GPF", "0000999008"
    )
    assert len(records) == 1
    record = records[0]
    assert record.revenue == 1000
    assert record.gross_profit == 400  # direct tag
    assert record.cost_of_sales == 600  # fallback: 1000 - 400


def test_cost_of_sales_direct_tag_and_gross_profit_fallback():
    """Cost of Sales has a direct tag; Gross Profit has none (no
    GrossProfit tag), so it falls back to Revenue - Cost of Sales (using
    Cost of Sales' direct-tag value)."""
    facts_json = _load_fixture("facts_cost_of_sales_fallback_only.json")
    records = facts_parser.parse_company_facts(
        facts_json, "CostOfSalesFallback Co", "COSF", "0000999009"
    )
    assert len(records) == 1
    record = records[0]
    assert record.revenue == 900
    assert record.cost_of_sales == 300  # direct tag
    assert record.gross_profit == 600  # fallback: 900 - 300


def test_free_cash_flow_computed_from_direct_tags():
    facts_json = _load_fixture("facts_free_cash_flow.json")
    records = facts_parser.parse_company_facts(
        facts_json, "FreeCashFlow Co", "FCF", "0000999010"
    )
    assert len(records) == 1
    record = records[0]
    assert record.operating_cash_flow == 500
    assert record.capital_expenditures == 120
    assert record.free_cash_flow == 380


def test_free_cash_flow_blank_when_capex_missing():
    facts_json = _load_fixture("facts_revenues_tag.json")
    records = facts_parser.parse_company_facts(
        facts_json, "Revenues Tag Co", "RTC", "0000999001"
    )
    assert len(records) == 1
    assert records[0].operating_cash_flow is None
    assert records[0].capital_expenditures is None
    assert records[0].free_cash_flow is None


# -- circularity guard regression --------------------------------------------


def test_circularity_guard_neither_fallback_reads_the_others_derived_value():
    """Regression test proving the guard is actually exercised, not just
    believed correct. Fixture has two filings for one company:

    - FY2020 (accn ...21-000001): Revenue=1000, direct GrossProfit=400, NO
      direct Cost-of-Sales tag anywhere in the company's facts.
      Correct: Cost of Sales falls back to 1000 - 400 = 600 (reading
      Gross Profit's DIRECT-tag value); Gross Profit = 400 (direct).
    - FY2021 (accn ...22-000001): Revenue=900, NEITHER a direct
      Cost-of-Sales tag NOR a direct GrossProfit tag.
      Correct: both Cost of Sales and Gross Profit stay blank, since
      neither fallback has a DIRECT-tag value on the other side to read.

    A naive sequential implementation that (incorrectly) lets state leak
    across records -- e.g. reusing a mutable "last known value" cache
    across the company's filings instead of computing each (accn, form)
    record's fallbacks from a fresh per-record snapshot of direct-tag
    values only -- would wrongly carry FY2020's derived Gross Profit
    forward and compute a non-blank Cost of Sales for FY2021 (verified by
    hand-modeling the naive approach: it produces Cost of Sales=500,
    Gross Profit=400 for FY2021, both wrong). This test fails under that
    naive approach and passes under the correct, per-record,
    direct-tag-only rule.
    """
    facts_json = _load_fixture("facts_circularity_guard.json")
    records = facts_parser.parse_company_facts(
        facts_json, "CircularityGuard Co", "CGC", "0000999011"
    )
    assert len(records) == 2
    by_period_end = {r.period_end: r for r in records}

    fy2020 = by_period_end["2020-12-31"]
    assert fy2020.revenue == 1000
    assert fy2020.gross_profit == 400
    assert fy2020.cost_of_sales == 600

    fy2021 = by_period_end["2021-12-31"]
    assert fy2021.revenue == 900
    assert fy2021.gross_profit is None
    assert fy2021.cost_of_sales is None


# -- extended _blank_records_from_any_tag: discovers dei-namespace tags too --


def test_blank_records_from_any_tag_discovers_dei_namespace():
    """A company with data ONLY under a dei tag (no us-gaap facts at all)
    must still get a blank row per filing -- the fallback filing-discovery
    scan must check both namespaces, not just us-gaap."""
    facts_json = {
        "cik": 999012,
        "entityName": "DeiOnly Co",
        "facts": {
            "dei": {
                "EntityCommonStockSharesOutstanding": {
                    "units": {
                        "shares": [
                            {
                                "end": "2021-02-01",
                                "val": 5000000,
                                "accn": "0000999012-21-000001",
                                "fy": 2021,
                                "fp": "FY",
                                "form": "10-K",
                                "filed": "2021-02-15",
                            }
                        ]
                    }
                }
            }
        },
    }
    # parse_company_facts alone finds no matching concept-table tag data
    # at all (dei:EntityCommonStockSharesOutstanding is in CONCEPT_TABLE,
    # so it WOULD actually be found -- use a tag not in CONCEPT_TABLE to
    # exercise the "no matching tag at all" fallback path specifically).
    facts_json["facts"]["dei"] = {
        "SomeUnrelatedDeiTag": {
            "units": {
                "shares": [
                    {
                        "end": "2021-02-01",
                        "val": 5000000,
                        "accn": "0000999012-21-000001",
                        "fy": 2021,
                        "fp": "FY",
                        "form": "10-K",
                        "filed": "2021-02-15",
                    }
                ]
            }
        }
    }
    assert facts_parser.parse_company_facts(facts_json, "DeiOnly Co", "DEIO", "0000999012") == []

    records = facts_parser._blank_records_from_any_tag(
        facts_json, "DeiOnly Co", "DEIO", "0000999012", forms=["10-K", "10-Q"]
    )
    assert len(records) == 1
    record = records[0]
    assert record.form == "10-K"
    assert record.period_end == "2021-02-01"
    assert record.revenue is None
