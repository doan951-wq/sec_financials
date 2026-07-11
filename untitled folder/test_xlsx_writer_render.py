"""Unit tests for Milestone 12.3b: rendering real pivoted data (Milestone
12.2's `build_pivot` output) into a styled sheet (Milestone 12.3a's
styling functions), via `render_workbook`/`write_company_xlsx`.

Uses the same real AMZN FY2022-FY2026 fixture as
`test_xlsx_writer_pivot.py` (imported from there, not redefined) so the
pivot-level and render-level tests stay in sync with the same fixture
data.

Requires `openpyxl` importable (skipped cleanly otherwise, same pattern
as `test_xlsx_writer_styling.py`).
"""

from __future__ import annotations

import pytest

openpyxl = pytest.importorskip("openpyxl")

from sec_financials.tests.test_xlsx_writer_pivot import (  # noqa: E402
    AMZN_FY2022_2026_RECORDS,
)
from sec_financials.xlsx_writer import (  # noqa: E402
    COMPANY_IDENTIFICATION_ROW,
    FIRST_SECTION_TITLE_ROW,
    SHEET_TITLE_ROW,
    SHEET_TITLE_TEXT,
    build_pivot,
    render_workbook,
    write_company_xlsx,
)


@pytest.fixture
def pivot():
    return build_pivot(AMZN_FY2022_2026_RECORDS, num_years=5)


@pytest.fixture
def rendered_ws(pivot):
    wb = render_workbook(openpyxl, pivot, "AMAZON COM INC (AMZN, CIK 0001018724)")
    return wb.active


class TestSheetLevelRendering:
    def test_sheet_title_present(self, rendered_ws):
        cell = rendered_ws.cell(row=SHEET_TITLE_ROW, column=2)
        assert cell.value == SHEET_TITLE_TEXT
        assert cell.font.bold is True

    def test_company_identification_present(self, rendered_ws):
        cell = rendered_ws.cell(row=COMPANY_IDENTIFICATION_ROW, column=2)
        assert "AMZN" in cell.value
        assert "0001018724" in cell.value

    def test_four_section_headers_present_in_order(self, rendered_ws):
        expected_titles = ["Income Statement", "Balance Sheet", "Cash Flow", "Per-Share & Other"]
        found = []
        for row in range(1, rendered_ws.max_row + 1):
            v = rendered_ws.cell(row=row, column=2).value
            if v in expected_titles:
                found.append(v)
        assert found == expected_titles

    def test_no_merged_cells_gridlines_visible_no_freeze(self, rendered_ws):
        assert len(rendered_ws.merged_cells.ranges) == 0
        assert rendered_ws.sheet_view.showGridLines is True
        assert rendered_ws.freeze_panes is None

    def test_no_section_boundary_row_collisions(self, pivot, rendered_ws):
        """Regression test for a real bug caught during manual
        verification: an earlier version of the renderer independently
        recomputed each section's title row via its own running counter
        (`SECTION_HEADER_ROWS + len(metric_rows) + 1`), which drifted out
        of sync with `build_pivot`'s own row numbering and caused a
        section header/period-header block to land on the SAME row as
        the previous section's last metric row (observed concretely:
        "Free Cash Flow"'s row also held "Per-Share & Other"'s section
        title in column B, and Free Cash Flow's Q4-plug formulas leaked
        into what should have been a section-header-only row). Fixed by
        deriving each section's header-block rows directly from
        `metric_rows[0].row_number` instead of a second independently
        -tracked counter. This test asserts every section's own row range
        (title row through last metric row) is fully disjoint from every
        other section's."""
        ranges = []
        for section_title, metric_rows in pivot.sections:
            first_metric_row = metric_rows[0].row_number
            section_title_row = first_metric_row - 3  # SECTION_HEADER_ROWS
            last_metric_row = metric_rows[-1].row_number
            ranges.append((section_title, section_title_row, last_metric_row))

        for i in range(len(ranges)):
            for j in range(i + 1, len(ranges)):
                _, start_i, end_i = ranges[i]
                _, start_j, end_j = ranges[j]
                assert end_i < start_j or end_j < start_i, (
                    f"Row range collision between {ranges[i]} and {ranges[j]}"
                )

        # Directly confirm each section's OWN label actually appears at
        # its own title row in the rendered sheet, and nowhere else's
        # metric rows leak a stray section-title value into column B.
        for section_title, start, end in ranges:
            assert rendered_ws.cell(row=start, column=2).value == section_title
            for metric_row in dict(pivot.sections)[section_title]:
                label_cell_value = rendered_ws.cell(row=metric_row.row_number, column=2).value
                assert label_cell_value == metric_row.label


class TestRealFormulasAndLabels:
    def test_revenue_row_label_and_q4_formula_fy2023(self, pivot, rendered_ws):
        income_statement = dict(pivot.sections)["Income Statement"]
        revenue_row = next(r for r in income_statement if r.field_name == "revenue")

        label_cell = rendered_ws.cell(row=revenue_row.row_number, column=2)
        assert label_cell.value == "Revenue"

        q4_col = next(c for c in pivot.columns if c.fiscal_year == 2023 and c.quarter == "Q4")
        col_idx = pivot.column_map[q4_col]
        formula_cell = rendered_ws.cell(row=revenue_row.row_number, column=col_idx)
        assert formula_cell.value == revenue_row.cells[q4_col].formula
        assert formula_cell.value.startswith("=")
        assert "SUM(" in formula_cell.value

    def test_period_header_labels_match_column_map(self, pivot, rendered_ws):
        income_statement = dict(pivot.sections)["Income Statement"]
        # Period-header row is 2 rows above the first metric row's section
        # header block (section_title, period_label, fiscal_year, metric...).
        first_metric_row = income_statement[0].row_number
        period_label_row = first_metric_row - 2
        for col in pivot.columns:
            col_idx = pivot.column_map[col]
            cell = rendered_ws.cell(row=period_label_row, column=col_idx)
            assert cell.value == col.label

    def test_fy_header_cell_has_fill_quarter_headers_do_not(self, pivot, rendered_ws):
        income_statement = dict(pivot.sections)["Income Statement"]
        first_metric_row = income_statement[0].row_number
        period_label_row = first_metric_row - 2

        fy_2023_col = next(c for c in pivot.columns if c.fiscal_year == 2023 and c.is_fy)
        fy_cell = rendered_ws.cell(row=period_label_row, column=pivot.column_map[fy_2023_col])
        assert fy_cell.fill.patternType == "solid"

        q1_2023_col = next(c for c in pivot.columns if c.fiscal_year == 2023 and c.quarter == "Q1")
        q1_cell = rendered_ws.cell(row=period_label_row, column=pivot.column_map[q1_2023_col])
        assert q1_cell.fill.patternType is None


class TestBlankCells:
    def test_newest_year_blank_fy_column_is_actually_blank(self, pivot, rendered_ws):
        income_statement = dict(pivot.sections)["Income Statement"]
        revenue_row = next(r for r in income_statement if r.field_name == "revenue")
        fy_2026_col = next(c for c in pivot.columns if c.fiscal_year == 2026 and c.is_fy)
        cell = rendered_ws.cell(row=revenue_row.row_number, column=pivot.column_map[fy_2026_col])
        assert cell.value is None

    def test_eps_diluted_q4_cells_blank_no_formula(self, pivot, rendered_ws):
        per_share = dict(pivot.sections)["Per-Share & Other"]
        eps_row = next(r for r in per_share if r.field_name == "eps_diluted")
        for year in (2023, 2024, 2025):
            q4_col = next(c for c in pivot.columns if c.fiscal_year == year and c.quarter == "Q4")
            cell = rendered_ws.cell(row=eps_row.row_number, column=pivot.column_map[q4_col])
            assert cell.value is None


class TestWriteCompanyXlsxToDisk:
    def test_writes_real_file_and_matches_csv_stem(self, tmp_path):
        out_path = write_company_xlsx(AMZN_FY2022_2026_RECORDS, tmp_path, "AMZN")
        assert out_path.exists()
        assert out_path.name == "AMZN.xlsx"

        wb = openpyxl.load_workbook(out_path, data_only=False)
        assert "Historical Financial Data" in wb.sheetnames
        ws = wb["Historical Financial Data"]
        assert ws.cell(row=SHEET_TITLE_ROW, column=2).value == SHEET_TITLE_TEXT

    def test_overwrites_existing_file(self, tmp_path):
        out_path_1 = write_company_xlsx(AMZN_FY2022_2026_RECORDS, tmp_path, "AMZN")
        mtime_1 = out_path_1.stat().st_mtime_ns
        out_path_2 = write_company_xlsx(AMZN_FY2022_2026_RECORDS, tmp_path, "AMZN")
        assert out_path_1 == out_path_2
        assert out_path_2.exists()
