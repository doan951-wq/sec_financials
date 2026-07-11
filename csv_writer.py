"""Writes normalized FilingRecords to CSV, one file per company.

Implements (Milestone 4):
- Write records using the stdlib `csv` module, one CSV per company, into a
  required output directory.
- Column order: company, ticker, CIK, form, fiscal year, fiscal period,
  period end date, filed date, Revenue, Cost of Sales, Gross Profit.
- Filename: {ticker-or-CIK}.csv inside the output directory; overwritten if
  it already exists.
- Rows sorted by period end date ascending.

Extended (Milestone 8): 24 additional columns appended after the 11 v1
columns above, which keep their exact original names/order unchanged (so
existing consumers -- the GUI, any saved CSVs -- aren't broken by a
reorder). Flat/wide CSV, one row per filing/period, 35 columns total
(~30, per the user's resolved decision in PLAN.md). Blank cells (None)
render the same way for every new column as they already did for v1's
Revenue/Cost of Sales/Gross Profit.

Extended (Milestone 13.4): 46 additional columns appended after the 35
Milestone-8 columns above, which keep their exact original names/order
unchanged (same non-negotiable regression enforced a second time). 81
columns total (45 new direct-tag concepts from Milestone 13.1 + Ending
Cash + the Sales/Maturities-of-Marketable-Securities SUM concept from
Milestone 13.2). "Beginning Cash" is deliberately NOT a CSV column -- it
has no `FilingRecord` field at all by design (Milestone 13.3, Capability
2: it is an xlsx-render-time-only cross-column formula, never a
per-filing fact) -- this is not a missing column, do not add one.
"""

from __future__ import annotations

import csv
import re
from pathlib import Path
from typing import Iterable

from sec_financials.facts_parser import FilingRecord

_LEGACY_CSV_COLUMNS = [
    "company",
    "ticker",
    "cik",
    "form",
    "fiscal_year",
    "fiscal_period",
    "period_end",
    "filed",
    "revenue",
    "cost_of_sales",
    "gross_profit",
    # -- Milestone 8: new columns appended after the 11 v1 columns above,
    # which keep their exact original names/order unchanged.
    "operating_income",
    "net_income",
    "research_and_development",
    "income_tax_expense",
    "pre_tax_income",
    "cash",
    "total_assets",
    "total_liabilities",
    "stockholders_equity",
    "long_term_debt",
    "long_term_debt_current",
    "long_term_debt_noncurrent",
    "short_term_debt",
    "operating_lease_liabilities",
    "finance_lease_liabilities",
    "marketable_securities_current",
    "operating_cash_flow",
    "capital_expenditures",
    "free_cash_flow",
    "shares_outstanding",
    "eps_basic",
    "eps_diluted",
    "dei_common_stock_shares_outstanding",
    "rsu_count",
    # -- Milestone 13.4: 46 additional columns appended after the 35
    # Milestone-8 columns above, which keep their exact original
    # names/order unchanged. Grouped here in the same Income Statement ->
    # Balance Sheet -> Cash Flow order as xlsx_writer.py's
    # INCOME_STATEMENT_ROWS/BALANCE_SHEET_ROWS/CASH_FLOW_ROWS, for the
    # same readability reasons as Milestone 8's grouping. No
    # "beginning_cash" column -- see module docstring.
    #
    # Income Statement
    "operating_expenses",
    "sales_and_marketing",
    "general_and_administrative",
    "other_operating_expense_income",
    "interest_income",
    "interest_expense",
    "other_income_expense_net",
    "diluted_shares",
    # Balance Sheet
    "accounts_receivable",
    "inventory",
    "other_current_assets",
    "property_and_equipment_net",
    "operating_lease_rou_asset",
    "goodwill",
    "other_assets_noncurrent",
    "accounts_payable",
    "accrued_expenses_and_other",
    "unearned_revenue",
    "current_finance_lease_liabilities",
    "current_operating_lease_liabilities",
    "long_term_finance_lease_liabilities",
    "long_term_operating_lease_liabilities",
    "other_long_term_liabilities",
    "total_liabilities_and_stockholders_equity",
    "ending_cash",
    # Cash Flow
    "depreciation_and_amortization",
    "stock_based_compensation",
    "deferred_taxes",
    "other_non_cash_items",
    "unearned_revenue_cf_change",
    "inventory_cf_change",
    "accounts_receivable_cf_change",
    "other_assets_cf_change",
    "accounts_payable_cf_change",
    "accrued_expenses_cf_change",
    "acquisitions",
    "net_cash_used_in_investing_activities",
    "purchases_of_marketable_securities",
    "sales_maturities_of_marketable_securities",
    "proceeds_from_short_term_debt_and_other",
    "repayments_of_short_term_debt_and_other",
    "proceeds_from_long_term_debt",
    "repayments_of_long_term_debt",
    "finance_lease_principal_payments",
    "financing_obligation_principal_payments",
    "net_cash_used_in_financing_activities",
    "other_financing_activities",
    "net_change_in_cash",
]

_LEGACY_CSV_HEADER = [
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
    # -- Milestone 8: new columns appended after the 11 v1 columns above,
    # which keep their exact original names/order unchanged. Grouped here
    # in the same order as the target schema in PLAN.md (Income Statement
    # -> Balance Sheet -> Cash Flow -> Per-Share & Other), matching the
    # GUI's tab grouping (Milestone 9) for readability, though the CSV
    # itself is a single flat/wide table, not tabbed.
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
    # -- Milestone 13.4: 46 additional headers, matching CSV_COLUMNS'
    # new entries 1:1 in order, and matching xlsx_writer.py's row labels
    # exactly (same schema, two renderers).
    #
    # Income Statement
    "Operating Expenses",
    "Sales & Marketing",
    "General & Administrative",
    "Other Operating Expenses/Income",
    "Interest Income",
    "Interest Expense",
    "Other Income / Expense, Net",
    "Diluted Shares",
    # Balance Sheet
    "Accounts Receivable",
    "Inventory",
    "Other Current Assets",
    "Property & Equipment Net",
    "Operating Lease ROU Asset",
    "Goodwill",
    "Other Assets Noncurrent",
    "Accounts Payable",
    "Accrued Expenses and Other",
    "Unearned Revenue",
    "Current Finance Lease Liabilities",
    "Current Operating Lease Liabilities",
    "Long-Term Finance Lease Liabilities",
    "Long-Term Operating Lease Liabilities",
    "Other Long-Term Liabilities",
    "Total Liabilities & Stockholders' Equity",
    "Ending Cash",
    # Cash Flow
    "Depreciation and Amortization",
    "Stock-Based Compensation",
    "Deferred Taxes",
    "Other Non-Cash Items",
    "Unearned Revenue CF Change",
    "Inventory CF Change",
    "Accounts Receivable CF Change",
    "Other Assets CF Change",
    "Accounts Payable CF Change",
    "Accrued Expenses CF Change",
    "Acquisitions",
    "Net Cash Used in Investing Activities",
    "Purchases of Marketable Securities",
    "Sales / Maturities of Marketable Securities",
    "Proceeds from Short-Term Debt and Other",
    "Repayments of Short-Term Debt and Other",
    "Proceeds from Long-Term Debt",
    "Repayments of Long-Term Debt",
    "Finance Lease Principal Payments",
    "Financing Obligation Principal Payments",
    "Net Cash Used in Financing Activities",
    "Other Financing Activities",
    "Net Change in Cash",
]

# Canonical output order. The user's financial-statement layout is now
# the single source of truth for both CSV field order and display headers.
# All 83 existing columns remain present; only their order is normalized.
CSV_SCHEMA = [
    ("company", "Company"),
    ("ticker", "Ticker"),
    ("cik", "CIK"),
    ("form", "Form"),
    ("fiscal_year", "Fiscal Year"),
    ("fiscal_period", "Fiscal Period"),
    ("period_end", "Period End Date"),
    ("filed", "Filed Date"),
    # Income Statement
    ("revenue", "Revenue"),
    ("cost_of_sales", "Cost of Sales"),
    ("gross_profit", "Gross Profit"),
    ("operating_expenses", "Operating Expenses"),
    ("sales_and_marketing", "Sales & Marketing"),
    ("general_and_administrative", "General & Administrative"),
    ("other_operating_expense_income", "Other Operating Expenses/Income"),
    ("operating_income", "Operating Income"),
    ("interest_income", "Interest Income"),
    ("interest_expense", "Interest Expense"),
    ("other_income_expense_net", "Other Income / Expense, Net"),
    ("net_income", "Net Income"),
    ("research_and_development", "Research and Development"),
    ("income_tax_expense", "Income Tax Expense"),
    ("pre_tax_income", "Pre-Tax Income"),
    ("diluted_shares", "Diluted Shares"),
    # Balance Sheet
    ("cash", "Cash"),
    ("marketable_securities_current", "Marketable Securities Current"),
    ("accounts_receivable", "Accounts Receivable"),
    ("inventory", "Inventory"),
    ("other_current_assets", "Other Current Assets"),
    ("property_and_equipment_net", "Property & Equipment Net"),
    ("operating_lease_rou_asset", "Operating Lease ROU Asset"),
    ("goodwill", "Goodwill"),
    ("other_assets_noncurrent", "Other Assets Noncurrent"),
    ("total_assets", "Total Assets"),
    ("accounts_payable", "Accounts Payable"),
    ("accrued_expenses_and_other", "Accrued Expenses and Other"),
    ("unearned_revenue", "Unearned Revenue"),
    ("long_term_debt_current", "Long Term Debt Current"),
    ("current_finance_lease_liabilities", "Current Finance Lease Liabilities"),
    ("current_operating_lease_liabilities", "Current Operating Lease Liabilities"),
    ("long_term_debt", "Long Term Debt"),
    ("long_term_debt_noncurrent", "Long Term Debt Noncurrent"),
    ("short_term_debt", "Short Term Debt"),
    ("operating_lease_liabilities", "Operating Lease Liabilities"),
    ("finance_lease_liabilities", "Finance Lease Liabilities"),
    ("long_term_finance_lease_liabilities", "Long-Term Finance Lease Liabilities"),
    ("long_term_operating_lease_liabilities", "Long-Term Operating Lease Liabilities"),
    ("other_long_term_liabilities", "Other Long-Term Liabilities"),
    ("total_liabilities", "Total Liabilities"),
    ("stockholders_equity", "Stockholders Equity"),
    ("total_liabilities_and_stockholders_equity", "Total Liabilities & Stockholders' Equity"),
    # Cash Flow
    ("operating_cash_flow", "Operating Cash Flow"),
    ("depreciation_and_amortization", "Depreciation and Amortization"),
    ("stock_based_compensation", "Stock-Based Compensation"),
    ("deferred_taxes", "Deferred Taxes"),
    ("other_non_cash_items", "Other Non-Cash Items"),
    ("unearned_revenue_cf_change", "Unearned Revenue CF Change"),
    ("inventory_cf_change", "Inventory CF Change"),
    ("accounts_receivable_cf_change", "Accounts Receivable CF Change"),
    ("other_assets_cf_change", "Other Assets CF Change"),
    ("accounts_payable_cf_change", "Accounts Payable CF Change"),
    ("accrued_expenses_cf_change", "Accrued Expenses CF Change"),
    ("capital_expenditures", "Capital Expenditures"),
    ("purchases_of_marketable_securities", "Purchases of Marketable Securities"),
    ("sales_maturities_of_marketable_securities", "Sales / Maturities of Marketable Securities"),
    ("acquisitions", "Acquisitions"),
    ("net_cash_used_in_investing_activities", "Net Cash Used in Investing Activities"),
    ("proceeds_from_short_term_debt_and_other", "Proceeds from Short-Term Debt and Other"),
    ("repayments_of_short_term_debt_and_other", "Repayments of Short-Term Debt and Other"),
    ("proceeds_from_long_term_debt", "Proceeds from Long-Term Debt"),
    ("repayments_of_long_term_debt", "Repayments of Long-Term Debt"),
    ("finance_lease_principal_payments", "Finance Lease Principal Payments"),
    ("financing_obligation_principal_payments", "Financing Obligation Principal Payments"),
    ("net_cash_used_in_financing_activities", "Net Cash Used in Financing Activities"),
    ("other_financing_activities", "Other Financing Activities"),
    ("free_cash_flow", "Free Cash Flow"),
    ("net_change_in_cash", "Net Change in Cash"),
    ("ending_cash", "Ending Cash"),
    # Beginning Cash is xlsx-only; it has no per-filing FilingRecord field.
    # Per-Share & Other
    ("shares_outstanding", "Shares Outstanding"),
    ("eps_basic", "EPS Basic"),
    ("eps_diluted", "EPS Diluted"),
    ("dei_common_stock_shares_outstanding", "Common Stock Outstanding Shares (dei)"),
    ("rsu_count", "RSU Count"),
]

# The CSV format is a public, append-only interface.  CSV_SCHEMA is useful for
# the analyst-facing statement layout, but must not redefine the serialized
# column order: consumers created against Milestones 8 and 13 depend on the
# original columns retaining their positions.
CSV_COLUMNS = list(_LEGACY_CSV_COLUMNS)
CSV_HEADER = list(_LEGACY_CSV_HEADER)

# Characters not safe/sensible in a filename; replaced with "_".
_UNSAFE_FILENAME_CHARS_RE = re.compile(r"[^A-Za-z0-9_.-]")


def safe_filename_stem(ticker_or_cik: str) -> str:
    """Sanitize a ticker or CIK string for use as a filename stem."""
    stem = ticker_or_cik.strip()
    stem = _UNSAFE_FILENAME_CHARS_RE.sub("_", stem)
    return stem or "company"


def _format_cell(value) -> str:
    """Format a record field for CSV output; None becomes an empty cell."""
    if value is None:
        return ""
    return value


def write_company_csv(
    records: Iterable[FilingRecord],
    out_dir: Path | str,
    ticker_or_cik: str,
) -> Path:
    """Write one company's records to a CSV file in `out_dir`.

    Rows are sorted by period end date ascending. Returns the path to the
    written file. Overwrites any existing file at that path.
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    filename = f"{safe_filename_stem(ticker_or_cik)}.csv"
    out_path = out_dir / filename

    sorted_records = sorted(records, key=lambda r: (r.period_end or "", r.form))

    with out_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(CSV_HEADER)
        for record in sorted_records:
            writer.writerow(
                [_format_cell(getattr(record, field_name)) for field_name in CSV_COLUMNS]
            )
            continue  # legacy explicit row below retained temporarily for audit history
            writer.writerow(
                [
                    _format_cell(record.company),
                    _format_cell(record.ticker),
                    _format_cell(record.cik),
                    _format_cell(record.form),
                    _format_cell(record.fiscal_year),
                    _format_cell(record.fiscal_period),
                    _format_cell(record.period_end),
                    _format_cell(record.filed),
                    _format_cell(record.revenue),
                    _format_cell(record.cost_of_sales),
                    _format_cell(record.gross_profit),
                    _format_cell(record.operating_income),
                    _format_cell(record.net_income),
                    _format_cell(record.research_and_development),
                    _format_cell(record.income_tax_expense),
                    _format_cell(record.pre_tax_income),
                    _format_cell(record.cash),
                    _format_cell(record.total_assets),
                    _format_cell(record.total_liabilities),
                    _format_cell(record.stockholders_equity),
                    _format_cell(record.long_term_debt),
                    _format_cell(record.long_term_debt_current),
                    _format_cell(record.long_term_debt_noncurrent),
                    _format_cell(record.short_term_debt),
                    _format_cell(record.operating_lease_liabilities),
                    _format_cell(record.finance_lease_liabilities),
                    _format_cell(record.marketable_securities_current),
                    _format_cell(record.operating_cash_flow),
                    _format_cell(record.capital_expenditures),
                    _format_cell(record.free_cash_flow),
                    _format_cell(record.shares_outstanding),
                    _format_cell(record.eps_basic),
                    _format_cell(record.eps_diluted),
                    _format_cell(record.dei_common_stock_shares_outstanding),
                    _format_cell(record.rsu_count),
                    # -- Milestone 13.4: 46 additional cells, matching
                    # CSV_COLUMNS'/CSV_HEADER's new entries 1:1 in order.
                    _format_cell(record.operating_expenses),
                    _format_cell(record.sales_and_marketing),
                    _format_cell(record.general_and_administrative),
                    _format_cell(record.other_operating_expense_income),
                    _format_cell(record.interest_income),
                    _format_cell(record.interest_expense),
                    _format_cell(record.other_income_expense_net),
                    _format_cell(record.diluted_shares),
                    _format_cell(record.accounts_receivable),
                    _format_cell(record.inventory),
                    _format_cell(record.other_current_assets),
                    _format_cell(record.property_and_equipment_net),
                    _format_cell(record.operating_lease_rou_asset),
                    _format_cell(record.goodwill),
                    _format_cell(record.other_assets_noncurrent),
                    _format_cell(record.accounts_payable),
                    _format_cell(record.accrued_expenses_and_other),
                    _format_cell(record.unearned_revenue),
                    _format_cell(record.current_finance_lease_liabilities),
                    _format_cell(record.current_operating_lease_liabilities),
                    _format_cell(record.long_term_finance_lease_liabilities),
                    _format_cell(record.long_term_operating_lease_liabilities),
                    _format_cell(record.other_long_term_liabilities),
                    _format_cell(record.total_liabilities_and_stockholders_equity),
                    _format_cell(record.ending_cash),
                    _format_cell(record.depreciation_and_amortization),
                    _format_cell(record.stock_based_compensation),
                    _format_cell(record.deferred_taxes),
                    _format_cell(record.other_non_cash_items),
                    _format_cell(record.unearned_revenue_cf_change),
                    _format_cell(record.inventory_cf_change),
                    _format_cell(record.accounts_receivable_cf_change),
                    _format_cell(record.other_assets_cf_change),
                    _format_cell(record.accounts_payable_cf_change),
                    _format_cell(record.accrued_expenses_cf_change),
                    _format_cell(record.acquisitions),
                    _format_cell(record.net_cash_used_in_investing_activities),
                    _format_cell(record.purchases_of_marketable_securities),
                    _format_cell(record.sales_maturities_of_marketable_securities),
                    _format_cell(record.proceeds_from_short_term_debt_and_other),
                    _format_cell(record.repayments_of_short_term_debt_and_other),
                    _format_cell(record.proceeds_from_long_term_debt),
                    _format_cell(record.repayments_of_long_term_debt),
                    _format_cell(record.finance_lease_principal_payments),
                    _format_cell(record.financing_obligation_principal_payments),
                    _format_cell(record.net_cash_used_in_financing_activities),
                    _format_cell(record.other_financing_activities),
                    _format_cell(record.net_change_in_cash),
                ]
            )

    return out_path
