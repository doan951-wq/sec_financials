"""Tests for Milestone 13.6: GUI tab extension for the ~46 new
Milestone 13.1/13.2 concepts (`gui.py`).

NOT run via the isolated pytest venv (`.venv_sec_financials_test`), which
does not have `customtkinter` installed. Run directly with system
`python3` (see test_gui_manual.py's module docstring for the same
environment-split rationale):

    python3 -m sec_financials.tests.test_gui_milestone13
"""

from __future__ import annotations

import csv
import sys
from pathlib import Path

try:
    from sec_financials import csv_writer, gui
except ImportError as _import_error:
    try:
        import pytest as _pytest

        _pytest.skip(f"sec_financials.gui unavailable: {_import_error}", allow_module_level=True)
    except ImportError:
        raise

FIXTURES_DIR = Path(__file__).parent / "fixtures"


def _write_csv(tmp_path: Path, header: list[str], rows: list[list[str]]) -> Path:
    path = tmp_path / "test.csv"
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(header)
        writer.writerows(rows)
    return path


# -- scaffolding: column names match csv_writer.CSV_HEADER exactly ----------


def test_income_statement_columns_match_csv_header_exactly():
    """Every Milestone 13.1 Income-Statement metric column name in
    gui.INCOME_STATEMENT_COLUMNS (excluding the GUI-only computed "Gross
    Margin %") must be a real csv_writer.CSV_HEADER string -- same wiring
    convention as every prior GUI extension (Milestone 9.2)."""
    for col in gui.INCOME_STATEMENT_COLUMNS:
        if col == "Gross Margin %":
            continue
        assert col in csv_writer.CSV_HEADER, f"{col!r} not in CSV_HEADER"


def test_balance_sheet_columns_match_csv_header_exactly():
    for col in gui.BALANCE_SHEET_COLUMNS:
        assert col in csv_writer.CSV_HEADER, f"{col!r} not in CSV_HEADER"


def test_cash_flow_columns_match_csv_header_exactly():
    for col in gui.CASH_FLOW_COLUMNS:
        assert col in csv_writer.CSV_HEADER, f"{col!r} not in CSV_HEADER"


def test_cash_flow_columns_include_ending_cash_but_not_beginning_cash():
    """Ending Cash is a real CSV/FilingRecord field and belongs in the
    GUI; Beginning Cash has no CSV column/FilingRecord field at all
    (Milestone 13.3, Capability 2) and must NOT appear here."""
    assert "Ending Cash" in gui.CASH_FLOW_COLUMNS
    assert "Beginning Cash" not in gui.CASH_FLOW_COLUMNS


def test_new_columns_have_column_widths():
    """Every new Milestone 13.1/13.2 column should have an explicit
    COLUMN_WIDTHS entry (not required for correctness -- DEFAULT_COLUMN_WIDTH
    covers any gap -- but this is the established convention for every
    prior GUI extension)."""
    all_metric_columns = (
        gui.INCOME_STATEMENT_COLUMNS
        + gui.BALANCE_SHEET_COLUMNS
        + gui.CASH_FLOW_COLUMNS
        + gui.PER_SHARE_AND_OTHER_COLUMNS
    )
    missing = [c for c in all_metric_columns if c != "Gross Margin %" and c not in gui.COLUMN_WIDTHS]
    assert not missing, f"Missing COLUMN_WIDTHS entries: {missing}"


# -- wiring/verification: real csv_writer.CSV_HEADER-shaped synthetic rows --


def _synthetic_full_row(csv_columns: list[str], csv_header: list[str]) -> dict[str, str]:
    """Build a synthetic row dict keyed by CSV_HEADER label, with a
    distinct, recognizable value per column (the column's own index as a
    string) so each cell's presence/position can be asserted precisely."""
    return {header: str(i) for i, header in enumerate(csv_header)}


def test_build_tab_rows_all_milestone13_columns_populated(tmp_path):
    """A CSV shaped exactly like the real, current csv_writer.CSV_HEADER
    (81+ columns, all populated) renders every Milestone 13.1/13.2 column
    correctly on its respective tab, matching the real column by name."""
    row_dict = _synthetic_full_row(csv_writer.CSV_COLUMNS, csv_writer.CSV_HEADER)
    path = _write_csv(tmp_path, csv_writer.CSV_HEADER, [list(row_dict.values())])
    rows = gui.load_csv_rows(str(path))
    assert len(rows) == 1

    for tab_name, metric_columns in gui.TABS:
        tab_rows = gui.build_tab_rows(rows, metric_columns)
        assert len(tab_rows) == 1
        rendered = tab_rows[0][len(gui.IDENTITY_COLUMNS):]
        for col, cell in zip(metric_columns, rendered):
            if col == "Gross Margin %":
                continue  # computed, not a passthrough CSV value
            assert cell == row_dict[col], f"tab={tab_name!r} col={col!r}"


def test_build_tab_rows_milestone13_columns_blank_for_older_csv(tmp_path):
    """A CSV predating Milestone 13 (only the original 35 Milestone-8
    columns) still loads and renders -- every Milestone 13.1/13.2 column
    is blank (missing column), not an error, on every affected tab."""
    header = [
        "Company", "Ticker", "CIK", "Form", "Fiscal Year", "Fiscal Period",
        "Period End Date", "Filed Date", "Revenue", "Cost of Sales", "Gross Profit",
        "Operating Income", "Net Income", "Research and Development",
        "Income Tax Expense", "Pre-Tax Income", "Cash", "Total Assets",
        "Total Liabilities", "Stockholders Equity", "Long Term Debt",
        "Long Term Debt Current", "Long Term Debt Noncurrent", "Short Term Debt",
        "Operating Lease Liabilities", "Finance Lease Liabilities",
        "Marketable Securities Current", "Operating Cash Flow",
        "Capital Expenditures", "Free Cash Flow", "Shares Outstanding",
        "EPS Basic", "EPS Diluted", "Common Stock Outstanding Shares (dei)",
        "RSU Count",
    ]
    row = [
        "Example Corp", "EX", "0000000001", "10-K", "2022", "FY",
        "2022-09-24", "2022-11-01", "1000", "400", "600",
        "300", "250", "80", "60", "310", "150", "2000", "1200", "800",
        "500", "50", "450", "", "40", "", "120",
        "700", "100", "600",
        "1000", "1.5", "1.48", "1010", "",
    ]
    path = _write_csv(tmp_path, header, [row])
    rows = gui.load_csv_rows(str(path))

    milestone_13_income_cols = [c for c in gui.INCOME_STATEMENT_COLUMNS if c not in header and c != "Gross Margin %"]
    milestone_13_balance_cols = [c for c in gui.BALANCE_SHEET_COLUMNS if c not in header]
    milestone_13_cash_flow_cols = [c for c in gui.CASH_FLOW_COLUMNS if c not in header]
    assert milestone_13_income_cols and milestone_13_balance_cols and milestone_13_cash_flow_cols

    income = gui.build_tab_rows(rows, gui.INCOME_STATEMENT_COLUMNS)[0]
    for col in milestone_13_income_cols:
        idx = len(gui.IDENTITY_COLUMNS) + gui.INCOME_STATEMENT_COLUMNS.index(col)
        assert income[idx] == ""

    balance = gui.build_tab_rows(rows, gui.BALANCE_SHEET_COLUMNS)[0]
    for col in milestone_13_balance_cols:
        idx = len(gui.IDENTITY_COLUMNS) + gui.BALANCE_SHEET_COLUMNS.index(col)
        assert balance[idx] == ""

    cash_flow = gui.build_tab_rows(rows, gui.CASH_FLOW_COLUMNS)[0]
    for col in milestone_13_cash_flow_cols:
        idx = len(gui.IDENTITY_COLUMNS) + gui.CASH_FLOW_COLUMNS.index(col)
        assert cash_flow[idx] == ""


def _run_all():
    import tempfile
    import traceback

    test_fns = [
        (name, obj)
        for name, obj in sorted(globals().items())
        if name.startswith("test_") and callable(obj)
    ]
    passed = 0
    failed = 0
    for name, fn in test_fns:
        try:
            with tempfile.TemporaryDirectory() as tmp:
                if "tmp_path" in fn.__code__.co_varnames[: fn.__code__.co_argcount]:
                    fn(Path(tmp))
                else:
                    fn()
            print(f"PASS: {name}")
            passed += 1
        except Exception:
            print(f"FAIL: {name}")
            traceback.print_exc()
            failed += 1
    print(f"\n{passed} passed, {failed} failed")
    return failed == 0


if __name__ == "__main__":
    ok = _run_all()
    sys.exit(0 if ok else 1)
