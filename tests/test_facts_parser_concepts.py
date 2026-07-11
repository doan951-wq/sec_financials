"""Tests for Milestone 7.1: concept classification table and the
generalized namespace/unit-aware datapoint iterator and duration-grouping
functions. Fixtures only -- no live network calls."""

from __future__ import annotations

import json
from pathlib import Path

from sec_financials import facts_parser
from sec_financials.facts_parser import (
    CONCEPT_BY_KEY,
    CONCEPT_TABLE,
    DURATION,
    INSTANT,
    SUM,
    UNIT_SHARES,
    UNIT_USD,
    UNIT_USD_PER_SHARE,
)

FIXTURES_DIR = Path(__file__).parent / "fixtures"


def _load_fixture(name: str) -> dict:
    with (FIXTURES_DIR / name).open("r", encoding="utf-8") as f:
        return json.load(f)


# -- classification table completeness --------------------------------------


def test_every_concept_has_kind_namespace_unit():
    # Milestone 13.2 adds a third kind, SUM (multi-tag-sum concepts) --
    # this assertion is widened accordingly rather than left stale, since
    # CONCEPT_TABLE is one shared module-level table checked by this test
    # against every concept, current and future.
    for spec in CONCEPT_TABLE:
        assert spec.kind in (DURATION, INSTANT, SUM)
        assert spec.namespace in ("us-gaap", "dei", "amzn")
        assert spec.unit in (UNIT_USD, UNIT_USD_PER_SHARE, UNIT_SHARES)
        assert spec.tag_priority, f"{spec.key} has an empty tag_priority list"


def test_concept_table_covers_expected_keys():
    expected_keys = {
        "revenue",
        "cost_of_sales",
        "gross_profit",
        "operating_income",
        "net_income",
        "cash",
        "total_assets",
        "total_liabilities",
        "stockholders_equity",
        "operating_cash_flow",
        "capital_expenditures",
        "long_term_debt",
        "research_and_development",
        "shares_outstanding",
        "eps_basic",
        "eps_diluted",
        "income_tax_expense",
        "pre_tax_income",
        "dei_common_stock_shares_outstanding",
        "marketable_securities_current",
        "operating_lease_liabilities",
        "long_term_debt_noncurrent",
        "long_term_debt_current",
        "short_term_debt",
        "finance_lease_liabilities",
        "rsu_count",
    }
    assert expected_keys <= set(CONCEPT_BY_KEY)


def test_revenue_fallback_reordered_per_restated_list():
    """Milestone 7.1: Revenue's fallback order is restated to put
    RevenueFromContractWithCustomerExcludingAssessedTax first, Revenues
    second (a reordering from v1, not a behavior change)."""
    spec = CONCEPT_BY_KEY["revenue"]
    assert spec.tag_priority[0] == "RevenueFromContractWithCustomerExcludingAssessedTax"
    assert spec.tag_priority[1] == "Revenues"


# -- generalized iterator: regression against old us-gaap/USD behavior -----


def test_iter_datapoints_us_gaap_usd_matches_legacy_iter_usd_datapoints():
    facts_json = _load_fixture("facts_revenues_tag.json")
    facts = facts_json["facts"]

    via_generalized = list(facts_parser._iter_datapoints(facts, "us-gaap", "Revenues", "USD"))
    via_legacy = list(facts_parser._iter_usd_datapoints(facts, "Revenues"))

    assert via_generalized == via_legacy
    assert len(via_generalized) == 2


def test_best_value_by_accession_us_gaap_usd_regression():
    """Regression: calling the generalized _best_value_by_accession with
    default namespace/unit args must match v1 behavior exactly."""
    facts_json = _load_fixture("facts_revenues_tag.json")
    facts = facts_json["facts"]

    result = facts_parser._best_value_by_accession(
        facts, facts_parser.REVENUE_TAG_PRIORITY, ["10-K", "10-Q"]
    )
    assert len(result) == 1
    (key, dp), = result.items()
    assert dp["val"] == 1100
    assert dp["end"] == "2021-12-31"


# -- dei namespace + shares/USD-per-shares unit handling --------------------


def test_iter_datapoints_dei_namespace_shares_unit():
    facts_json = _load_fixture("facts_eps_and_dei.json")
    facts = facts_json["facts"]

    dps = list(
        facts_parser._iter_datapoints(
            facts, "dei", "EntityCommonStockSharesOutstanding", "shares"
        )
    )
    assert len(dps) == 1
    assert dps[0]["val"] == 15900000


def test_iter_datapoints_us_gaap_shares_unit():
    facts_json = _load_fixture("facts_eps_and_dei.json")
    facts = facts_json["facts"]

    dps = list(
        facts_parser._iter_datapoints(facts, "us-gaap", "CommonStockSharesOutstanding", "shares")
    )
    assert len(dps) == 1
    assert dps[0]["val"] == 16000000


def test_best_value_by_accession_usd_per_shares_unit_eps():
    """EPS Basic/Diluted use the same duration-band + max-end-date
    selection as Revenue/Cost of Sales, just under unit "USD/shares"."""
    facts_json = _load_fixture("facts_eps_and_dei.json")
    facts = facts_json["facts"]

    result = facts_parser._best_value_by_accession(
        facts, ["EarningsPerShareBasic"], ["10-K", "10-Q"], "us-gaap", "USD/shares"
    )
    assert len(result) == 1
    (key, dp), = result.items()
    assert dp["val"] == 6.15
    assert dp["end"] == "2021-12-31"


def test_best_value_by_accession_wrong_unit_yields_nothing():
    """Sanity check that unit filtering is actually applied -- asking for
    the wrong unit key on a real tag should yield no matches."""
    facts_json = _load_fixture("facts_eps_and_dei.json")
    facts = facts_json["facts"]

    result = facts_parser._best_value_by_accession(
        facts, ["EarningsPerShareBasic"], ["10-K", "10-Q"], "us-gaap", "USD"
    )
    assert result == {}
