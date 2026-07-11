"""Tests for csv_writer.py. Synthetic record sets only -- no live network
calls."""

from __future__ import annotations

import csv

from sec_financials.csv_writer import (
    CSV_HEADER,
    safe_filename_stem,
    write_company_csv,
)
from sec_financials.facts_parser import FilingRecord


def _make_record(period_end, form="10-K", **overrides):
    defaults = dict(
        company="Example Corp",
        ticker="EX",
        cik="0000000001",
        form=form,
        fiscal_year=2022,
        fiscal_period="FY",
        period_end=period_end,
        filed="2022-11-01",
        revenue=1000,
        cost_of_sales=400,
        gross_profit=600,
    )
    defaults.update(overrides)
    return FilingRecord(**defaults)


def test_write_company_csv_column_order_and_header(tmp_path):
    records = [_make_record("2022-09-24")]
    out_path = write_company_csv(records, tmp_path, "EX")

    with out_path.open("r", newline="", encoding="utf-8") as f:
        reader = csv.reader(f)
        rows = list(reader)

    assert rows[0] == CSV_HEADER
    # First 11 columns preserve their exact v1 name/order/content
    # unchanged (Milestone 8 requirement); remaining columns are blank
    # since this synthetic record only sets v1 fields.
    assert rows[1][:11] == [
        "Example Corp",
        "EX",
        "0000000001",
        "10-K",
        "2022",
        "FY",
        "2022-09-24",
        "2022-11-01",
        "1000",
        "400",
        "600",
    ]
    assert len(rows[1]) == len(CSV_HEADER)
    assert all(cell == "" for cell in rows[1][11:])


def test_write_company_csv_sorts_by_period_end_ascending(tmp_path):
    records = [
        _make_record("2022-09-24"),
        _make_record("2020-09-26"),
        _make_record("2021-09-25"),
    ]
    out_path = write_company_csv(records, tmp_path, "EX")

    with out_path.open("r", newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        period_ends = [row["Period End Date"] for row in reader]

    assert period_ends == ["2020-09-26", "2021-09-25", "2022-09-24"]


def test_write_company_csv_blank_cells_for_none_values(tmp_path):
    records = [
        _make_record(
            "2021-12-31", revenue=None, cost_of_sales=None, gross_profit=None
        )
    ]
    out_path = write_company_csv(records, tmp_path, "EX")

    with out_path.open("r", newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        row = next(reader)

    assert row["Revenue"] == ""
    assert row["Cost of Sales"] == ""
    assert row["Gross Profit"] == ""


def test_write_company_csv_filename_uses_ticker_or_cik(tmp_path):
    out_path = write_company_csv([_make_record("2022-01-01")], tmp_path, "AAPL")
    assert out_path.name == "AAPL.csv"
    assert out_path.parent == tmp_path


def test_write_company_csv_overwrites_existing_file(tmp_path):
    out_path = tmp_path / "EX.csv"
    out_path.write_text("stale content")

    write_company_csv([_make_record("2022-01-01")], tmp_path, "EX")

    content = out_path.read_text()
    assert "stale content" not in content
    assert "Example Corp" in content


def test_safe_filename_stem_sanitizes_unsafe_characters():
    assert safe_filename_stem("BRK.B") == "BRK.B"
    assert safe_filename_stem("AAPL") == "AAPL"
    assert safe_filename_stem("0000320193") == "0000320193"
    assert safe_filename_stem("weird/name?") == "weird_name_"


# -- Milestone 8: expanded schema (~22 new columns) --------------------------


def test_write_company_csv_extended_columns_populated(tmp_path):
    """A fully-populated record (every new Milestone 7/8 concept present)
    renders every new column correctly, in CSV_HEADER order."""
    record = _make_record(
        "2022-09-24",
        operating_income=119437,
        net_income=99803,
        research_and_development=26251,
        income_tax_expense=19300,
        pre_tax_income=119103,
        cash=23646,
        total_assets=352755,
        total_liabilities=302083,
        stockholders_equity=50672,
        long_term_debt=98959,
        long_term_debt_current=11128,
        long_term_debt_noncurrent=98959,
        short_term_debt=None,
        operating_lease_liabilities=10141,
        finance_lease_liabilities=None,
        marketable_securities_current=24658,
        operating_cash_flow=122151,
        capital_expenditures=10708,
        free_cash_flow=111443,
        shares_outstanding=15908118,
        eps_basic=6.15,
        eps_diluted=6.11,
        dei_common_stock_shares_outstanding=15908118,
        rsu_count=155208,
    )
    out_path = write_company_csv([record], tmp_path, "EX")

    with out_path.open("r", newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        row = next(reader)

    assert row["Operating Income"] == "119437"
    assert row["Net Income"] == "99803"
    assert row["Research and Development"] == "26251"
    assert row["Income Tax Expense"] == "19300"
    assert row["Pre-Tax Income"] == "119103"
    assert row["Cash"] == "23646"
    assert row["Total Assets"] == "352755"
    assert row["Total Liabilities"] == "302083"
    assert row["Stockholders Equity"] == "50672"
    assert row["Long Term Debt"] == "98959"
    assert row["Long Term Debt Current"] == "11128"
    assert row["Long Term Debt Noncurrent"] == "98959"
    assert row["Short Term Debt"] == ""
    assert row["Operating Lease Liabilities"] == "10141"
    assert row["Finance Lease Liabilities"] == ""
    assert row["Marketable Securities Current"] == "24658"
    assert row["Operating Cash Flow"] == "122151"
    assert row["Capital Expenditures"] == "10708"
    assert row["Free Cash Flow"] == "111443"
    assert row["Shares Outstanding"] == "15908118"
    assert row["EPS Basic"] == "6.15"
    assert row["EPS Diluted"] == "6.11"
    assert row["Common Stock Outstanding Shares (dei)"] == "15908118"
    assert row["RSU Count"] == "155208"


def test_write_company_csv_blank_cells_for_missing_new_columns(tmp_path):
    """A company lacking most new concepts (e.g. only Revenue/Cost of
    Sales/Gross Profit populated, matching a v1-style record) renders every
    new column blank, not an error. Covers ALL new columns (Milestone 8 +
    Milestone 13.4 combined), not just Milestone 8's slice."""
    record = _make_record("2021-12-31")
    out_path = write_company_csv([record], tmp_path, "EX")

    with out_path.open("r", newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        row = next(reader)

    new_columns = CSV_HEADER[11:]
    assert len(new_columns) == 72  # 24 (Milestone 8) + 48 (Milestone 13.4)
    for col in new_columns:
        assert row[col] == ""


def test_csv_header_preserves_v1_column_names_and_order():
    """Milestone 8/13.4 requirement: the 11 v1 columns keep their exact
    original names/order; new columns are appended after them."""
    assert CSV_HEADER[:11] == [
        "Company",
        "Ticker",
        "CIK",
        "Form",
        "Fiscal Year",
        "Fiscal Period",
        "Period End Date",
        "Filed Date",
        "Revenue",
        "Cost of Sales",
        "Gross Profit",
    ]
    assert len(CSV_HEADER) == 83  # 11 (v1) + 24 (Milestone 8) + 48 (Milestone 13.4)


def test_csv_header_preserves_milestone8_column_names_and_order():
    """Milestone 13.4 requirement: the 24 Milestone-8 columns (indices
    11:35) keep their exact original names/order; Milestone 13.4's new
    columns are appended after them, not interleaved."""
    assert CSV_HEADER[11:35] == [
        "Operating Income",
        "Net Income",
        "Research and Development",
        "Income Tax Expense",
        "Pre-Tax Income",
        "Cash",
        "Total Assets",
        "Total Liabilities",
        "Stockholders Equity",
        "Long Term Debt",
        "Long Term Debt Current",
        "Long Term Debt Noncurrent",
        "Short Term Debt",
        "Operating Lease Liabilities",
        "Finance Lease Liabilities",
        "Marketable Securities Current",
        "Operating Cash Flow",
        "Capital Expenditures",
        "Free Cash Flow",
        "Shares Outstanding",
        "EPS Basic",
        "EPS Diluted",
        "Common Stock Outstanding Shares (dei)",
        "RSU Count",
    ]
