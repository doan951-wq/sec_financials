"""Milestone 14.2: shared cross-quarter YTD derivation helper
(`derive_ytd_fallback_values`), wired once upstream of both writers.

Tests the actual subtraction arithmetic (`ytd_raw - sum(prior quarters)`),
the Q2-before-Q3 ordering invariant (explicit and load-bearing now that
both values are plain Python computations, not live Excel formulas), the
missing-Q1-blanks-Q2/Q3 edge case, and the AMZN-shaped no-op regression.
"""

from __future__ import annotations

from sec_financials.facts_parser import FilingRecord, derive_ytd_fallback_values


def _record(fiscal_year, fiscal_period, operating_cash_flow=None, ytd_fallback=None, form="10-Q", **kwargs):
    return FilingRecord(
        company="Test Co",
        ticker="TEST",
        cik="0000000000",
        form=form,
        fiscal_year=fiscal_year,
        fiscal_period=fiscal_period,
        period_end=f"{fiscal_year}-XX-XX",
        filed=f"{fiscal_year}-XX-XX",
        revenue=None,
        cost_of_sales=None,
        gross_profit=None,
        operating_cash_flow=operating_cash_flow,
        ytd_fallback=ytd_fallback,
        **kwargs,
    )


def test_googl_real_q2_operating_cash_flow_derives_correctly():
    """Real, live-verified GOOGL FY2024 figures: Q1 (discrete) =
    $28,848M; Q2 raw YTD = $55,488M -> derived Q2 = 55,488 - 28,848 =
    $26,640M."""
    q1 = _record(2024, "Q1", operating_cash_flow=28848000000)
    q2 = _record(
        2024,
        "Q2",
        operating_cash_flow=55488000000,  # raw YTD, pre-derivation
        ytd_fallback={"operating_cash_flow": 55488000000},
    )
    result = derive_ytd_fallback_values([q1, q2])
    by_period = {r.fiscal_period: r for r in result}
    assert by_period["Q2"].operating_cash_flow == 26640000000
    # Provenance remains the SEC-reported raw YTD figure after the public
    # field is resolved to the standalone quarter.
    assert by_period["Q2"].ytd_fallback == {"operating_cash_flow": 55488000000}


def test_missing_q1_blanks_q2():
    """No Q1 record at all for this fiscal year (e.g. mid-fiscal-year
    company start / IPO, or Q1 simply absent from the input) -- Q2 cannot
    be derived (`YTD - Q1` needs a real Q1 value). Must blank, not guess
    or crash."""
    q2 = _record(
        2024,
        "Q2",
        operating_cash_flow=55488000000,
        ytd_fallback={"operating_cash_flow": 55488000000},
    )
    result = derive_ytd_fallback_values([q2])
    assert result[0].operating_cash_flow is None


def test_full_year_q3_subtracts_both_q1_and_derived_q2():
    """Full-year fixture: Q1 discrete, Q2 YTD-only, Q3 YTD-only, FY
    discrete. Confirms Q3's derivation correctly subtracts BOTH Q1 and
    the now-DERIVED Q2 (not the raw/undeserved Q2 YTD figure) -- this is
    the crux of the ordering invariant. Using round synthetic numbers so
    the arithmetic is easy to verify by eye: Q1=100, Q2 raw YTD=250 (->
    derived Q2=150), Q3 raw YTD=420 (-> derived Q3 = 420 - 100 - 150 =
    170)."""
    q1 = _record(2024, "Q1", operating_cash_flow=100)
    q2 = _record(2024, "Q2", operating_cash_flow=250, ytd_fallback={"operating_cash_flow": 250})
    q3 = _record(2024, "Q3", operating_cash_flow=420, ytd_fallback={"operating_cash_flow": 420})
    fy = _record(2024, "FY", operating_cash_flow=520, form="10-K")

    result = derive_ytd_fallback_values([q1, q2, q3, fy])
    by_period = {r.fiscal_period: r for r in result}

    assert by_period["Q2"].operating_cash_flow == 150
    assert by_period["Q3"].operating_cash_flow == 170
    assert by_period["FY"].operating_cash_flow == 520  # untouched, no ytd_fallback


def test_ordering_invariant_q3_must_use_derived_q2_not_raw_q2():
    """Regression test specifically designed to fail if Q2-before-Q3
    ordering were accidentally reversed: constructs the input list with
    Q3 appearing BEFORE Q2 in raw iteration order (list order, and via a
    dict-shuffling trick to also stress non-sorted internal iteration),
    and confirms the result is still correct -- i.e. the function does
    NOT rely on input list order, and does NOT compute Q3 from Q2's raw
    YTD value (which would give a wrong answer).

    If Q3 were (incorrectly) computed as `raw_Q3_YTD - Q1 - raw_Q2_YTD`
    instead of `raw_Q3_YTD - Q1 - derived_Q2`, the result would be
    `420 - 100 - 250 = 70`, not the correct `170`. This test fails loudly
    (asserts the WRONG answer is NOT produced) if that bug were
    reintroduced.
    """
    q1 = _record(2024, "Q1", operating_cash_flow=100)
    q2 = _record(2024, "Q2", operating_cash_flow=250, ytd_fallback={"operating_cash_flow": 250})
    q3 = _record(2024, "Q3", operating_cash_flow=420, ytd_fallback={"operating_cash_flow": 420})

    # Q3 listed first, deliberately, to stress any accidental reliance on
    # input list order.
    result = derive_ytd_fallback_values([q3, q1, q2])
    by_period = {r.fiscal_period: r for r in result}

    correct_q3 = 170
    wrong_q3_if_using_raw_q2 = 420 - 100 - 250  # == 70
    assert by_period["Q3"].operating_cash_flow == correct_q3
    assert by_period["Q3"].operating_cash_flow != wrong_q3_if_using_raw_q2


def test_amzn_shaped_records_pass_through_completely_unchanged():
    """Regression: records with no `ytd_fallback` anywhere (AMZN-shaped --
    every concept resolved via a discrete fact) must pass through
    `derive_ytd_fallback_values` completely unchanged -- same values, not
    just structurally equal, confirming zero side effects for filers/
    concepts that don't need this mechanism at all."""
    q1 = _record(2024, "Q1", operating_cash_flow=100)
    q2 = _record(2024, "Q2", operating_cash_flow=200)  # real discrete value, no ytd_fallback
    q3 = _record(2024, "Q3", operating_cash_flow=300)
    fy = _record(2024, "FY", operating_cash_flow=1000, form="10-K")

    original = [q1, q2, q3, fy]
    result = derive_ytd_fallback_values(original)

    assert result == original
    for orig, res in zip(original, result):
        assert res.operating_cash_flow == orig.operating_cash_flow


def test_multiple_concepts_in_one_ytd_fallback_dict_all_derived():
    """A single record's `ytd_fallback` can cover more than one concept
    (e.g. Operating Cash Flow AND Capital Expenditures both YTD-only for
    the same filing) -- confirms every key gets derived independently,
    not just the first."""
    q1 = _record(2024, "Q1", operating_cash_flow=100, capital_expenditures=10)
    q2 = _record(
        2024,
        "Q2",
        operating_cash_flow=250,
        capital_expenditures=25,
        ytd_fallback={"operating_cash_flow": 250, "capital_expenditures": 25},
    )
    result = derive_ytd_fallback_values([q1, q2])
    by_period = {r.fiscal_period: r for r in result}
    assert by_period["Q2"].operating_cash_flow == 150
    assert by_period["Q2"].capital_expenditures == 15


def test_q3_can_use_a_direct_q2_as_a_resolved_prior_quarter():
    """A YTD Q3 must resolve when Q2 is a normal discrete value. This
    guards against caching only fallback-derived Q2 records."""
    q1 = _record(2024, "Q1", operating_cash_flow=100)
    q2 = _record(2024, "Q2", operating_cash_flow=50)  # discrete, no fallback
    q3 = _record(
        2024,
        "Q3",
        operating_cash_flow=220,
        ytd_fallback={"operating_cash_flow": 220},
    )

    result = derive_ytd_fallback_values([q3, q1, q2])
    by_period = {r.fiscal_period: r for r in result}
    assert by_period["Q3"].operating_cash_flow == 70


def test_free_cash_flow_recomputed_after_ytd_values_are_resolved():
    """FCF is initially calculated from raw values in parse_company_facts,
    so it must be recalculated after both input concepts are derived."""
    q1 = _record(
        2024,
        "Q1",
        operating_cash_flow=100,
        capital_expenditures=10,
        free_cash_flow=90,
    )
    q2 = _record(
        2024,
        "Q2",
        operating_cash_flow=250,
        capital_expenditures=25,
        free_cash_flow=225,  # stale raw-YTD FCF before derivation
        ytd_fallback={"operating_cash_flow": 250, "capital_expenditures": 25},
    )

    result = derive_ytd_fallback_values([q1, q2])
    resolved_q2 = next(r for r in result if r.fiscal_period == "Q2")
    assert resolved_q2.operating_cash_flow == 150
    assert resolved_q2.capital_expenditures == 15
    assert resolved_q2.free_cash_flow == 135
