"""Tests for Milestone 13.3 (Capability 2): the Beginning Cash
cross-period derivation mechanism in `xlsx_writer.py` -- the
`_prior_period_column`/`_prior_column_cell` adjacency helper and the
"Beginning Cash" pivot row `build_pivot` appends to the Cash Flow
section. Pure Python data structures only -- no openpyxl dependency.
"""

from __future__ import annotations

import re

from sec_financials.facts_parser import FilingRecord
from sec_financials.xlsx_writer import (
    BEGINNING_CASH_LABEL,
    PeriodColumn,
    _prior_column_cell,
    _prior_period_column,
    build_pivot,
    column_letter,
)


def _record(fiscal_year, fiscal_period, form, ending_cash=None, period_end=None):
    return FilingRecord(
        company="AMAZON COM INC",
        ticker="AMZN",
        cik="0001018724",
        form=form,
        fiscal_year=fiscal_year,
        fiscal_period=fiscal_period,
        period_end=period_end,
        filed=None,
        revenue=None,
        cost_of_sales=None,
        gross_profit=None,
        ending_cash=ending_cash,
    )


# Real AMZN figures, verified live against Amazon's companyfacts JSON
# during this milestone's implementation (see EXECUTION_LOG.md): Q1 2025
# 10-Q accession 0001018724-25-000036 reports end=2025-03-31 val
# $69,893M (Q1 2025's own Ending Cash) and end=2024-12-31 val $82,312M
# (FY2024's Ending Cash) in the same accession -- the exact real-world
# relationship "Beginning Cash for Q1 2025 == Ending Cash for FY2024"
# this mechanism depends on.
AMZN_RECORDS = [
    _record(2022, "FY", "10-K", ending_cash=53888000000, period_end="2022-12-31"),
    _record(2023, "Q1", "10-Q", ending_cash=49734000000, period_end="2023-03-31"),
    _record(2023, "Q2", "10-Q", ending_cash=49529000000, period_end="2023-06-30"),
    _record(2023, "Q3", "10-Q", ending_cash=49605000000, period_end="2023-09-30"),
    _record(2023, "FY", "10-K", ending_cash=73387000000, period_end="2023-12-31"),
    _record(2024, "Q1", "10-Q", ending_cash=72852000000, period_end="2024-03-31"),
    _record(2024, "Q2", "10-Q", ending_cash=71178000000, period_end="2024-06-30"),
    _record(2024, "Q3", "10-Q", ending_cash=75091000000, period_end="2024-09-30"),
    _record(2024, "FY", "10-K", ending_cash=82312000000, period_end="2024-12-31"),
    _record(2025, "Q1", "10-Q", ending_cash=69893000000, period_end="2025-03-31"),
]


def _cash_flow_rows(pivot):
    for title, rows in pivot.sections:
        if title == "Cash Flow":
            return rows
    raise AssertionError("Cash Flow section not found")


def _row(pivot, label):
    for row in _cash_flow_rows(pivot):
        if row.label == label:
            return row
    raise AssertionError(f"Row {label!r} not found in Cash Flow section")


# -- Beginning Cash row existence/placement ----------------------------------


def test_beginning_cash_row_appended_after_ending_cash_in_cash_flow_section():
    pivot = build_pivot(AMZN_RECORDS, num_years=5)
    rows = _cash_flow_rows(pivot)
    labels = [r.label for r in rows]
    assert "Ending Cash" in labels
    assert "Beginning Cash" in labels
    ending_idx = labels.index("Ending Cash")
    beginning_idx = labels.index("Beginning Cash")
    assert beginning_idx == ending_idx + 1, "Beginning Cash must immediately follow Ending Cash"


def test_beginning_cash_row_number_is_one_more_than_ending_cash():
    pivot = build_pivot(AMZN_RECORDS, num_years=5)
    ending_row = _row(pivot, "Ending Cash")
    beginning_row = _row(pivot, "Beginning Cash")
    assert beginning_row.row_number == ending_row.row_number + 1


# -- exact formula-string assertions, several column-adjacency shapes -------


def test_q2_beginning_cash_references_q1_ending_cash():
    pivot = build_pivot(AMZN_RECORDS, num_years=5)
    beginning_row = _row(pivot, "Beginning Cash")
    ending_row = _row(pivot, "Ending Cash")

    q2_2023 = next(c for c in pivot.columns if c.fiscal_year == 2023 and c.quarter == "Q2")
    q1_2023 = next(c for c in pivot.columns if c.fiscal_year == 2023 and c.quarter == "Q1")

    formula = beginning_row.cells[q2_2023].formula
    expected_cell = f"{column_letter(pivot.column_map[q1_2023])}{ending_row.row_number}"
    assert formula == f"={expected_cell}"


def test_q1_beginning_cash_references_prior_years_fy_ending_cash():
    pivot = build_pivot(AMZN_RECORDS, num_years=5)
    beginning_row = _row(pivot, "Beginning Cash")
    ending_row = _row(pivot, "Ending Cash")

    q1_2023 = next(c for c in pivot.columns if c.fiscal_year == 2023 and c.quarter == "Q1")
    fy_2022 = next(c for c in pivot.columns if c.fiscal_year == 2022 and c.is_fy)

    formula = beginning_row.cells[q1_2023].formula
    expected_cell = f"{column_letter(pivot.column_map[fy_2022])}{ending_row.row_number}"
    assert formula == f"={expected_cell}"


def test_non_oldest_fy_column_beginning_cash_references_prior_fy_column():
    """The critical resolved-decision case: a non-oldest year's own FY
    column's Beginning Cash formula must reference the PRIOR FISCAL
    YEAR'S FY column -- NOT that same year's positionally-adjacent Q4
    column, even though Q4 sits immediately to the FY column's left in
    the rendered sheet."""
    pivot = build_pivot(AMZN_RECORDS, num_years=5)
    beginning_row = _row(pivot, "Beginning Cash")
    ending_row = _row(pivot, "Ending Cash")

    fy_2023 = next(c for c in pivot.columns if c.fiscal_year == 2023 and c.is_fy)
    fy_2022 = next(c for c in pivot.columns if c.fiscal_year == 2022 and c.is_fy)

    formula = beginning_row.cells[fy_2023].formula
    expected_cell = f"{column_letter(pivot.column_map[fy_2022])}{ending_row.row_number}"
    assert formula == f"={expected_cell}"


def test_non_oldest_fy_column_beginning_cash_does_not_reference_own_q4_column():
    """Regression test for the exact wrong-but-plausible bug this
    milestone's resolved decision rules out: FY2023's Beginning Cash must
    NOT reference FY2023's own Q4 column, even though Q4 2023 is
    positionally immediately to FY2023's left in the sheet."""
    pivot = build_pivot(AMZN_RECORDS, num_years=5)
    beginning_row = _row(pivot, "Beginning Cash")

    fy_2023 = next(c for c in pivot.columns if c.fiscal_year == 2023 and c.is_fy)
    q4_2023 = next(
        (c for c in pivot.columns if c.fiscal_year == 2023 and c.quarter == "Q4"), None
    )

    formula = beginning_row.cells[fy_2023].formula
    assert formula is not None

    if q4_2023 is not None:
        wrong_cell_ref = column_letter(pivot.column_map[q4_2023])
        m = re.match(r"^=([A-Z]+)(\d+)$", formula)
        assert m is not None
        referenced_col_letter = m.group(1)
        assert referenced_col_letter != wrong_cell_ref, (
            f"FY2023's Beginning Cash formula {formula!r} incorrectly references "
            f"its own year's Q4 column ({wrong_cell_ref!r}) instead of the prior "
            "fiscal year's FY column"
        )


def test_oldest_window_column_beginning_cash_is_blank():
    """The window's oldest column (always FY-only) has no predecessor at
    all -- Beginning Cash must be blank (no formula), not an error."""
    pivot = build_pivot(AMZN_RECORDS, num_years=5)
    beginning_row = _row(pivot, "Beginning Cash")

    oldest_year = min(c.fiscal_year for c in pivot.columns)
    oldest_col = next(c for c in pivot.columns if c.fiscal_year == oldest_year and c.is_fy)

    cell_plan = beginning_row.cells[oldest_col]
    assert cell_plan.is_blank
    assert cell_plan.formula is None


def test_oldest_window_beginning_cash_uses_prior_fy_ending_cash_when_available():
    records = [
        _record(2021, "FY", "10-K", ending_cash=36477000000, period_end="2021-12-31"),
        *AMZN_RECORDS,
        _record(2026, "Q1", "10-Q", ending_cash=80000000000, period_end="2026-03-31"),
    ]
    pivot = build_pivot(records, num_years=5)
    beginning_row = _row(pivot, "Beginning Cash")
    fy_2022 = next(c for c in pivot.columns if c.fiscal_year == 2022 and c.is_fy)
    assert beginning_row.cells[fy_2022].value == 36477000000
    assert beginning_row.cells[fy_2022].formula is None


# -- cell-reference cross-check (Milestone 12.5 verification technique 4) --


def test_every_beginning_cash_formula_resolves_to_correct_prior_period_row_and_column():
    """Cross-check every generated Beginning Cash formula's referenced
    cell against the pivot's own column_map, confirming it actually
    resolves to the Ending Cash row in the correct adjacent period
    column -- the same off-by-one risk class already caught real bugs in
    this project twice before (Milestone 12.2's Q4-column rule,
    Milestone 12.3b's section-row-collision bug)."""
    pivot = build_pivot(AMZN_RECORDS, num_years=5)
    beginning_row = _row(pivot, "Beginning Cash")
    ending_row = _row(pivot, "Ending Cash")

    index_to_col = {idx: col for col, idx in pivot.column_map.items()}
    col_letter_to_index = {column_letter(idx): idx for idx in index_to_col}

    pattern = re.compile(r"^=([A-Z]+)(\d+)$")
    checked = 0
    for col in pivot.columns:
        cell_plan = beginning_row.cells[col]
        expected_prior = _prior_period_column(
            col, _columns_by_year(pivot.columns)
        )
        if expected_prior is None:
            assert cell_plan.formula is None
            continue

        m = pattern.match(cell_plan.formula)
        assert m, f"Formula didn't match expected shape: {cell_plan.formula!r}"
        letter, row_str = m.groups()

        assert int(row_str) == ending_row.row_number
        referenced_col = index_to_col[col_letter_to_index[letter]]
        assert referenced_col == expected_prior
        checked += 1

    assert checked > 0


def _columns_by_year(columns):
    by_year: dict[int, list[PeriodColumn]] = {}
    for c in columns:
        by_year.setdefault(c.fiscal_year, []).append(c)
    return by_year


# -- direct unit tests for the adjacency helper itself -----------------------


def test_prior_period_column_q2_predecessor_is_q1():
    columns = [
        PeriodColumn(2023, "Q1"),
        PeriodColumn(2023, "Q2"),
    ]
    by_year = _columns_by_year(columns)
    assert _prior_period_column(PeriodColumn(2023, "Q2"), by_year) == PeriodColumn(2023, "Q1")


def test_prior_period_column_q1_predecessor_is_prior_year_fy():
    columns = [
        PeriodColumn(2022, None),
        PeriodColumn(2023, "Q1"),
    ]
    by_year = _columns_by_year(columns)
    assert _prior_period_column(PeriodColumn(2023, "Q1"), by_year) == PeriodColumn(2022, None)


def test_prior_period_column_non_oldest_fy_predecessor_is_prior_fy_not_own_q4():
    columns = [
        PeriodColumn(2022, None),
        PeriodColumn(2023, "Q1"),
        PeriodColumn(2023, "Q2"),
        PeriodColumn(2023, "Q3"),
        PeriodColumn(2023, "Q4"),
        PeriodColumn(2023, None),
    ]
    by_year = _columns_by_year(columns)
    result = _prior_period_column(PeriodColumn(2023, None), by_year)
    assert result == PeriodColumn(2022, None)
    assert result != PeriodColumn(2023, "Q4")


def test_prior_period_column_oldest_fy_has_no_predecessor():
    columns = [PeriodColumn(2022, None)]
    by_year = _columns_by_year(columns)
    assert _prior_period_column(PeriodColumn(2022, None), by_year) is None


def test_prior_column_cell_returns_none_when_no_predecessor():
    columns = [PeriodColumn(2022, None)]
    by_year = _columns_by_year(columns)
    column_map = {PeriodColumn(2022, None): 3}
    assert _prior_column_cell(PeriodColumn(2022, None), 155, by_year, column_map) is None


def test_prior_column_cell_builds_correct_reference():
    columns = [PeriodColumn(2022, None), PeriodColumn(2023, "Q1")]
    by_year = _columns_by_year(columns)
    column_map = {PeriodColumn(2022, None): 3, PeriodColumn(2023, "Q1"): 4}
    cell = _prior_column_cell(PeriodColumn(2023, "Q1"), 155, by_year, column_map)
    assert cell == "C155"
