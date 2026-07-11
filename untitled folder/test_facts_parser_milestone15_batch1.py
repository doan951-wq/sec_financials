"""Tests for Milestone 15 Batch 1 (15.1, 15.2, 15.4, 15.5): plain
`tag_priority`-list fallback-tag additions to already-shipped concepts,
prompted by a live-verified GOOGL (CIK 0001652044) coverage gap -- the
originally chosen tag returns zero facts for Alphabet even though a
working alternate tag exists (see PLAN.md's Milestone 15 section for the
live-verification numbers). Fixtures only -- no live network calls.

Every fixture below follows the existing "primary-tag-absent-for-this-
accession, fallback-tag-present" pattern already used by
`facts_property_and_equipment_fallback.json` (Milestone 13.1) and the
Milestone 7.1/7.3 `_best_value_by_accession` fallback tests: one accession
has ONLY the fallback tag (confirms the fallback fires), and (for
concepts where both tags happen to be present in the same accession) the
primary tag's value must still win, unchanged first-tag-present-wins
per-accession behavior.
"""

from __future__ import annotations

import json
from pathlib import Path

from sec_financials.facts_parser import CONCEPT_BY_KEY, parse_company_facts

FIXTURES_DIR = Path(__file__).parent / "fixtures"


def _load_fixture(name: str) -> dict:
    with (FIXTURES_DIR / name).open("r", encoding="utf-8") as f:
        return json.load(f)


# -- 15.1: Interest Income / Interest Expense / Other Income-Expense, Net --


def test_interest_income_tag_priority_extended():
    spec = CONCEPT_BY_KEY["interest_income"]
    assert spec.tag_priority == [
        "InvestmentIncomeInterest",
        "InterestIncomeNonOperating",
        "InterestIncomeOther",
    ]


def test_interest_income_fallback_tag_used_when_primary_absent():
    facts_json = _load_fixture("facts_milestone15_interest_income_fallback.json")
    records = parse_company_facts(facts_json, "InterestIncomeFallback Co", "IIF", "0000999040")
    by_year = {r.fiscal_year: r for r in records}
    # FY2022 accession has BOTH tags -- primary (InterestIncomeNonOperating)
    # must win, per-accession first-tag-present-wins, unchanged.
    assert by_year[2022].interest_income == 500000000
    # FY2021 accession has ONLY the fallback tag (InterestIncomeOther) --
    # must be used since the primary has no data for that accession.
    assert by_year[2021].interest_income == 300000000


def test_interest_expense_tag_priority_extended():
    spec = CONCEPT_BY_KEY["interest_expense"]
    assert spec.tag_priority == [
        "InterestExpenseNonoperating",
        "InterestExpenseNonOperating",
        "InterestExpense",
    ]


def test_interest_expense_fallback_tag_used_when_primary_absent():
    facts_json = _load_fixture("facts_milestone15_interest_expense_fallback.json")
    records = parse_company_facts(facts_json, "InterestExpenseFallback Co", "IEF", "0000999041")
    by_year = {r.fiscal_year: r for r in records}
    assert by_year[2022].interest_expense == 350000000
    assert by_year[2021].interest_expense == 200000000


def test_other_income_expense_net_tag_priority_extended():
    spec = CONCEPT_BY_KEY["other_income_expense_net"]
    assert spec.tag_priority == [
        "OtherNonoperatingIncomeExpense",
        "OtherIncomeExpenseNet",
        "NonoperatingIncomeExpense",
    ]


def test_other_income_expense_net_fallback_tag_used_when_primary_absent():
    facts_json = _load_fixture(
        "facts_milestone15_other_income_expense_net_fallback.json"
    )
    records = parse_company_facts(
        facts_json, "OtherIncomeExpenseFallback Co", "OIEF", "0000999042"
    )
    by_year = {r.fiscal_year: r for r in records}
    assert by_year[2022].other_income_expense_net == 50000000
    assert by_year[2021].other_income_expense_net == 150000000


# -- 15.2: Sales & Marketing -------------------------------------------------


def test_sales_and_marketing_tag_priority_extended():
    spec = CONCEPT_BY_KEY["sales_and_marketing"]
    assert spec.tag_priority == ["MarketingExpense", "SellingAndMarketingExpense"]


def test_sales_and_marketing_fallback_tag_used_when_primary_absent():
    facts_json = _load_fixture("facts_milestone15_sales_and_marketing_fallback.json")
    records = parse_company_facts(
        facts_json, "SalesMarketingFallback Co", "SMF", "0000999043"
    )
    by_year = {r.fiscal_year: r for r in records}
    assert by_year[2022].sales_and_marketing == 600000000
    assert by_year[2021].sales_and_marketing == 500000000


# -- 15.4: Sales/Maturities of Marketable Securities: second combined-tag fallback --


def test_sales_maturities_second_combined_fallback_used_when_first_absent():
    """Both split summands absent, first combined fallback tag
    (`ProceedsFromSaleMaturityAndCollectionsOfInvestments`) also absent,
    but the second (`ProceedsFromSaleAndMaturityOfMarketableSecurities`)
    is present -- concept resolves to that value, not blank."""
    facts_json = _load_fixture("facts_sum_second_combined_fallback.json")
    records = parse_company_facts(
        facts_json, "SumSecondCombinedFallback Co", "SSCF", "0000999044"
    )
    assert len(records) == 1
    assert records[0].sales_maturities_of_marketable_securities == 15000000000


def test_sales_maturities_first_combined_fallback_still_wins_when_present():
    """Regression: existing Milestone 13.2 behavior (first combined
    fallback tag present) is unchanged by adding the second fallback tag."""
    facts_json = _load_fixture("facts_sum_neither_present_combined_fallback.json")
    records = parse_company_facts(
        facts_json, "SumCombinedFallback Co", "SCF", "0000999032"
    )
    assert len(records) == 1
    assert records[0].sales_maturities_of_marketable_securities == 12000000000


def test_sales_maturities_split_summands_still_win_over_both_fallbacks():
    """Regression: existing Milestone 13.2 behavior (split summands
    present) is unchanged by adding the second fallback tag."""
    facts_json = _load_fixture("facts_sum_both_present.json")
    records = parse_company_facts(facts_json, "SumBothPresent Co", "SBP", "0000999030")
    assert len(records) == 1
    assert records[0].sales_maturities_of_marketable_securities == 4000000000 + 6000000000


# -- 15.5: Deferred Taxes -----------------------------------------------------


def test_deferred_taxes_tag_priority_extended():
    spec = CONCEPT_BY_KEY["deferred_taxes"]
    assert spec.tag_priority == [
        "DeferredIncomeTaxExpenseBenefit",
        "DeferredIncomeTaxesAndTaxCredits",
    ]


def test_deferred_taxes_primary_wins_when_both_present_even_though_values_differ():
    """Live-verified real-world shape: GOOGL's two Deferred Taxes tags
    both report the SAME (accession, form, start, end) period with
    DIFFERENT values (e.g. real FY2022: -493M primary vs. -437M
    secondary). Primary must win -- the two tags are not interchangeable,
    and the user resolved to keep the narrower/plainer tag primary."""
    facts_json = _load_fixture("facts_milestone15_deferred_taxes_fallback.json")
    records = parse_company_facts(
        facts_json, "DeferredTaxesFallback Co", "DTF", "0000999045"
    )
    by_year = {r.fiscal_year: r for r in records}
    assert by_year[2022].deferred_taxes == -493000000


def test_deferred_taxes_secondary_fallback_used_when_primary_absent():
    """FY2021 accession has ONLY the secondary tag
    (DeferredIncomeTaxesAndTaxCredits) -- used only because the primary
    has no data at all for that accession, not as an override."""
    facts_json = _load_fixture("facts_milestone15_deferred_taxes_fallback.json")
    records = parse_company_facts(
        facts_json, "DeferredTaxesFallback Co", "DTF", "0000999045"
    )
    by_year = {r.fiscal_year: r for r in records}
    assert by_year[2021].deferred_taxes == 145000000
