"""Milestone 14.3: xlsx consumption of Milestone 14.1/14.2's YTD-fallback
derivation.

`build_pivot`/`_cell_plan_for` need NO code change to correctly show a
derived Q2/Q3 value (Milestone 14.2 already overwrote the field on the
record before `build_pivot` runs) -- these tests confirm that is actually
true against real, live-verified GOOGL figures, confirm the pre-existing
"silently wrong Q4" bug is now fixed as a side effect once Q2/Q3 are
populated, and confirm AMZN-shaped (no `ytd_fallback` anywhere) input
produces a byte-identical pivot to before this milestone.
"""

from __future__ import annotations

from sec_financials.facts_parser import FilingRecord, derive_ytd_fallback_values
from sec_financials.xlsx_writer import build_pivot, column_letter


def _record(fiscal_year, fiscal_period, form="10-Q", operating_cash_flow=None, ytd_fallback=None):
    return FilingRecord(
        company="Alphabet Inc.",
        ticker="GOOGL",
        cik="0001652044",
        form=form,
        fiscal_year=fiscal_year,
        fiscal_period=fiscal_period,
        period_end=f"{fiscal_year}-01-01",
        filed=f"{fiscal_year}-01-01",
        revenue=None,
        cost_of_sales=None,
        gross_profit=None,
        operating_cash_flow=operating_cash_flow,
        ytd_fallback=ytd_fallback,
    )


def _ocf_row(pivot):
    for section, rows in pivot.sections:
        if section != "Cash Flow":
            continue
        for row in rows:
            if row.field_name == "operating_cash_flow":
                return row
    raise AssertionError("Operating Cash Flow row not found")


def test_googl_real_operating_cash_flow_q2_q3_cells_show_derived_values():
    """Real, live-verified GOOGL FY2024 figures, run end-to-end through
    derive_ytd_fallback_values -> build_pivot: Q1 discrete = $28,848M; Q2
    raw YTD $55,488M -> derived $26,640M; Q3 raw YTD $86,186M -> derived
    $30,698M (= 86,186 - 26,640 - 28,848)."""
    records = [
        # Prior fiscal year, so FY2024 isn't the window's oldest year
        # (_period_columns_for_window only emits Q1-Q4 columns for
        # non-oldest years) -- FY2023 here is a plain FY-only row, exactly
        # like the real oldest-window-year shape this project already
        # handles elsewhere.
        _record(2023, "FY", form="10-K", operating_cash_flow=90000000000),
        _record(2024, "Q1", operating_cash_flow=28848000000),
        _record(
            2024,
            "Q2",
            operating_cash_flow=55488000000,
            ytd_fallback={"operating_cash_flow": 55488000000},
        ),
        _record(
            2024,
            "Q3",
            operating_cash_flow=86186000000,
            ytd_fallback={"operating_cash_flow": 86186000000},
        ),
        _record(2024, "FY", form="10-K", operating_cash_flow=120000000000),
    ]
    derived = derive_ytd_fallback_values(records)
    pivot = build_pivot(derived, num_years=5)
    row = _ocf_row(pivot)

    by_label = {col.label: plan for col, plan in row.cells.items()}
    assert by_label["Q1"].value == 28848000000
    assert by_label["Q2"].value == 26640000000
    assert by_label["Q3"].value == 30698000000
    # Cross-check against an independent computation (not the code's own
    # arithmetic re-read): 55,488 - 28,848 = 26,640; 86,186 - 26,640 -
    # 28,848 = 30,698.
    assert by_label["Q2"].value == 55488000000 - 28848000000
    assert by_label["Q3"].value == 86186000000 - 26640000000 - 28848000000


def test_q4_plug_formula_is_now_generated_and_arithmetically_correct():
    """The pre-existing 'silently wrong Q4' bug: once Q2/Q3 are populated
    (non-blank), the Q4-plug eligibility condition
    (all(k in letters for k in ("FY","Q1","Q2","Q3"))) finds real cells to
    reference where it previously found none, and the resulting formula
    now computes the arithmetically CORRECT Q4 -- not the old silently
    -wrong "FY - Q1 only" behavior."""
    q1, q2_raw, q3_raw, fy = 100, 250, 420, 520
    records = [
        _record(2023, "FY", form="10-K", operating_cash_flow=900),  # oldest window year, FY-only
        _record(2024, "Q1", operating_cash_flow=q1),
        _record(2024, "Q2", operating_cash_flow=q2_raw, ytd_fallback={"operating_cash_flow": q2_raw}),
        _record(2024, "Q3", operating_cash_flow=q3_raw, ytd_fallback={"operating_cash_flow": q3_raw}),
        _record(2024, "FY", form="10-K", operating_cash_flow=fy),
    ]
    derived = derive_ytd_fallback_values(records)
    pivot = build_pivot(derived, num_years=5)
    row = _ocf_row(pivot)

    by_label = {col.label: (col, plan) for col, plan in row.cells.items()}
    q4_col, q4_plan = by_label["Q4"]
    assert q4_plan.value is None
    assert q4_plan.formula is not None
    assert q4_plan.formula.startswith("=")
    assert "SUM(" in q4_plan.formula

    # Cell-reference cross-check (same technique used in Milestones
    # 12.5/13.x): confirm the formula's own cell operands resolve to the
    # correct row/column for FY, Q1, Q3 now that Q2/Q3 are real cells.
    fy_col, fy_plan_pair = by_label["FY"]
    q1_col, _ = by_label["Q1"]
    q3_col, _ = by_label["Q3"]
    fy_letter = column_letter(pivot.column_map[fy_col])
    q1_letter = column_letter(pivot.column_map[q1_col])
    q3_letter = column_letter(pivot.column_map[q3_col])
    expected_formula = f"={fy_letter}{row.row_number}-SUM({q1_letter}{row.row_number}:{q3_letter}{row.row_number})"
    assert q4_plan.formula == expected_formula

    # Correct Q4 arithmetic: FY - Q1 - derived Q2 - derived Q3.
    derived_q2 = q2_raw - q1
    derived_q3 = q3_raw - q1 - derived_q2
    correct_q4 = fy - q1 - derived_q2 - derived_q3
    # What the OLD (pre-Milestone-14) behavior would have silently
    # computed: Excel's SUM() over blank Q2/Q3 cells treats them as 0, so
    # Q4 would have come out as FY - Q1 only.
    old_wrong_q4 = fy - q1
    assert correct_q4 != old_wrong_q4
    assert correct_q4 == fy - (q1 + derived_q2 + derived_q3)


def test_amzn_shaped_pivot_is_byte_identical_to_pre_milestone_behavior():
    """Regression: AMZN-shaped input (real discrete facts everywhere, no
    `ytd_fallback` populated anywhere) must produce the exact same
    PivotResult whether or not it's first passed through
    derive_ytd_fallback_values -- confirms zero pivot-level regression for
    filers that don't need this fallback at all."""
    records = [
        _record(2024, "Q1", operating_cash_flow=100),
        _record(2024, "Q2", operating_cash_flow=200),
        _record(2024, "Q3", operating_cash_flow=300),
        _record(2024, "FY", form="10-K", operating_cash_flow=1000),
    ]
    pivot_without_derivation_step = build_pivot(records, num_years=5)
    pivot_with_derivation_step = build_pivot(derive_ytd_fallback_values(records), num_years=5)

    assert pivot_without_derivation_step == pivot_with_derivation_step
