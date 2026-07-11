"""Unit tests for Milestone 12.2: the long-to-wide pivot logic in
`xlsx_writer.py` (trailing-window filter, period-column determination,
Q4-plug formula-string generation). Pure Python data structures only --
no openpyxl dependency, so these tests run in any environment.

Fixtures use real AMZN figures (from the live-fetched
`/Users/tonyledoan/Documents/seccsv/AMZN.csv`, checked 2026-07-07) for the
FY2022-FY2026 window, including the real edge case that most-recent
fiscal year 2026 has only a Q1 10-Q row and NO FY/10-K row yet.
"""

from __future__ import annotations

import pytest

from sec_financials.facts_parser import INSTANT, FilingRecord, derive_ytd_fallback_values
from sec_financials.xlsx_writer import (
    NO_Q4_PLUG_FIELDS,
    PeriodColumn,
    _field_kind,
    _filter_to_trailing_window,
    _period_columns_for_window,
    build_pivot,
    column_letter,
)


def _amzn_record(
    fiscal_year,
    fiscal_period,
    form,
    revenue=None,
    net_income=None,
    eps_diluted=None,
    cash=None,
    total_assets=None,
    operating_cash_flow=None,
    capital_expenditures=None,
    free_cash_flow=None,
    ytd_fallback=None,
    period_end=None,
):
    return FilingRecord(
        company="AMAZON COM INC",
        ticker="AMZN",
        cik="0001018724",
        form=form,
        fiscal_year=fiscal_year,
        fiscal_period=fiscal_period,
        period_end=period_end,
        filed=None,
        revenue=revenue,
        cost_of_sales=None,
        gross_profit=None,
        net_income=net_income,
        eps_diluted=eps_diluted,
        cash=cash,
        total_assets=total_assets,
        operating_cash_flow=operating_cash_flow,
        capital_expenditures=capital_expenditures,
        free_cash_flow=free_cash_flow,
        ytd_fallback=ytd_fallback,
    )


# Real AMZN FY2022-FY2026 shape, verified live against the real CSV
# 2026-07-07: FY2026 (the most recent fiscal year present) has only a Q1
# 10-Q row, no FY/10-K row at all yet.
AMZN_FY2022_2026_RECORDS = [
    _amzn_record(2022, "Q1", "10-Q", revenue=116444000000, net_income=-3844000000, eps_diluted=-7.56, cash=36393000000, total_assets=410767000000, period_end="2022-03-31"),
    _amzn_record(2022, "Q2", "10-Q", revenue=121234000000, net_income=-2028000000, eps_diluted=-0.2, cash=37478000000, total_assets=419728000000, period_end="2022-06-30"),
    _amzn_record(2022, "Q3", "10-Q", revenue=127101000000, net_income=2872000000, eps_diluted=0.28, cash=34947000000, total_assets=428362000000, period_end="2022-09-30"),
    _amzn_record(2022, "FY", "10-K", revenue=513983000000, net_income=-2722000000, eps_diluted=-0.27, cash=53888000000, total_assets=462675000000, period_end="2022-12-31"),
    _amzn_record(2023, "Q1", "10-Q", revenue=127358000000, net_income=3172000000, eps_diluted=0.31, cash=49343000000, total_assets=464378000000, period_end="2023-03-31"),
    _amzn_record(2023, "Q2", "10-Q", revenue=134383000000, net_income=6750000000, eps_diluted=0.65, cash=49529000000, total_assets=477607000000, period_end="2023-06-30"),
    _amzn_record(2023, "Q3", "10-Q", revenue=143083000000, net_income=9879000000, eps_diluted=0.94, cash=49605000000, total_assets=486883000000, period_end="2023-09-30"),
    _amzn_record(2023, "FY", "10-K", revenue=574785000000, net_income=30425000000, eps_diluted=2.9, cash=73387000000, total_assets=527854000000, period_end="2023-12-31"),
    _amzn_record(2024, "Q1", "10-Q", revenue=143313000000, net_income=10431000000, eps_diluted=0.98, cash=72852000000, total_assets=530969000000, period_end="2024-03-31"),
    _amzn_record(2024, "Q2", "10-Q", revenue=147977000000, net_income=13485000000, eps_diluted=1.26, cash=71178000000, total_assets=554818000000, period_end="2024-06-30"),
    _amzn_record(2024, "Q3", "10-Q", revenue=158877000000, net_income=15328000000, eps_diluted=1.43, cash=75091000000, total_assets=584626000000, period_end="2024-09-30"),
    _amzn_record(2024, "FY", "10-K", revenue=637959000000, net_income=59248000000, eps_diluted=5.53, cash=78779000000, total_assets=624894000000, period_end="2024-12-31"),
    _amzn_record(2025, "Q1", "10-Q", revenue=155667000000, net_income=17127000000, eps_diluted=1.59, cash=66207000000, total_assets=643256000000, period_end="2025-03-31"),
    _amzn_record(2025, "Q2", "10-Q", revenue=167702000000, net_income=18164000000, eps_diluted=1.68, cash=57741000000, total_assets=682170000000, period_end="2025-06-30"),
    _amzn_record(2025, "Q3", "10-Q", revenue=180169000000, net_income=21187000000, eps_diluted=1.95, cash=66922000000, total_assets=727921000000, period_end="2025-09-30"),
    _amzn_record(2025, "FY", "10-K", revenue=716924000000, net_income=77670000000, eps_diluted=7.17, cash=86810000000, total_assets=818042000000, period_end="2025-12-31"),
    _amzn_record(2026, "Q1", "10-Q", revenue=181519000000, net_income=30255000000, eps_diluted=2.78, cash=101816000000, total_assets=916630000000, period_end="2026-03-31"),
]

# Full history: the real AMZN CSV spans FY2009-FY2026 (69 rows). Build a
# synthetic full-history list by prepending a decade of FY-only 10-K rows
# before the real FY2022-2026 window, to exercise the trailing-window
# filter dropping years outside the window from a genuinely longer history.
AMZN_FULL_HISTORY_RECORDS = [
    _amzn_record(year, "FY", "10-K", revenue=year * 1_000_000, period_end=f"{year}-12-31")
    for year in range(2009, 2022)
] + AMZN_FY2022_2026_RECORDS


class TestTrailingWindowFilter:
    def test_full_history_filtered_to_5_years(self):
        windowed = _filter_to_trailing_window(AMZN_FULL_HISTORY_RECORDS, num_years=5)
        years = sorted({int(r.fiscal_year) for r in windowed})
        assert years == [2022, 2023, 2024, 2025, 2026]

    def test_short_history_company_no_padding_no_error(self):
        # Synthetic recent-IPO company: only 2 fiscal years of data.
        records = [
            _amzn_record(2025, "Q1", "10-Q", revenue=100, period_end="2025-03-31"),
            _amzn_record(2025, "FY", "10-K", revenue=500, period_end="2025-12-31"),
            _amzn_record(2026, "Q1", "10-Q", revenue=120, period_end="2026-03-31"),
        ]
        windowed = _filter_to_trailing_window(records, num_years=5)
        years = sorted({int(r.fiscal_year) for r in windowed})
        assert years == [2025, 2026]
        assert len(windowed) == 3  # nothing dropped, nothing padded

    def test_real_amzn_shape_max_year_is_q1_only_no_fy_row(self):
        # Confirms the real, live-verified edge case: FY2026 (max fiscal
        # year) has exactly one row (Q1, 10-Q) and no FY row.
        windowed = _filter_to_trailing_window(AMZN_FY2022_2026_RECORDS, num_years=5)
        fy2026_rows = [r for r in windowed if int(r.fiscal_year) == 2026]
        assert len(fy2026_rows) == 1
        assert fy2026_rows[0].fiscal_period == "Q1"
        assert fy2026_rows[0].form == "10-Q"


class TestPeriodColumnOrdering:
    def test_oldest_window_year_gets_fy_only(self):
        windowed = _filter_to_trailing_window(AMZN_FY2022_2026_RECORDS, num_years=5)
        columns = _period_columns_for_window(windowed)
        fy2022_cols = [c for c in columns if c.fiscal_year == 2022]
        assert len(fy2022_cols) == 1
        assert fy2022_cols[0].is_fy

    def test_newest_window_year_gets_q1_and_blank_fy_column(self):
        windowed = _filter_to_trailing_window(AMZN_FY2022_2026_RECORDS, num_years=5)
        columns = _period_columns_for_window(windowed)
        fy2026_cols = [c for c in columns if c.fiscal_year == 2026]
        labels = [c.label for c in fy2026_cols]
        # Q1 (has a filing) plus FY (present per the edge-case rule, even
        # though no FY filing exists yet) -- no Q2/Q3/Q4 (no filings).
        assert labels == ["Q1", "FY"]

    def test_full_column_order_for_windowed_multiyear_mixed_company(self):
        windowed = _filter_to_trailing_window(AMZN_FY2022_2026_RECORDS, num_years=5)
        columns = _period_columns_for_window(windowed)
        expected = (
            [(2022, "FY")]
            + [(2023, q) for q in ("Q1", "Q2", "Q3", "Q4", "FY")]
            + [(2024, q) for q in ("Q1", "Q2", "Q3", "Q4", "FY")]
            + [(2025, q) for q in ("Q1", "Q2", "Q3", "Q4", "FY")]
            + [(2026, "Q1"), (2026, "FY")]
        )
        actual = [(c.fiscal_year, c.label) for c in columns]
        assert actual == expected


class TestColumnLetter:
    def test_basic_mapping(self):
        assert column_letter(1) == "A"
        assert column_letter(2) == "B"
        assert column_letter(26) == "Z"
        assert column_letter(27) == "AA"
        assert column_letter(28) == "AB"

    def test_rejects_non_positive_index(self):
        with pytest.raises(ValueError):
            column_letter(0)


class TestFieldKindClassification:
    def test_revenue_and_net_income_are_duration(self):
        assert _field_kind("revenue") == "duration"
        assert _field_kind("net_income") == "duration"

    def test_cash_and_total_assets_are_instant(self):
        assert _field_kind("cash") == "instant"
        assert _field_kind("total_assets") == "instant"

    def test_eps_fields_are_duration_but_in_no_q4_plug_set(self):
        assert _field_kind("eps_basic") == "duration"
        assert _field_kind("eps_diluted") == "duration"
        assert "eps_basic" in NO_Q4_PLUG_FIELDS
        assert "eps_diluted" in NO_Q4_PLUG_FIELDS

    def test_free_cash_flow_fallback_classified_duration(self):
        # Not in CONCEPT_TABLE (purely derived) -- must hit the named
        # fallback, not raise.
        assert _field_kind("free_cash_flow") == "duration"

    def test_unknown_field_raises(self):
        with pytest.raises(KeyError):
            _field_kind("not_a_real_field")


class TestQ4FormulaGeneration:
    """The single most important unit-test target per PLAN.md: exact
    formula strings with real column letters/row numbers."""

    def _pivot(self):
        return build_pivot(AMZN_FY2022_2026_RECORDS, num_years=5)

    def _revenue_row(self, pivot):
        income_statement = dict(pivot.sections)["Income Statement"]
        return next(r for r in income_statement if r.field_name == "revenue")

    def _net_income_row(self, pivot):
        income_statement = dict(pivot.sections)["Income Statement"]
        return next(r for r in income_statement if r.field_name == "net_income")

    def test_revenue_q4_formula_fy2023(self):
        pivot = self._pivot()
        revenue_row = self._revenue_row(pivot)
        col_map = pivot.column_map
        q4_col = next(c for c in pivot.columns if c.fiscal_year == 2023 and c.quarter == "Q4")
        fy_col = next(c for c in pivot.columns if c.fiscal_year == 2023 and c.is_fy)
        q1_col = next(c for c in pivot.columns if c.fiscal_year == 2023 and c.quarter == "Q1")
        q3_col = next(c for c in pivot.columns if c.fiscal_year == 2023 and c.quarter == "Q3")

        cell = revenue_row.cells[q4_col]
        assert cell.formula is not None

        fy_letter = column_letter(col_map[fy_col])
        q1_letter = column_letter(col_map[q1_col])
        q3_letter = column_letter(col_map[q3_col])
        expected = f"={fy_letter}{revenue_row.row_number}-SUM({q1_letter}{revenue_row.row_number}:{q3_letter}{revenue_row.row_number})"
        assert cell.formula == expected

    def test_net_income_q4_formula_fy2024(self):
        pivot = self._pivot()
        net_income_row = self._net_income_row(pivot)
        col_map = pivot.column_map
        q4_col = next(c for c in pivot.columns if c.fiscal_year == 2024 and c.quarter == "Q4")
        fy_col = next(c for c in pivot.columns if c.fiscal_year == 2024 and c.is_fy)
        q1_col = next(c for c in pivot.columns if c.fiscal_year == 2024 and c.quarter == "Q1")
        q3_col = next(c for c in pivot.columns if c.fiscal_year == 2024 and c.quarter == "Q3")

        cell = net_income_row.cells[q4_col]
        fy_letter = column_letter(col_map[fy_col])
        q1_letter = column_letter(col_map[q1_col])
        q3_letter = column_letter(col_map[q3_col])
        expected = f"={fy_letter}{net_income_row.row_number}-SUM({q1_letter}{net_income_row.row_number}:{q3_letter}{net_income_row.row_number})"
        assert cell.formula == expected

    def test_oldest_year_fy_only_no_q4_column_at_all(self):
        pivot = self._pivot()
        revenue_row = self._revenue_row(pivot)
        fy2022_cols = [c for c in pivot.columns if c.fiscal_year == 2022]
        assert len(fy2022_cols) == 1
        assert fy2022_cols[0].is_fy
        # No Q4 column for FY2022 at all -- nothing to assert a formula on.

    def test_newest_year_blank_fy_column_is_blank_not_formula(self):
        pivot = self._pivot()
        revenue_row = self._revenue_row(pivot)
        fy2026_fy_col = next(c for c in pivot.columns if c.fiscal_year == 2026 and c.is_fy)
        cell = revenue_row.cells[fy2026_fy_col]
        assert cell.is_blank

    def test_instant_metric_non_q4_columns_never_get_a_formula(self):
        pivot = self._pivot()
        balance_sheet = dict(pivot.sections)["Balance Sheet"]
        cash_row = next(r for r in balance_sheet if r.field_name == "cash")
        for col, cell in cash_row.cells.items():
            if col.quarter == "Q4":
                continue
            assert cell.formula is None

    def test_instant_metric_q4_mirrors_fy_column_not_blank(self):
        # Regression test: instant-kind metrics' Q4 column must NOT be
        # blank for a fully-populated fiscal year -- a fiscal-year-end
        # balance sheet snapshot IS the Q4-end snapshot (same date, same
        # value), so Q4 should be a formula mirroring that year's FY
        # column, e.g. `=L18`, matching the real template's own verified
        # convention (K71='=L71' etc. for Cash & Cash Equivalents).
        pivot = self._pivot()
        col_map = pivot.column_map
        balance_sheet = dict(pivot.sections)["Balance Sheet"]
        cash_row = next(r for r in balance_sheet if r.field_name == "cash")

        for year in (2023, 2024, 2025):
            q4_col = next(c for c in pivot.columns if c.fiscal_year == year and c.quarter == "Q4")
            fy_col = next(c for c in pivot.columns if c.fiscal_year == year and c.is_fy)
            cell = cash_row.cells[q4_col]

            assert cell.formula is not None, f"FY{year} Q4 cash cell is blank, expected a mirror formula"
            assert cell.value is None

            fy_letter = column_letter(col_map[fy_col])
            expected = f"={fy_letter}{cash_row.row_number}"
            assert cell.formula == expected

    def test_eps_diluted_q4_is_blank_no_plug(self):
        pivot = self._pivot()
        per_share = dict(pivot.sections)["Per-Share & Other"]
        eps_row = next(r for r in per_share if r.field_name == "eps_diluted")
        for year in (2023, 2024, 2025):
            q4_col = next(c for c in pivot.columns if c.fiscal_year == year and c.quarter == "Q4")
            cell = eps_row.cells[q4_col]
            assert cell.formula is None
            # No direct Q4 EPS was ever provided in the fixture -> blank.
            assert cell.value is None

    def test_ytd_derived_cash_flow_values_feed_a_correct_q4_plug(self):
        """The pivot receives plain values from the shared parser helper:
        Q2/Q3 stay values (not hybrid formulas) and Q4 references all
        resolved quarters."""
        records = derive_ytd_fallback_values([
            _amzn_record(2023, "FY", "10-K", operating_cash_flow=400),
            _amzn_record(2024, "Q1", "10-Q", operating_cash_flow=100),
            _amzn_record(
                2024, "Q2", "10-Q", operating_cash_flow=250,
                ytd_fallback={"operating_cash_flow": 250},
            ),
            _amzn_record(
                2024, "Q3", "10-Q", operating_cash_flow=420,
                ytd_fallback={"operating_cash_flow": 420},
            ),
            _amzn_record(2024, "FY", "10-K", operating_cash_flow=520),
        ])
        pivot = build_pivot(records, num_years=5)
        cash_flow = dict(pivot.sections)["Cash Flow"]
        row = next(r for r in cash_flow if r.field_name == "operating_cash_flow")
        q2_col = next(c for c in pivot.columns if c.fiscal_year == 2024 and c.quarter == "Q2")
        q3_col = next(c for c in pivot.columns if c.fiscal_year == 2024 and c.quarter == "Q3")
        q4_col = next(c for c in pivot.columns if c.fiscal_year == 2024 and c.quarter == "Q4")
        fy_col = next(c for c in pivot.columns if c.fiscal_year == 2024 and c.is_fy)
        q1_col = next(c for c in pivot.columns if c.fiscal_year == 2024 and c.quarter == "Q1")

        assert row.cells[q2_col].value == 150
        assert row.cells[q2_col].formula is None
        assert row.cells[q3_col].value == 170
        assert row.cells[q3_col].formula is None
        assert row.cells[q4_col].formula == (
            f"={column_letter(pivot.column_map[fy_col])}{row.row_number}"
            f"-SUM({column_letter(pivot.column_map[q1_col])}{row.row_number}:"
            f"{column_letter(pivot.column_map[q3_col])}{row.row_number})"
        )

    def test_q4_plug_is_withheld_when_a_required_quarter_is_blank(self):
        """Excel SUM treats blanks as zero, so a Q4 plug is unsafe unless
        the field has FY, Q1, Q2, and Q3 values -- not merely columns."""
        records = derive_ytd_fallback_values([
            _amzn_record(2023, "FY", "10-K", operating_cash_flow=400),
            _amzn_record(2024, "Q1", "10-Q", operating_cash_flow=None),
            _amzn_record(
                2024, "Q2", "10-Q", operating_cash_flow=250,
                ytd_fallback={"operating_cash_flow": 250},
            ),
            _amzn_record(
                2024, "Q3", "10-Q", operating_cash_flow=420,
                ytd_fallback={"operating_cash_flow": 420},
            ),
            _amzn_record(2024, "FY", "10-K", operating_cash_flow=520),
        ])
        pivot = build_pivot(records, num_years=5)
        cash_flow = dict(pivot.sections)["Cash Flow"]
        row = next(r for r in cash_flow if r.field_name == "operating_cash_flow")
        q4_col = next(c for c in pivot.columns if c.fiscal_year == 2024 and c.quarter == "Q4")

        assert row.cells[q4_col].is_blank


class TestCellReferenceCrossCheck:
    """Milestone 12.5 verification technique 4, exercised here at the
    12.2 pivot level: parse the coordinates out of each generated Q4
    formula and assert they match the pivot's own column map for that
    fiscal year.

    Two formula shapes are expected, by kind, for every Q4-column formula
    cell (Beginning Cash, a third and structurally distinct formula shape
    that is NOT confined to the Q4 column, is excluded from this
    Q4-specific cross-check and has its own dedicated test class below):
    - DURATION metrics' Q4 column: the plug formula
      `=<FY>-SUM(<Q1>:<Q3>)`.
    - INSTANT metrics' Q4 column: the mirror formula `=<FY>` (Milestone
      12.6 bugfix -- a fiscal-year-end balance sheet snapshot IS the
      Q4-end snapshot, so Q4 simply references the FY cell)."""

    def test_all_q4_formulas_reference_correct_columns_for_their_year(self):
        import re

        pivot = build_pivot(AMZN_FY2022_2026_RECORDS, num_years=5)
        col_map = pivot.column_map
        # Build reverse lookup: column index -> PeriodColumn
        index_to_col = {idx: col for col, idx in col_map.items()}
        col_letter_to_index = {column_letter(idx): idx for idx in index_to_col}

        plug_pattern = re.compile(r"^=([A-Z]+)(\d+)-SUM\(([A-Z]+)(\d+):([A-Z]+)(\d+)\)$")
        mirror_pattern = re.compile(r"^=([A-Z]+)(\d+)$")

        checked_plug = False
        checked_mirror = False
        for section_title, rows in pivot.sections:
            for row in rows:
                if row.label == "Beginning Cash":
                    # Milestone 13.3: Beginning Cash is a third, distinct
                    # formula shape not confined to the Q4 column at all
                    # -- covered by TestBeginningCashAdjacency below, not
                    # this Q4-specific cross-check.
                    continue
                for col, cell in row.cells.items():
                    if cell.formula is None:
                        continue
                    # Every formula cell is itself the Q4 column for
                    # `col.fiscal_year`, regardless of kind.
                    assert col.quarter == "Q4"

                    if row.kind == INSTANT:
                        checked_mirror = True
                        m = mirror_pattern.match(cell.formula)
                        assert m, f"Formula didn't match expected mirror shape: {cell.formula!r}"
                        fy_letter, fy_row = m.groups()

                        fy_idx = col_letter_to_index[fy_letter]
                        fy_ref_col = index_to_col[fy_idx]
                        assert fy_ref_col.fiscal_year == col.fiscal_year
                        assert fy_ref_col.is_fy
                        assert int(fy_row) == row.row_number
                        continue

                    checked_plug = True
                    m = plug_pattern.match(cell.formula)
                    assert m, f"Formula didn't match expected plug shape: {cell.formula!r}"
                    fy_letter, fy_row, q1_letter, q1_row, q3_letter, q3_row = m.groups()

                    fy_idx = col_letter_to_index[fy_letter]
                    q1_idx = col_letter_to_index[q1_letter]
                    q3_idx = col_letter_to_index[q3_letter]

                    fy_ref_col = index_to_col[fy_idx]
                    q1_ref_col = index_to_col[q1_idx]
                    q3_ref_col = index_to_col[q3_idx]

                    assert fy_ref_col.fiscal_year == col.fiscal_year
                    assert fy_ref_col.is_fy
                    assert q1_ref_col.fiscal_year == col.fiscal_year
                    assert q1_ref_col.quarter == "Q1"
                    assert q3_ref_col.fiscal_year == col.fiscal_year
                    assert q3_ref_col.quarter == "Q3"

                    # Row numbers must match this metric row's own row_number.
                    assert int(fy_row) == row.row_number
                    assert int(q1_row) == row.row_number
                    assert int(q3_row) == row.row_number

        assert checked_plug, "Expected at least one Q4 plug formula to be generated"
        assert checked_mirror, "Expected at least one Q4 mirror formula to be generated"


class TestOmittedSections:
    def test_no_revenue_build_or_amazon_only_extension_rows(self):
        """Milestone 13 adds Sales & Marketing/General & Administrative/
        Accounts Receivable/Inventory/D&A/Stock-Based Compensation as real
        rows (see facts_parser CONCEPT_TABLE + this module's
        INCOME_STATEMENT_ROWS/BALANCE_SHEET_ROWS/CASH_FLOW_ROWS) -- this
        test is narrowed from its original Milestone 12 scope-guard intent
        to what's still genuinely excluded: segment-level revenue-build
        rows (never planned) and the seven amzn:-namespace extension tags
        (explicitly excluded per PLAN.md's Milestone 13 resolved
        decisions, since they don't generalize beyond Amazon's own
        taxonomy)."""
        pivot = build_pivot(AMZN_FY2022_2026_RECORDS, num_years=5)
        all_labels = {row.label for _, rows in pivot.sections for row in rows}
        forbidden = {
            "North America Revenue",
            "International Revenue",
            "AWS Revenue",
        }
        assert all_labels.isdisjoint(forbidden)
        assert "Fulfillment" in all_labels
        assert "Technology & Infrastructure" in all_labels

    def test_no_net_working_capital_or_change_in_nwc_rows(self):
        """Milestone 13's Capability 3 (Net Working Capital / Change in
        NWC) is explicitly deferred -- confirm neither row exists."""
        pivot = build_pivot(AMZN_FY2022_2026_RECORDS, num_years=5)
        all_labels = {row.label for _, rows in pivot.sections for row in rows}
        assert "Net Working Capital" not in all_labels
        assert "Change in Net Working Capital(NWC)" not in all_labels
        assert "Total Current Assets" in all_labels
        assert "Total Current Liabilities" in all_labels

    def test_only_four_sections_present(self):
        pivot = build_pivot(AMZN_FY2022_2026_RECORDS, num_years=5)
        titles = [title for title, _ in pivot.sections]
        assert titles == ["Income Statement", "Balance Sheet", "Cash Flow", "Per-Share & Other"]
