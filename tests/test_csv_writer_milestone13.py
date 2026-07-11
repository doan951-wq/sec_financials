"""Tests for Milestone 13.4: CSV output extension for the ~46 new
Milestone 13.1/13.2 concepts. Synthetic record sets only -- no live
network calls."""

from __future__ import annotations

import csv

from sec_financials.csv_writer import CSV_COLUMNS, CSV_HEADER, write_company_csv
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


def test_csv_columns_and_header_are_same_length_and_no_beginning_cash():
    assert len(CSV_COLUMNS) == len(CSV_HEADER)
    assert "beginning_cash" not in CSV_COLUMNS
    assert "Beginning Cash" not in CSV_HEADER


def test_milestone13_columns_populated_correctly(tmp_path):
    """A fully-populated record (every new Milestone 13.1/13.2 concept
    present) renders every new column correctly, in CSV_HEADER order."""
    record = _make_record(
        "2022-09-24",
        operating_expenses=274891,
        sales_and_marketing=25094,
        general_and_administrative=6210,
        other_operating_expense_income=-1000,
        interest_income=2825,
        interest_expense=2931,
        other_income_expense_net=-382,
        diluted_shares=16325819,
        accounts_receivable=60932,
        inventory=6331,
        other_current_assets=14695,
        property_and_equipment_net=42117,
        operating_lease_rou_asset=10417,
        goodwill=18884,
        other_assets_noncurrent=18040,
        accounts_payable=64115,
        accrued_expenses_and_other=76318,
        unearned_revenue=8249,
        current_finance_lease_liabilities=1632,
        current_operating_lease_liabilities=1876,
        long_term_finance_lease_liabilities=859,
        long_term_operating_lease_liabilities=8235,
        other_long_term_liabilities=8249,
        total_liabilities_and_stockholders_equity=352755,
        ending_cash=24000,
        depreciation_and_amortization=11104,
        stock_based_compensation=9038,
        deferred_taxes=-4774,
        other_non_cash_items=-2266,
        unearned_revenue_cf_change=5632,
        inventory_cf_change=-1046,
        accounts_receivable_cf_change=-9343,
        other_assets_cf_change=-6499,
        accounts_payable_cf_change=9448,
        accrued_expenses_cf_change=3852,
        acquisitions=-306,
        net_cash_used_in_investing_activities=-22354,
        purchases_of_marketable_securities=-76923,
        sales_maturities_of_marketable_securities=90000,
        proceeds_from_short_term_debt_and_other=0,
        repayments_of_short_term_debt_and_other=0,
        proceeds_from_long_term_debt=5465,
        repayments_of_long_term_debt=-9543,
        finance_lease_principal_payments=-435,
        financing_obligation_principal_payments=0,
        net_cash_used_in_financing_activities=-110749,
        other_financing_activities=-6,
        net_change_in_cash=-1983,
    )
    out_path = write_company_csv([record], tmp_path, "EX")

    with out_path.open("r", newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        row = next(reader)

    assert row["Operating Expenses"] == "274891"
    assert row["Sales & Marketing"] == "25094"
    assert row["General & Administrative"] == "6210"
    assert row["Other Operating Expenses/Income"] == "-1000"
    assert row["Interest Income"] == "2825"
    assert row["Interest Expense"] == "2931"
    assert row["Other Income / Expense, Net"] == "-382"
    assert row["Diluted Shares"] == "16325819"
    assert row["Accounts Receivable"] == "60932"
    assert row["Inventory"] == "6331"
    assert row["Other Current Assets"] == "14695"
    assert row["Property & Equipment Net"] == "42117"
    assert row["Operating Lease ROU Asset"] == "10417"
    assert row["Goodwill"] == "18884"
    assert row["Other Assets Noncurrent"] == "18040"
    assert row["Accounts Payable"] == "64115"
    assert row["Accrued Expenses and Other"] == "76318"
    assert row["Unearned Revenue"] == "8249"
    assert row["Current Finance Lease Liabilities"] == "1632"
    assert row["Current Operating Lease Liabilities"] == "1876"
    assert row["Long-Term Finance Lease Liabilities"] == "859"
    assert row["Long-Term Operating Lease Liabilities"] == "8235"
    assert row["Other Long-Term Liabilities"] == "8249"
    assert row["Total Liabilities & Stockholders' Equity"] == "352755"
    assert row["Ending Cash"] == "24000"
    assert row["Depreciation and Amortization"] == "11104"
    assert row["Stock-Based Compensation"] == "9038"
    assert row["Deferred Taxes"] == "-4774"
    assert row["Other Non-Cash Items"] == "-2266"
    assert row["Unearned Revenue CF Change"] == "5632"
    assert row["Inventory CF Change"] == "-1046"
    assert row["Accounts Receivable CF Change"] == "-9343"
    assert row["Other Assets CF Change"] == "-6499"
    assert row["Accounts Payable CF Change"] == "9448"
    assert row["Accrued Expenses CF Change"] == "3852"
    assert row["Acquisitions"] == "-306"
    assert row["Net Cash Used in Investing Activities"] == "-22354"
    assert row["Purchases of Marketable Securities"] == "-76923"
    assert row["Sales / Maturities of Marketable Securities"] == "90000"
    assert row["Proceeds from Short-Term Debt and Other"] == "0"
    assert row["Repayments of Short-Term Debt and Other"] == "0"
    assert row["Proceeds from Long-Term Debt"] == "5465"
    assert row["Repayments of Long-Term Debt"] == "-9543"
    assert row["Finance Lease Principal Payments"] == "-435"
    assert row["Financing Obligation Principal Payments"] == "0"
    assert row["Net Cash Used in Financing Activities"] == "-110749"
    assert row["Other Financing Activities"] == "-6"
    assert row["Net Change in Cash"] == "-1983"


def test_milestone13_columns_blank_for_v1_style_record(tmp_path):
    """A v1-style record (only Revenue/Cost of Sales/Gross Profit
    populated) renders all 48 Milestone 13.1/13.2 columns blank."""
    record = _make_record("2021-12-31")
    out_path = write_company_csv([record], tmp_path, "EX")

    with out_path.open("r", newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        row = next(reader)

    milestone_13_columns = CSV_HEADER[35:]
    assert len(milestone_13_columns) == 48
    for col in milestone_13_columns:
        assert row[col] == ""
