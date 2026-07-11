"""Tests for Milestone 15.3: converting `depreciation_and_amortization`
from a plain single-tag DURATION concept to a SUM-kind ConceptSpec,
reusing `_sum_value_by_accession` (Milestone 13.2) unchanged. GOOGL (CIK
0001652044) has ZERO facts under `DepreciationDepletionAndAmortization`
(404) but real coverage under `Depreciation` (23 facts) and
`AmortizationOfIntangibleAssets` (2 facts, sparse/recent-periods-only) --
see PLAN.md's Milestone 15 section for the live-verification numbers.

**STATUS: conversion drafted, then REVERTED after its required live
non-regression check (see PLAN.md's 15.3 non-regression requirement)
found a genuine regression, not a false alarm:**

- AAPL and AMZN both have real accessions where
  `AmortizationOfIntangibleAssets` is present but `Depreciation` is
  absent for that same accession (e.g. AAPL FY2017 10-K:
  `AmortizationOfIntangibleAssets` = $1.2B present, `Depreciation`
  absent, `DepreciationDepletionAndAmortization` = $8.2B). The SUM
  mechanism's "missing summand treated as 0" rule would silently replace
  the correct $8.2B combined-tag value with just $1.2B -- a wrong-value
  regression on an already-shipped, already-correct figure, not a
  blank-vs-populated coverage gap.
- Separately, SUM-kind concepts are deliberately excluded from the
  Milestone 14.1 YTD-duration fallback (see `_sum_value_by_accession`'s
  docstring), but AAPL's Q2/Q3 D&A currently renders correctly ONLY
  because of that fallback (AAPL reports
  `DepreciationDepletionAndAmortization` in YTD-cumulative-only shape,
  with no discrete quarter fact, for its Q2/Q3 10-Qs from 2019 onward).
  Converting to SUM-kind would blank out AAPL's Q2/Q3 D&A that renders
  correctly today.

`depreciation_and_amortization` therefore remains its original plain
single-tag DURATION `ConceptSpec`, unchanged, in `facts_parser.py`. The
tests below are kept (not deleted) as a record of the mechanism that was
verified NOT to work for this concept as originally scoped, and are
skipped so the file documents the finding without failing the suite --
see EXECUTION_LOG.md for the full live-data evidence and the report back
to the user. Do not re-enable without a resolved decision on how to
represent GOOGL's `Depreciation`/`AmortizationOfIntangibleAssets` split
without regressing AAPL/AMZN."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from sec_financials.facts_parser import CONCEPT_BY_KEY, SUM, parse_company_facts

FIXTURES_DIR = Path(__file__).parent / "fixtures"

pytestmark = pytest.mark.skip(
    reason=(
        "Milestone 15.3's SUM-kind conversion was reverted after its "
        "required live non-regression check found a genuine regression "
        "for AAPL/AMZN (see module docstring and EXECUTION_LOG.md). "
        "depreciation_and_amortization remains a plain DURATION concept."
    )
)


def _load_fixture(name: str) -> dict:
    with (FIXTURES_DIR / name).open("r", encoding="utf-8") as f:
        return json.load(f)


def test_depreciation_and_amortization_is_now_sum_kind():
    spec = CONCEPT_BY_KEY["depreciation_and_amortization"]
    assert spec.kind == SUM
    assert spec.sum_tags == ["Depreciation", "AmortizationOfIntangibleAssets"]
    assert spec.tag_priority == ["DepreciationDepletionAndAmortization"]


def test_both_summands_present_are_summed():
    facts_json = _load_fixture("facts_da_sum_both_present.json")
    records = parse_company_facts(facts_json, "DASumBothPresent Co", "DASBP", "0000999050")
    assert len(records) == 1
    assert records[0].depreciation_and_amortization == 7000000000 + 500000000


def test_only_depreciation_present_renders_that_value_not_blank():
    """GOOGL's actual asymmetric coverage shape: `Depreciation` goes back
    much further than `AmortizationOfIntangibleAssets` (23 vs. 2 facts).
    For periods where only `Depreciation` exists, the concept must render
    as `Depreciation` alone (missing summand treated as 0), not blank."""
    facts_json = _load_fixture("facts_da_sum_only_depreciation_present.json")
    records = parse_company_facts(
        facts_json, "DASumOnlyDepreciation Co", "DASOD", "0000999051"
    )
    assert len(records) == 1
    assert records[0].depreciation_and_amortization == 4200000000


def test_neither_summand_nor_fallback_present_is_blank():
    facts_json = _load_fixture("facts_da_sum_neither_summand_nor_fallback.json")
    records = parse_company_facts(
        facts_json, "DANeitherSummandNorFallback Co", "DANSF", "0000999052"
    )
    assert records
    for r in records:
        assert r.depreciation_and_amortization is None


def test_combined_tag_only_zero_summands_falls_back_correctly():
    """Dedicated non-regression fixture, distinct from the
    neither-summand-nor-fallback blank case above: a filing that reports
    ONLY the combined tag (`DepreciationDepletionAndAmortization`) and
    NEITHER component tag at all -- exactly AAPL's real, live-verified
    FY2018 10-K shape (confirmed live: that filing has zero
    `Depreciation`/`AmortizationOfIntangibleAssets` facts, only the
    combined tag, value $10,903,000,000). Must degrade to the fallback
    value, identical to this concept's pre-Milestone-15.3 (plain DURATION,
    single-tag) behavior -- not blank."""
    facts_json = _load_fixture("facts_da_sum_combined_tag_only_no_summands.json")
    records = parse_company_facts(
        facts_json, "DACombinedTagOnly Co", "DACTO", "0000999053"
    )
    assert len(records) == 1
    assert records[0].depreciation_and_amortization == 10903000000
