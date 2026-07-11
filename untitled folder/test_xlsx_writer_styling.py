"""Unit tests for Milestone 12.3a: styling scaffolding in
`xlsx_writer.py` (fonts, fills, number formats, column widths, sheet
-level settings). Exercised against a synthetic/fixture sheet shape --
no dependency on Milestone 12.2's real pivot output.

Requires `openpyxl` importable to run at all (skipped cleanly if not,
same self-skip pattern `test_gui_manual.py` already uses for
`customtkinter`) -- run via `.venv_sec_financials_test` (has `openpyxl`
added per Milestone 12.1) or any environment with it installed.
"""

from __future__ import annotations

import pytest

openpyxl = pytest.importorskip("openpyxl")

from sec_financials.xlsx_writer import (  # noqa: E402
    COLUMN_WIDTH_DATA,
    COLUMN_WIDTH_LABEL,
    COLUMN_WIDTH_SPACER,
    DOLLAR_NUMBER_FORMAT,
    EPS_NUMBER_FORMAT,
    FIRST_DATA_COLUMN_INDEX,
    FONT_NAME,
    FY_HEADER_FILL_RGB,
    LABEL_COLUMN_INDEX,
    SHARE_COUNT_NUMBER_FORMAT,
    SHEET_TITLE_TEXT,
    CellPlan,
    PeriodColumn,
    apply_sheet_level_settings,
    number_format_for_field,
    set_data_column_widths,
    style_company_identification,
    style_data_cell,
    style_fiscal_year_row,
    style_metric_row_label,
    style_period_header_row,
    style_section_header,
    style_sheet_title,
)


@pytest.fixture
def ws():
    wb = openpyxl.Workbook()
    return wb.active


# Synthetic/placeholder period-column shape (not 12.2's real pivot
# output) -- exactly what 12.3a's tests are meant to exercise
# independently.
PLACEHOLDER_COLUMNS = [
    PeriodColumn(2023, None),
    PeriodColumn(2024, "Q1"),
    PeriodColumn(2024, "Q2"),
    PeriodColumn(2024, "Q3"),
    PeriodColumn(2024, "Q4"),
    PeriodColumn(2024, None),
]
PLACEHOLDER_COLUMN_MAP = {
    col: FIRST_DATA_COLUMN_INDEX + i for i, col in enumerate(PLACEHOLDER_COLUMNS)
}


class TestFontsAndSizes:
    def test_sheet_title_font(self, ws):
        style_sheet_title(openpyxl, ws, row=1, col=2)
        cell = ws.cell(row=1, column=2)
        assert cell.value == SHEET_TITLE_TEXT
        assert cell.font.name == FONT_NAME
        assert cell.font.size == 14
        assert cell.font.bold is True

    def test_section_header_font(self, ws):
        style_section_header(openpyxl, ws, row=5, title="Income Statement")
        cell = ws.cell(row=5, column=LABEL_COLUMN_INDEX)
        assert cell.value == "Income Statement"
        assert cell.font.name == FONT_NAME
        assert cell.font.size == 12
        assert cell.font.bold is True

    def test_metric_label_font_not_bold(self, ws):
        style_metric_row_label(ws=ws, openpyxl=openpyxl, row=6, label="Revenue")
        cell = ws.cell(row=6, column=LABEL_COLUMN_INDEX)
        assert cell.value == "Revenue"
        assert cell.font.name == FONT_NAME
        assert cell.font.size == 12
        assert cell.font.bold is False

    def test_period_header_font_bold_10pt_centered(self, ws):
        style_period_header_row(openpyxl, ws, label_row=3, columns=PLACEHOLDER_COLUMNS, column_map=PLACEHOLDER_COLUMN_MAP)
        q1_col = PLACEHOLDER_COLUMN_MAP[PeriodColumn(2024, "Q1")]
        cell = ws.cell(row=3, column=q1_col)
        assert cell.value == "Q1"
        assert cell.font.size == 10
        assert cell.font.bold is True
        assert cell.alignment.horizontal == "center"

    def test_company_identification_font_bold(self, ws):
        style_company_identification(openpyxl, ws, row=2, text="AMAZON COM INC (AMZN, CIK 0001018724)")
        cell = ws.cell(row=2, column=LABEL_COLUMN_INDEX)
        assert "AMZN" in cell.value
        assert cell.font.bold is True


class TestFyHeaderFill:
    def test_fy_header_cell_gets_fill_quarter_headers_do_not(self, ws):
        style_period_header_row(openpyxl, ws, label_row=3, columns=PLACEHOLDER_COLUMNS, column_map=PLACEHOLDER_COLUMN_MAP)

        fy_2024_col = PLACEHOLDER_COLUMN_MAP[PeriodColumn(2024, None)]
        fy_cell = ws.cell(row=3, column=fy_2024_col)
        assert fy_cell.fill.patternType == "solid"
        assert fy_cell.fill.fgColor.rgb == FY_HEADER_FILL_RGB

        q1_col = PLACEHOLDER_COLUMN_MAP[PeriodColumn(2024, "Q1")]
        q1_cell = ws.cell(row=3, column=q1_col)
        assert q1_cell.fill.patternType is None

    def test_data_cells_never_get_fill_even_in_fy_column(self, ws):
        fy_col_idx = PLACEHOLDER_COLUMN_MAP[PeriodColumn(2024, None)]
        style_data_cell(openpyxl, ws, row=10, col_idx=fy_col_idx, field_name="revenue", cell_plan=CellPlan(value=100))
        cell = ws.cell(row=10, column=fy_col_idx)
        assert cell.fill.patternType is None


class TestNumberFormats:
    def test_dollar_field_gets_dollar_format(self):
        assert number_format_for_field("revenue") == DOLLAR_NUMBER_FORMAT

    def test_eps_field_gets_eps_format(self):
        assert number_format_for_field("eps_basic") == EPS_NUMBER_FORMAT
        assert number_format_for_field("eps_diluted") == EPS_NUMBER_FORMAT

    def test_share_count_field_gets_share_count_format(self):
        assert number_format_for_field("shares_outstanding") == SHARE_COUNT_NUMBER_FORMAT
        assert number_format_for_field("rsu_count") == SHARE_COUNT_NUMBER_FORMAT

    def test_data_cell_carries_correct_number_format(self, ws):
        style_data_cell(openpyxl, ws, row=10, col_idx=3, field_name="eps_diluted", cell_plan=CellPlan(value=1.5))
        cell = ws.cell(row=10, column=3)
        assert cell.number_format == EPS_NUMBER_FORMAT


class TestDataCellNoColorCoding:
    def test_formula_and_value_cells_use_same_plain_black_font(self, ws):
        style_data_cell(openpyxl, ws, row=10, col_idx=3, field_name="revenue", cell_plan=CellPlan(value=100))
        style_data_cell(openpyxl, ws, row=11, col_idx=3, field_name="revenue", cell_plan=CellPlan(formula="=C3-SUM(C4:C6)"))
        value_cell = ws.cell(row=10, column=3)
        formula_cell = ws.cell(row=11, column=3)
        assert value_cell.font.color.rgb == "FF000000"
        assert formula_cell.font.color.rgb == "FF000000"
        assert value_cell.font.name == formula_cell.font.name == FONT_NAME

    def test_blank_cell_written_when_cell_plan_is_blank(self, ws):
        style_data_cell(openpyxl, ws, row=10, col_idx=3, field_name="revenue", cell_plan=CellPlan())
        cell = ws.cell(row=10, column=3)
        assert cell.value is None


class TestColumnWidths:
    def test_spacer_and_label_column_widths(self, ws):
        apply_sheet_level_settings(ws)
        assert ws.column_dimensions["A"].width == COLUMN_WIDTH_SPACER
        assert ws.column_dimensions["B"].width == COLUMN_WIDTH_LABEL

    def test_data_column_widths_consistent_across_columns(self, ws):
        set_data_column_widths(ws, num_data_columns=6)
        for i in range(6):
            from sec_financials.xlsx_writer import column_letter

            letter = column_letter(FIRST_DATA_COLUMN_INDEX + i)
            assert ws.column_dimensions[letter].width == COLUMN_WIDTH_DATA


class TestSheetLevelSettings:
    def test_no_merged_cells_gridlines_visible_no_freeze_panes(self, ws):
        # Sanity: nothing in this module ever calls ws.merge_cells.
        apply_sheet_level_settings(ws)
        assert len(ws.merged_cells.ranges) == 0
        assert ws.sheet_view.showGridLines is True
        assert ws.freeze_panes is None
