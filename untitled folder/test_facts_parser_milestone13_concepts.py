"""Tests for Milestone 13.1: ~45 new direct-tag-lookup concepts added to
CONCEPT_TABLE/FilingRecord (Income Statement, Balance Sheet, Cash Flow),
plus Ending Cash (Capability 2's plain new INSTANT concept). Fixtures
only -- no live network calls."""

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
    parse_company_facts,
)

FIXTURES_DIR = Path(__file__).parent / "fixtures"


def _load_fixture(name: str) -> dict:
    with (FIXTURES_DIR / name).open("r", encoding="utf-8") as f:
        return json.load(f)


# -- classification table completeness (Milestone 13.1) ---------------------


def test_every_new_milestone13_concept_has_kind_namespace_unit():
    for spec in CONCEPT_TABLE:
        assert spec.kind in (DURATION, INSTANT, SUM)
        assert spec.namespace in ("us-gaap", "dei", "amzn")
        assert spec.unit in (UNIT_USD, UNIT_USD_PER_SHARE, UNIT_SHARES)
        assert spec.tag_priority, f"{spec.key} has an empty tag_priority list"


def test_concept_table_covers_all_milestone13_1_keys():
    expected_new_keys = {
        # Income Statement
        "operating_expenses",
        "sales_and_marketing",
        "general_and_administrative",
        "other_operating_expense_income",
        "interest_income",
        "interest_expense",
        "other_income_expense_net",
        "diluted_shares",
        # Balance Sheet
        "accounts_receivable",
        "inventory",
        "other_current_assets",
        "property_and_equipment_net",
        "operating_lease_rou_asset",
        "goodwill",
        "other_assets_noncurrent",
        "accounts_payable",
        "accrued_expenses_and_other",
        "unearned_revenue",
        "current_finance_lease_liabilities",
        "current_operating_lease_liabilities",
        "long_term_finance_lease_liabilities",
        "long_term_operating_lease_liabilities",
        "other_long_term_liabilities",
        "total_liabilities_and_stockholders_equity",
        "ending_cash",
        # Cash Flow
        "depreciation_and_amortization",
        "stock_based_compensation",
        "deferred_taxes",
        "other_non_cash_items",
        "unearned_revenue_cf_change",
        "inventory_cf_change",
        "accounts_receivable_cf_change",
        "other_assets_cf_change",
        "accounts_payable_cf_change",
        "accrued_expenses_cf_change",
        "acquisitions",
        "net_cash_used_in_investing_activities",
        "purchases_of_marketable_securities",
        "proceeds_from_short_term_debt_and_other",
        "repayments_of_short_term_debt_and_other",
        "proceeds_from_long_term_debt",
        "repayments_of_long_term_debt",
        "finance_lease_principal_payments",
        "financing_obligation_principal_payments",
        "net_cash_used_in_financing_activities",
        "other_financing_activities",
        "net_change_in_cash",
    }
    assert expected_new_keys <= set(CONCEPT_BY_KEY)


def test_ending_cash_is_instant_us_gaap_usd():
    spec = CONCEPT_BY_KEY["ending_cash"]
    assert spec.kind == INSTANT
    assert spec.namespace == "us-gaap"
    assert spec.unit == UNIT_USD
    assert spec.tag_priority == [
        "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents"
    ]


def test_diluted_shares_is_distinct_from_shares_outstanding():
    """Diluted Shares (weighted-average) is a DURATION concept, distinct
    from the existing INSTANT "Shares Outstanding" concept."""
    diluted = CONCEPT_BY_KEY["diluted_shares"]
    outstanding = CONCEPT_BY_KEY["shares_outstanding"]
    assert diluted.kind == DURATION
    assert diluted.tag_priority == ["WeightedAverageNumberOfDilutedSharesOutstanding"]
    assert outstanding.kind == INSTANT
    assert diluted.tag_priority != outstanding.tag_priority


def test_purchases_of_marketable_securities_fallback_tag_corrected():
    """The brief's literal fallback tag name
    (PaymentsToAcquireAvailableForSaleSecurities) was checked live against
    the SEC frames API during implementation and found to have NO
    populated frame (404) -- the corrected, verified real tag
    (PaymentsToAcquireAvailableForSaleSecuritiesDebt, 906 companies in
    CY2023) is used instead, same discipline as the sum-tags correction."""
    spec = CONCEPT_BY_KEY["purchases_of_marketable_securities"]
    assert spec.tag_priority == [
        "PaymentsToAcquireMarketableSecurities",
        "PaymentsToAcquireAvailableForSaleSecuritiesDebt",
    ]


# -- blank-cell behavior for a company missing a new concept -----------------


def test_missing_new_concept_renders_blank_not_error():
    """A company with data under Revenue/Cost of Sales but nothing at all
    under any of the new Milestone 13.1 tags should get blank (None)
    values for those fields, not an error."""
    facts_json = _load_fixture("facts_revenues_tag.json")
    records = parse_company_facts(facts_json, "Revenues Tag Co", "RVT", "0000999001")
    assert records
    for r in records:
        assert r.goodwill is None
        assert r.stock_based_compensation is None
        assert r.diluted_shares is None
        assert r.ending_cash is None


# -- Property & Equipment Net: primary/fallback tag-order regression --------


def test_property_and_equipment_net_primary_tag_wins_when_both_present():
    facts_json = _load_fixture("facts_property_and_equipment_fallback.json")
    records = parse_company_facts(facts_json, "PPEFallback Co", "PPEF", "0000999020")
    by_year = {r.fiscal_year: r for r in records}

    # FY2022 accession has BOTH tags -- primary
    # (PropertyPlantAndEquipmentAndFinanceLeaseRightOfUseAssetAfter...)
    # must win, per the existing first-tag-present-wins convention.
    assert by_year[2022].property_and_equipment_net == 250000000


def test_property_and_equipment_net_fallback_tag_used_when_primary_absent():
    facts_json = _load_fixture("facts_property_and_equipment_fallback.json")
    records = parse_company_facts(facts_json, "PPEFallback Co", "PPEF", "0000999020")
    by_year = {r.fiscal_year: r for r in records}

    # FY2021 accession has ONLY the fallback tag
    # (PropertyPlantAndEquipmentNet) -- must be used since the primary tag
    # has no data for that specific accession.
    assert by_year[2021].property_and_equipment_net == 180000000
