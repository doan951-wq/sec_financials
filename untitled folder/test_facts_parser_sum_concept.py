"""Tests for Milestone 13.2 (Capability 1): the multi-tag-sum ConceptSpec
mechanism ("Sales / Maturities of Marketable Securities"). Fixtures only
-- no live network calls, though the fixture in this file
(facts_sum_real_truist_2023.json) is pinned to real Truist Financial Corp
(CIK 0000092230) figures, verified live against the SEC frames API during
this milestone's implementation -- see EXECUTION_LOG.md."""

from __future__ import annotations

import json
from pathlib import Path

from sec_financials.facts_parser import CONCEPT_BY_KEY, SUM, parse_company_facts

FIXTURES_DIR = Path(__file__).parent / "fixtures"


def _load_fixture(name: str) -> dict:
    with (FIXTURES_DIR / name).open("r", encoding="utf-8") as f:
        return json.load(f)


def test_sum_concept_classification():
    spec = CONCEPT_BY_KEY["sales_maturities_of_marketable_securities"]
    assert spec.kind == SUM
    assert spec.sum_tags == [
        "ProceedsFromSaleOfAvailableForSaleSecuritiesDebt",
        "ProceedsFromMaturitiesPrepaymentsAndCallsOfAvailableForSaleSecurities",
    ]
    # Milestone 15.4: tag_priority extended with a second combined-tag
    # fallback (used only when both split summands are absent).
    assert spec.tag_priority == [
        "ProceedsFromSaleMaturityAndCollectionsOfInvestments",
        "ProceedsFromSaleAndMaturityOfMarketableSecurities",
    ]


def test_both_summand_tags_present_are_summed():
    facts_json = _load_fixture("facts_sum_both_present.json")
    records = parse_company_facts(facts_json, "SumBothPresent Co", "SBP", "0000999030")
    assert len(records) == 1
    assert records[0].sales_maturities_of_marketable_securities == 4000000000 + 6000000000


def test_only_one_summand_tag_present_is_used_not_treated_as_blank():
    facts_json = _load_fixture("facts_sum_one_present.json")
    records = parse_company_facts(facts_json, "SumOnePresent Co", "SOP", "0000999031")
    assert len(records) == 1
    # A missing individual summand is treated as 0, not blank -- the
    # concept's value is simply the one present summand's value.
    assert records[0].sales_maturities_of_marketable_securities == 5000000000


def test_neither_split_tag_present_falls_back_to_combined_tag():
    facts_json = _load_fixture("facts_sum_neither_present_combined_fallback.json")
    records = parse_company_facts(
        facts_json, "SumCombinedFallback Co", "SCF", "0000999032"
    )
    assert len(records) == 1
    assert records[0].sales_maturities_of_marketable_securities == 12000000000


def test_all_three_tags_absent_is_blank_not_zero():
    """A company with no data under any of the three tags (split or
    combined) must render blank (None), never a fabricated 0."""
    facts_json = _load_fixture("facts_revenues_tag.json")
    records = parse_company_facts(facts_json, "Revenues Tag Co", "RVT", "0000999001")
    assert records
    for r in records:
        assert r.sales_maturities_of_marketable_securities is None


def test_real_data_both_summand_tags_present_truist_2023():
    """Real-data-backed fixture (Truist Financial Corp, CIK 0000092230,
    FY2023 10-K, accession 0000092230-24-000010) -- one of 503 companies
    confirmed live via the SEC frames API to report both split tags in
    the same period. Verified real sum: $21,000,000 + $10,009,000,000 =
    $10,030,000,000."""
    facts_json = _load_fixture("facts_sum_real_truist_2023.json")
    records = parse_company_facts(
        facts_json, "TRUIST FINANCIAL CORPORATION", "TFC", "0000092230"
    )
    assert len(records) == 1
    assert records[0].sales_maturities_of_marketable_securities == 21000000 + 10009000000
    assert records[0].sales_maturities_of_marketable_securities == 10030000000
