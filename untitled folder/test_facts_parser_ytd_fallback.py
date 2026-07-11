"""Milestone 14.1: extraction-side YTD-duration fallback + provenance.

Tests `_best_value_by_accession_for_tag_with_ytd_fallback` and its wiring
into `_best_value_by_accession`/`_by_key_for_concept`/`parse_company_facts`
(the new `ytd_fallback` field on `FilingRecord`). Real-data-pinned fixtures
(GOOGL, AMZN) confirmed live against SEC EDGAR during Milestone 14's
planning/implementation pass -- see EXECUTION_LOG.md for the exact fetch
commands.
"""

from __future__ import annotations

import json
from pathlib import Path

from sec_financials.facts_parser import (
    YTD_DURATION_BANDS_BY_FISCAL_PERIOD,
    _best_value_by_accession_for_tag_with_ytd_fallback,
    parse_company_facts,
)

FIXTURES_DIR = Path(__file__).parent / "fixtures"


def _load_fixture(name: str) -> dict:
    with (FIXTURES_DIR / name).open() as f:
        return json.load(f)


def test_ytd_duration_bands_shape():
    assert YTD_DURATION_BANDS_BY_FISCAL_PERIOD["Q2"] == (170, 190)
    assert YTD_DURATION_BANDS_BY_FISCAL_PERIOD["Q3"] == (260, 280)
    assert "Q1" not in YTD_DURATION_BANDS_BY_FISCAL_PERIOD


def test_googl_operating_cash_flow_q2_q3_fall_back_to_raw_ytd():
    """GOOGL's real Operating Cash Flow accessions have NO discrete ~90-day
    fact for Q2/Q3 -- only YTD. Confirmed live: FY2024 Q1 (discrete, 90
    days) = $28,848M; FY2024 Q2 (accn 0001652044-24-000079, YTD, 181 days)
    raw = $55,488M; FY2024 Q3 (accn 0001652044-24-000118, YTD, 273 days)
    raw = $86,186M."""
    facts_json = _load_fixture("facts_ytd_fallback_googl_ocf.json")
    facts = facts_json["facts"]
    result = _best_value_by_accession_for_tag_with_ytd_fallback(
        facts, "NetCashProvidedByUsedInOperatingActivities", ["10-Q"]
    )

    q1_key = ("0001652044-24-000053", "10-Q")
    q2_key = ("0001652044-24-000079", "10-Q")
    q3_key = ("0001652044-24-000118", "10-Q")

    # Q1: discrete fact used directly, NOT marked as a fallback.
    assert result[q1_key]["val"] == 28848000000
    assert "_ytd_fallback" not in result[q1_key]

    # Q2/Q3: no discrete survivor -- falls back to the raw YTD figure,
    # tagged so downstream code can tell.
    assert result[q2_key]["val"] == 55488000000
    assert result[q2_key]["_ytd_fallback"] is True
    assert result[q3_key]["val"] == 86186000000
    assert result[q3_key]["_ytd_fallback"] is True


def test_amzn_operating_cash_flow_is_a_true_no_op():
    """AMZN's real accession 0001018724-24-000130 (Q2 2024) has BOTH a
    90-day discrete fact ($25,281M) and a 181-day YTD fact ($44,270M) --
    the existing discrete-band filter already picks the correct 90-day
    entry. The new fallback-capable function must return the exact same
    result as the plain discrete-only function for this accession: a true
    no-op, no `_ytd_fallback` marker at all."""
    facts_json = _load_fixture("facts_ytd_fallback_amzn_ocf_noop.json")
    facts = facts_json["facts"]

    from sec_financials.facts_parser import _best_value_by_accession_for_tag

    discrete_only = _best_value_by_accession_for_tag(
        facts, "NetCashProvidedByUsedInOperatingActivities", ["10-Q"]
    )
    with_fallback = _best_value_by_accession_for_tag_with_ytd_fallback(
        facts, "NetCashProvidedByUsedInOperatingActivities", ["10-Q"]
    )

    key = ("0001018724-24-000130", "10-Q")
    assert discrete_only[key]["val"] == 25281000000
    assert with_fallback[key]["val"] == 25281000000
    assert "_ytd_fallback" not in with_fallback[key]
    # Byte-for-byte identical dicts (no marker added, no value changed).
    assert with_fallback == discrete_only


def test_mixed_coverage_within_one_concept():
    """GOOGL/AAPL's real Stock-Based Compensation shape: some quarters
    discrete, some YTD-only, within the SAME concept. Confirmed live: accn
    0001652044-17-000026 (fy2017 Q2) has a discrete 90-day fact
    ($2,003M); accn 0001652044-19-000023 (fy2019 Q2) is YTD-only
    ($5,525M raw)."""
    facts_json = _load_fixture("facts_ytd_fallback_mixed_coverage.json")
    facts = facts_json["facts"]
    result = _best_value_by_accession_for_tag_with_ytd_fallback(
        facts, "ShareBasedCompensation", ["10-Q"]
    )

    discrete_key = ("0001652044-17-000026", "10-Q")
    ytd_key = ("0001652044-19-000023", "10-Q")

    assert result[discrete_key]["val"] == 2003000000
    assert "_ytd_fallback" not in result[discrete_key]

    assert result[ytd_key]["val"] == 5525000000
    assert result[ytd_key]["_ytd_fallback"] is True


def test_missing_q1_does_not_error_at_extraction_layer():
    """A YTD present for Q2 but no Q1 record at all in the company's
    facts: the extraction layer must populate `ytd_fallback` without
    erroring. Downstream blanking of the derived Q2 value (since there's
    no Q1 to subtract) is tested at the consumption layer (14.2/14.3),
    not here."""
    facts = {
        "us-gaap": {
            "NetCashProvidedByUsedInOperatingActivities": {
                "units": {
                    "USD": [
                        {
                            "start": "2024-01-01",
                            "end": "2024-06-30",
                            "val": 999,
                            "accn": "0000000000-24-000001",
                            "fy": 2024,
                            "fp": "Q2",
                            "form": "10-Q",
                            "filed": "2024-07-01",
                        }
                    ]
                }
            }
        }
    }
    result = _best_value_by_accession_for_tag_with_ytd_fallback(
        facts, "NetCashProvidedByUsedInOperatingActivities", ["10-Q"]
    )
    key = ("0000000000-24-000001", "10-Q")
    assert result[key]["val"] == 999
    assert result[key]["_ytd_fallback"] is True


def test_ytd_fallback_marker_round_trips_into_filing_record():
    """parse_company_facts must populate FilingRecord.ytd_fallback with
    {concept_key: raw_ytd_value} for concepts that hit the fallback path,
    and leave it None for filings where nothing did (e.g. AMZN's, or any
    record with only discrete facts)."""
    facts_json = _load_fixture("facts_ytd_fallback_googl_ocf.json")
    records = parse_company_facts(facts_json, "Alphabet Inc.", "GOOGL", "0001652044", forms=["10-Q"])

    by_period = {r.fiscal_period: r for r in records}

    q1 = by_period["Q1"]
    assert q1.operating_cash_flow == 28848000000
    assert q1.ytd_fallback is None  # discrete fact, no fallback hit

    q2 = by_period["Q2"]
    assert q2.operating_cash_flow == 55488000000  # raw YTD, not yet subtracted (14.2's job)
    assert q2.ytd_fallback == {"operating_cash_flow": 55488000000}

    q3 = by_period["Q3"]
    assert q3.operating_cash_flow == 86186000000
    assert q3.ytd_fallback == {"operating_cash_flow": 86186000000}


def test_amzn_records_have_no_ytd_fallback_populated_anywhere():
    """Regression: AMZN-shaped records (real discrete facts everywhere)
    must have ytd_fallback == None on every record -- confirms the no-op
    property holds all the way through parse_company_facts, not just at
    the low-level extraction function."""
    facts_json = _load_fixture("facts_ytd_fallback_amzn_ocf_noop.json")
    records = parse_company_facts(facts_json, "Amazon.com Inc.", "AMZN", "0001018724", forms=["10-Q"])
    assert len(records) == 1
    assert records[0].operating_cash_flow == 25281000000
    assert records[0].ytd_fallback is None


def test_income_statement_concept_unaffected_net_income_discrete_every_filing():
    """Confirmed live (per PLAN.md): Net Income has a discrete 90-day fact
    in every Q2/Q3 filing checked across GOOGL/AAPL/AMZN -- Income
    Statement concepts need no change and should show zero ytd_fallback
    entries even though they run through the same (no-op-for-them)
    fallback-capable function."""
    facts_json = {
        "facts": {
            "us-gaap": {
                "NetIncomeLoss": {
                    "units": {
                        "USD": [
                            {
                                "start": "2024-04-01",
                                "end": "2024-06-30",
                                "val": 23619000000,
                                "accn": "0001652044-24-000079",
                                "fy": 2024,
                                "fp": "Q2",
                                "form": "10-Q",
                                "filed": "2024-07-24",
                            },
                            {
                                "start": "2024-01-01",
                                "end": "2024-06-30",
                                "val": 44795000000,
                                "accn": "0001652044-24-000079",
                                "fy": 2024,
                                "fp": "Q2",
                                "form": "10-Q",
                                "filed": "2024-07-24",
                            },
                        ]
                    }
                }
            }
        }
    }
    records = parse_company_facts(facts_json, "Alphabet Inc.", "GOOGL", "0001652044", forms=["10-Q"])
    assert len(records) == 1
    assert records[0].net_income == 23619000000  # the discrete 90-day fact, not the YTD one
    assert records[0].ytd_fallback is None


def test_sum_concept_does_not_mix_discrete_and_ytd_bases():
    """SUM concepts remain discrete-only. A generic YTD fallback could
    otherwise combine a discrete summand with a YTD summand into a total
    that cannot safely be converted into a standalone quarter later."""
    facts_json = _load_fixture("facts_ytd_fallback_sum_mixed_basis.json")
    records = parse_company_facts(
        facts_json, "Mixed Basis Sum Co", "MBS", "0000999040", forms=["10-Q"]
    )

    assert len(records) == 1
    record = records[0]
    # Ordinary DURATION concepts still use the fallback and retain raw
    # provenance; only the multi-tag SUM concept is protected.
    assert record.operating_cash_flow == 999
    assert record.ytd_fallback == {"operating_cash_flow": 999}
    # The discrete summand still follows the pre-existing SUM rule; the
    # YTD-only sibling is not folded in (which would have produced 400).
    assert record.sales_maturities_of_marketable_securities == 100
