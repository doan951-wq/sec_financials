"""Tests for gui.py (Milestones 9.1/9.2: tab scaffolding + wiring to real
CSV columns).

NOT run via the isolated pytest venv (`.venv_sec_financials_test`), which
does not have `customtkinter` installed. Run directly with system
`python3`, which does have `customtkinter`/`bs4` (see EXECUTION_LOG.md's
GUI section for the same environment split used previously). This module
is still written as plain `assert`-based functions collectible by pytest
(`def test_*`) so it's not dead code and can run under pytest in an
environment that has both `pytest` and `customtkinter` installed --
invoke directly for now:

    python3 -m sec_financials.tests.test_gui_manual
"""

from __future__ import annotations

import csv
import sys
from pathlib import Path

try:
    from sec_financials import gui
except ImportError as _import_error:
    # customtkinter is not installed in .venv_sec_financials_test (only
    # system python3 has it -- see module docstring). Skip cleanly under
    # pytest rather than erroring out the whole collection run; the
    # __main__ direct-invocation path below (system python3) never hits
    # this branch.
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


# -- tab structure (9.1) ------------------------------------------------------


def test_tabs_cover_expected_groups_and_metrics():
    """Milestone 13.6 extends each tab's metric-column list with the new
    Milestone 13.1/13.2 columns; the tab titles and shared identity
    columns are unchanged from Milestone 9.1/9.2. Exact per-tab content
    (including the new columns) is asserted in
    test_gui_milestone13.py -- this test only pins the still-stable
    tab-title/grouping structure."""
    tab_names = [name for name, _ in gui.TABS]
    assert tab_names == ["Income Statement", "Balance Sheet", "Cash Flow", "Per-Share & Other"]

    by_name = dict(gui.TABS)
    # v1/Milestone 8 columns still present, in their original relative
    # order, within each tab's (now longer) column list.
    assert by_name["Income Statement"][:4] == [
        "Revenue", "Cost of Sales", "Gross Profit", "Gross Margin %",
    ]
    assert "Operating Income" in by_name["Income Statement"]
    assert "Net Income" in by_name["Income Statement"]
    assert by_name["Balance Sheet"][0] == "Cash"
    assert "Total Assets" in by_name["Balance Sheet"]
    assert by_name["Cash Flow"][0] == "Operating Cash Flow"
    assert "Free Cash Flow" in by_name["Cash Flow"]
    assert by_name["Per-Share & Other"] == [
        "Shares Outstanding", "EPS Basic", "EPS Diluted",
        "Common Stock Outstanding Shares (dei)", "RSU Count",
    ]


def test_identity_columns_shared_across_all_tabs():
    assert gui.IDENTITY_COLUMNS == [
        "Form", "Fiscal Year", "Fiscal Period", "Period End Date", "Filed Date",
    ]


# -- wiring to real CSV columns (9.2) -----------------------------------------


_FULL_HEADER = [
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


def test_build_tab_rows_all_tabs_populated(tmp_path):
    """`_FULL_HEADER` is the pre-Milestone-13 (Milestone 8) 35-column CSV
    shape -- none of Milestone 13.1/13.2's new columns are present at
    all. build_tab_rows must still render the identity + v1/Milestone-8
    metric cells correctly, with every new Milestone 13 column blank
    (missing-column case), not an error -- exercising the exact
    "older/narrower CSV" design Milestone 13.6 explicitly preserves."""
    row = [
        "Example Corp", "EX", "0000000001", "10-K", "2022", "FY",
        "2022-09-24", "2022-11-01", "1000", "400", "600",
        "300", "250", "80", "60", "310", "150", "2000", "1200", "800",
        "500", "50", "450", "", "40", "", "120",
        "700", "100", "600",
        "1000", "1.5", "1.48", "1010", "",
    ]
    path = _write_csv(tmp_path, _FULL_HEADER, [row])
    rows = gui.load_csv_rows(str(path))
    assert len(rows) == 1
    csv_row = rows[0]

    def _expected(metric_columns):
        identity = [csv_row.get(c, "") for c in gui.IDENTITY_COLUMNS]
        metrics = []
        for col in metric_columns:
            if col == "Gross Margin %":
                metrics.append("60.00%")
            else:
                metrics.append(csv_row.get(col, ""))
        return identity + metrics

    income = gui.build_tab_rows(rows, gui.INCOME_STATEMENT_COLUMNS)
    assert income == [_expected(gui.INCOME_STATEMENT_COLUMNS)]
    # Spot-check specific v1 cells are populated and new Milestone 13
    # cells are blank (missing column).
    assert income[0][gui.IDENTITY_COLUMNS.__len__() + gui.INCOME_STATEMENT_COLUMNS.index("Operating Income")] == "300"
    assert income[0][gui.IDENTITY_COLUMNS.__len__() + gui.INCOME_STATEMENT_COLUMNS.index("Diluted Shares")] == ""

    balance = gui.build_tab_rows(rows, gui.BALANCE_SHEET_COLUMNS)
    assert balance == [_expected(gui.BALANCE_SHEET_COLUMNS)]
    assert balance[0][gui.IDENTITY_COLUMNS.__len__() + gui.BALANCE_SHEET_COLUMNS.index("Cash")] == "150"
    assert balance[0][gui.IDENTITY_COLUMNS.__len__() + gui.BALANCE_SHEET_COLUMNS.index("Goodwill")] == ""

    cash_flow = gui.build_tab_rows(rows, gui.CASH_FLOW_COLUMNS)
    assert cash_flow == [_expected(gui.CASH_FLOW_COLUMNS)]
    assert cash_flow[0][gui.IDENTITY_COLUMNS.__len__() + gui.CASH_FLOW_COLUMNS.index("Operating Cash Flow")] == "700"
    assert cash_flow[0][gui.IDENTITY_COLUMNS.__len__() + gui.CASH_FLOW_COLUMNS.index("Ending Cash")] == ""

    per_share = gui.build_tab_rows(rows, gui.PER_SHARE_AND_OTHER_COLUMNS)
    assert per_share == [_expected(gui.PER_SHARE_AND_OTHER_COLUMNS)]


def test_build_tab_rows_blank_cells_per_tab(tmp_path):
    """A row with only the v1 columns populated (new columns blank/missing)
    renders every new-metric cell blank on every tab, not an error."""
    minimal_header = [
        "Company", "Ticker", "CIK", "Form", "Fiscal Year", "Fiscal Period",
        "Period End Date", "Filed Date", "Revenue", "Cost of Sales", "Gross Profit",
    ]
    row = ["Example Corp", "EX", "0000000001", "10-K", "2022", "FY",
           "2022-09-24", "2022-11-01", "1000", "400", "600"]
    path = _write_csv(tmp_path, minimal_header, [row])
    rows = gui.load_csv_rows(str(path))

    balance = gui.build_tab_rows(rows, gui.BALANCE_SHEET_COLUMNS)
    assert balance == [
        ["10-K", "2022", "FY", "2022-09-24", "2022-11-01"] + [""] * len(gui.BALANCE_SHEET_COLUMNS)
    ]

    cash_flow = gui.build_tab_rows(rows, gui.CASH_FLOW_COLUMNS)
    assert cash_flow == [
        ["10-K", "2022", "FY", "2022-09-24", "2022-11-01"] + [""] * len(gui.CASH_FLOW_COLUMNS)
    ]

    per_share = gui.build_tab_rows(rows, gui.PER_SHARE_AND_OTHER_COLUMNS)
    assert per_share == [[
        "10-K", "2022", "FY", "2022-09-24", "2022-11-01", "", "", "", "", "",
    ]]


def test_load_csv_rows_still_requires_v1_columns(tmp_path):
    bad_header = ["Company", "Ticker"]  # missing everything else required
    path = _write_csv(tmp_path, bad_header, [["A", "B"]])
    try:
        gui.load_csv_rows(str(path))
        raised = False
    except gui.CsvFormatError:
        raised = True
    assert raised


def test_company_key_and_grouping_unchanged():
    rows = [
        {"Company": "Apple Inc.", "Ticker": "AAPL"},
        {"Company": "Apple Inc.", "Ticker": "AAPL"},
        {"Company": "Alphabet Inc.", "Ticker": "GOOGL"},
    ]
    grouped = gui.group_rows_by_company(rows)
    assert list(grouped.keys()) == ["Apple Inc. (AAPL)", "Alphabet Inc. (GOOGL)"]
    assert len(grouped["Apple Inc. (AAPL)"]) == 2


def _run_all():
    """Run every test_* function in this module and report pass/fail,
    since pytest is not available in this environment's system Python."""
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
