"""Tests for facts_parser.py. Fixtures only -- no live network calls."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from sec_financials import facts_parser

FIXTURES_DIR = Path(__file__).parent / "fixtures"


def _load_fixture(name: str) -> dict:
    with (FIXTURES_DIR / name).open("r", encoding="utf-8") as f:
        return json.load(f)


# -- tag-priority selection -------------------------------------------------


def test_revenues_tag_selected_when_present():
    facts_json = _load_fixture("facts_revenues_tag.json")
    records = facts_parser.parse_company_facts(
        facts_json, "Revenues Tag Co", "RTC", "0000999001"
    )
    assert len(records) == 1
    record = records[0]
    assert record.revenue == 1100
    assert record.cost_of_sales == 450
    assert record.gross_profit == 650


def test_revenue_from_contract_tag_used_as_fallback():
    facts_json = _load_fixture("facts_revenue_from_contract_tag.json")
    records = facts_parser.parse_company_facts(
        facts_json, "ContractRevenue Tag Co", "CRT", "0000999002"
    )
    assert len(records) == 1
    record = records[0]
    assert record.revenue == 520
    assert record.cost_of_sales == 210
    assert record.gross_profit == 310
    assert record.form == "10-Q"


def test_non_standard_cost_tag_used_as_fallback():
    facts_json = _load_fixture("facts_non_standard_cost_tag.json")
    records = facts_parser.parse_company_facts(
        facts_json, "Services Co", "SVC", "0000999003"
    )
    assert len(records) == 1
    record = records[0]
    assert record.revenue == 900
    assert record.cost_of_sales == 300
    assert record.gross_profit == 600


# -- accession grouping / duration + max-end-date selection ----------------


def test_three_comparative_years_picks_max_end_date():
    """A single 10-K accession with 3 annual-band entries (current fiscal
    year + two comparative prior years) must resolve to just the current
    year -- the entry with the maximum `end` date."""
    facts_json = _load_fixture("facts_three_comparative_years.json")
    records = facts_parser.parse_company_facts(
        facts_json, "Apple Inc.", "AAPL", "0000320193"
    )
    assert len(records) == 1
    record = records[0]
    assert record.period_end == "2022-09-24"
    assert record.revenue == 394328000000
    assert record.cost_of_sales == 223546000000
    assert record.gross_profit == 394328000000 - 223546000000


def test_quarter_and_ytd_bands_disambiguated_independently():
    """A 10-Q accession containing both a ~90-day quarter entry and a
    ~181-day YTD entry must not be conflated -- only the quarterly
    (80-100 day) band should be selected for a 10-Q form record, and the
    max-end-date rule should apply within that band."""
    facts_json = _load_fixture("facts_quarter_and_ytd.json")
    records = facts_parser.parse_company_facts(
        facts_json, "QuarterAndYTD Co", "QYC", "0000999004"
    )
    # Only Revenue tag present in this fixture; Cost of Sales absent -> blank.
    assert len(records) == 1
    record = records[0]
    assert record.period_end == "2022-03-26"
    assert record.revenue == 97278
    assert record.cost_of_sales is None
    assert record.gross_profit is None


# -- per-accession tag fallback (company switched XBRL tags over time) -----


def test_stale_higher_priority_tag_does_not_blank_other_filings():
    """A company whose higher-priority tag (Revenues) only has data for an
    old filing, and whose newer filing uses a lower-priority fallback tag
    (RevenueFromContractWithCustomerExcludingAssessedTax), must get correct
    Revenue values for BOTH filings -- fallback must be resolved per
    accession, not once globally by picking whichever tag is first found
    present anywhere in the company's facts. This reproduces a real bug
    found via live smoke-testing against Apple's companyfacts data, where
    Apple's `Revenues` tag has no data after fiscal 2018 but is still
    present in the company's facts, which used to cause every later filing
    to be silently blanked out."""
    facts_json = _load_fixture("facts_stale_higher_priority_tag.json")
    records = facts_parser.parse_company_facts(
        facts_json, "TagSwitcher Co", "TSW", "0000999006"
    )
    assert len(records) == 2

    by_period_end = {r.period_end: r for r in records}
    assert by_period_end["2017-12-31"].revenue == 700
    assert by_period_end["2020-12-31"].revenue == 1200


# -- blank rows for filers with no matching tag -----------------------------


def test_no_matching_tag_still_emits_blank_row():
    facts_json = _load_fixture("facts_no_matching_tags.json")
    records = facts_parser.parse_company_facts(
        facts_json, "NoMatchingTag Co", "NMT", "0000999005"
    )
    # parse_company_facts alone finds no Revenue/Cost datapoints at all.
    assert records == []


def test_fetch_and_parse_falls_back_to_blank_records(monkeypatch):
    """fetch_and_parse_company_facts should still emit a row per filing
    (blank Revenue/Cost/GrossProfit) when no Revenue/Cost tag matches,
    per PLAN.md resolved decision #5."""

    class FakeClient:
        def get_json(self, url):
            return _load_fixture("facts_no_matching_tags.json")

    records = facts_parser.fetch_and_parse_company_facts(
        FakeClient(), "0000999005", "NoMatchingTag Co", "NMT"
    )
    assert len(records) == 1
    record = records[0]
    assert record.form == "10-K"
    assert record.period_end == "2020-12-31"
    assert record.revenue is None
    assert record.cost_of_sales is None
    assert record.gross_profit is None


def test_fetch_and_parse_raises_facts_parser_error_on_client_failure():
    from sec_financials.edgar_client import EdgarClientError

    class FailingClient:
        def get_json(self, url):
            raise EdgarClientError("boom")

    with pytest.raises(facts_parser.FactsParserError, match="Failed to fetch"):
        facts_parser.fetch_and_parse_company_facts(
            FailingClient(), "0000999999", "X", "X"
        )


# -- forms filter -------------------------------------------------


def test_forms_filter_restricts_to_requested_forms():
    facts_json = _load_fixture("facts_revenue_from_contract_tag.json")
    records = facts_parser.parse_company_facts(
        facts_json,
        "ContractRevenue Tag Co",
        "CRT",
        "0000999002",
        forms=["10-K"],
    )
    assert records == []
