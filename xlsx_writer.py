"""Writes a styled per-company `.xlsx` workbook alongside the CSV, visually
modeled on a user-provided analyst template
(`/Users/tonyledoan/Documents/AMZN_Company_ModelMain.xlsx`, sheet
"Historical Financial Data").

Implements Milestone 12:
- 12.1: `openpyxl`-availability check / soft-fail seam
  (`XlsxWriterUnavailableError`).
- 12.2: 5-fiscal-year trailing window + long-to-wide pivot (pure data
  transform, no styling).
- 12.3a/12.3b: styling (fonts, fills, number formats, column widths) and
  writing the real pivoted data into a styled sheet.

This module is soft-fail by design (Decision B in PLAN.md): if `openpyxl`
isn't importable, callers (`cli.py`/`gui.py`) must catch
`XlsxWriterUnavailableError` and continue writing the CSV regardless --
`.xlsx` output is purely additive and must never block CSV output.
"""

from __future__ import annotations

from dataclasses import dataclass

from sec_financials.csv_writer import safe_filename_stem
from sec_financials.facts_parser import (
    CONCEPT_BY_KEY,
    DURATION,
    INSTANT,
    SUM,
    FilingRecord,
    _index_records_by_year_period,
)

# -- Milestone 12.1: openpyxl-availability check / soft-fail seam ------------

OPENPYXL_INSTALL_COMMAND = "pip3 install --user --break-system-packages openpyxl"


class XlsxWriterUnavailableError(Exception):
    """Raised when `openpyxl` is not importable in the running Python.

    Callers (`cli.py`/`gui.py`) must catch this specifically and continue
    writing the CSV regardless -- `.xlsx` output is optional/soft-fail per
    Decision B in PLAN.md; CSV output (zero new dependencies) must never be
    blocked by this.
    """


def _require_openpyxl():
    """Import and return the `openpyxl` module, or raise
    `XlsxWriterUnavailableError` with a clear, actionable message pointing
    at the one-time install command."""
    try:
        import openpyxl
    except ImportError as exc:
        raise XlsxWriterUnavailableError(
            "The '.xlsx' output feature requires the 'openpyxl' package, "
            "which is not installed in this Python environment. Install it "
            f"once with:\n\n    {OPENPYXL_INSTALL_COMMAND}\n\n"
            "CSV output is unaffected and was written successfully; only "
            "the '.xlsx' file was skipped."
        ) from exc
    return openpyxl


# -- Milestone 12.2: long-to-wide pivot logic (pure data transform) ----------
#
# This section has no dependency on openpyxl -- it operates purely on
# `FilingRecord` objects (the same objects `csv_writer.write_company_csv`
# already consumes) and plain Python data structures, so it can be built
# and unit-tested in isolation from any styling code (same discipline as
# the Milestone 7.1/7.2/7.3 split).

DEFAULT_TRAILING_WINDOW_YEARS = 5

# Section groupings for the xlsx sheet, reusing gui.py's exact TABS
# groupings (Income Statement / Balance Sheet / Cash Flow / Per-Share &
# Other) mapped from CSV header string -> FilingRecord field name via
# csv_writer.CSV_HEADER/CSV_COLUMNS (not re-decided, not a new parallel
# list). "Gross Margin %" is intentionally excluded: it's a GUI-only
# computed display column with no FilingRecord field/CSV column backing
# it, not a metric we "have data for" in the sense Milestone 12.2 means.
#
# Each entry: (section title, [(display label, FilingRecord field name), ...]).
INCOME_STATEMENT_ROWS = [
    ("Revenue", "revenue"),
    ("Cost of Sales", "cost_of_sales"),
    ("Gross Profit", "gross_profit"),
    ("Operating Expenses", "operating_expenses"),
    ("Fulfillment", "fulfillment"),
    ("Technology & Infrastructure", "technology_and_infrastructure"),
    ("Sales & Marketing", "sales_and_marketing"),
    ("General & Administrative", "general_and_administrative"),
    ("Other Operating Expenses/Income", "other_operating_expense_income"),
    ("Operating Income", "operating_income"),
    ("Interest Income", "interest_income"),
    ("Interest Expense", "interest_expense"),
    ("Other Income / Expense, Net", "other_income_expense_net"),
    ("Pre-Tax Income", "pre_tax_income"),
    ("Income Tax Expense", "income_tax_expense"),
    ("Net Income", "net_income"),
    ("Diluted Shares", "diluted_shares"),
]

BALANCE_SHEET_ROWS = [
    ("Cash & Cash Equivalents", "cash"),
    ("Marketable Securities", "marketable_securities_current"),
    ("Accounts Receivable, net & other", "accounts_receivable"),
    ("Inventory", "inventory"),
    ("Other Current Assets (not separately disclosed)", "other_current_assets"),
    ("Total Current Assets", "total_current_assets"),
    ("Property & Equpment, Net", "property_and_equipment_net"),
    ("Operating Lease", "operating_lease_rou_asset"),
    ("Goodwill", "goodwill"),
    ("Other Assets Non current", "other_assets_noncurrent"),
    ("Total Assets", "total_assets"),
    ("Accounts Payable", "accounts_payable"),
    ("Accrued Expenses and Other", "accrued_expenses_and_other"),
    ("Unearned Revenue", "unearned_revenue"),
    ("Current Portion of Long-Term Debt", "long_term_debt_current"),
    ("Current Finance Lease Liabilities", "current_finance_lease_liabilities"),
    ("Current Operating Lease Liabilities", "current_operating_lease_liabilities"),
    ("Current Financing Obligations", "current_financing_obligations"),
    ("Long-Term Debt", "long_term_debt_noncurrent"),
    ("Long-Term Finance Lease Liabilities ", "long_term_finance_lease_liabilities"),
    ("Long-Term Operating Lease Liabilities ", "long_term_operating_lease_liabilities"),
    ("Other Long-Term Liabilities", "other_long_term_liabilities"),
    ("Total Current Liabilities", "total_current_liabilities"),
    ("Total Liabilities", "total_liabilities"),
    ("Stockholders’ Equity", "stockholders_equity"),
    ("Total Liabilities & Stockholders’ Equity", "total_liabilities_and_stockholders_equity"),
]

CASH_FLOW_ROWS = [
    ("Net Income", "net_income"),
    ("D&A", "depreciation_and_amortization"),
    ("Stock-Based Compensation", "stock_based_compensation"),
    ("Deferred Taxes", "deferred_taxes"),
    ("Non-operating expense (income), net", "other_non_cash_items"),
    ("Other Non-Cash Items", "__blank_other_non_cash_items"),
    ("Unearned Revenue", "unearned_revenue_cf_change"),
    ("Inventory", "inventory_cf_change"),
    ("Accounts Receivable", "accounts_receivable_cf_change"),
    ("Other Assets", "other_assets_cf_change"),
    ("Accounts Payable", "accounts_payable_cf_change"),
    ("Accrued Expenses", "accrued_expenses_cf_change"),
    ("Net Cash Provided by Operating Activities", "operating_cash_flow"),
    ("CapEx / Purchases of PP&E", "capital_expenditures"),
    ("Purchases of Marketable Securities", "purchases_of_marketable_securities"),
    ("Acquisitions", "acquisitions"),
    ("Proceeds from PP&E Sales & Incentives", "proceeds_from_ppe_sales_and_incentives"),
    ("Sales / Maturities of Marketable Securities", "sales_maturities_of_marketable_securities"),
    ("Net Cash Used in Investing Activities", "net_cash_used_in_investing_activities"),
    ("Proceeds from Short-Term Debt and Other", "proceeds_from_short_term_debt_and_other"),
    ("Repayments of Short-Term Debt and Other", "repayments_of_short_term_debt_and_other"),
    ("Proceeds from Long-Term Debt", "proceeds_from_long_term_debt"),
    ("Repayments of Long-Term Debt", "repayments_of_long_term_debt"),
    ("Finance Lease Principal Payments", "finance_lease_principal_payments"),
    ("Financing Obligation Principal Payments", "financing_obligation_principal_payments"),
    ("Net Cash Used in Financing Activities", "net_cash_used_in_financing_activities"),
    ("Other Financing Activities", "other_financing_activities"),
    ("Net Change in Cash", "net_change_in_cash"),
    ("Ending Cash", "ending_cash"),
]

PER_SHARE_ROWS = [
    ("Shares Outstanding", "shares_outstanding"),
    ("EPS Basic", "eps_basic"),
    ("EPS Diluted", "eps_diluted"),
    ("Common Stock Outstanding Shares (dei)", "dei_common_stock_shares_outstanding"),
    ("RSU Count", "rsu_count"),
    ("Diluted Shares", "diluted_shares"),
]

CASH_FLOW_SECTION_TITLE = "Cash Flow"

SHEET_SECTIONS = [
    ("Income Statement", INCOME_STATEMENT_ROWS),
    ("Balance Sheet", BALANCE_SHEET_ROWS),
    (CASH_FLOW_SECTION_TITLE, CASH_FLOW_ROWS),
    ("Per-Share & Other", PER_SHARE_ROWS),
]

# Milestone 13.3 (Capability 2): "Beginning Cash" is an xlsx-only pivot
# row appended at build_pivot time, immediately after Ending Cash at the
# end of the Cash Flow section -- see build_pivot's dedicated handling
# below. It has NO CONCEPT_TABLE entry and NO FilingRecord field (by
# design); BEGINNING_CASH_FIELD_NAME is a rendering-only sentinel used
# only for MetricRow.field_name / number-format lookup (falls through to
# the blanket dollar format, correctly, since it isn't in EPS_FIELDS/
# SHARE_COUNT_FIELDS) -- never looked up via _field_kind or
# CONCEPT_BY_KEY.
BEGINNING_CASH_LABEL = "Beginning Cash"
BEGINNING_CASH_FIELD_NAME = "beginning_cash"

# EPS Basic/Diluted are DURATION in CONCEPT_TABLE but get NO Q4-plug
# formula (per the template's own verified convention -- see PLAN.md,
# Milestone 12's template re-verification note): Diluted Shares/Diluted
# EPS are hardcoded literals in every column in the template, including
# Q4, with no plug. In practice this means these rows' Q4 column is
# simply blank (no direct Q4 EPS is ever reported by SEC filers), but the
# *mechanism* is "skip the Q4-plug formula for this field", named here as
# an explicit exception list rather than a silent special case buried in
# the formula-generation function.
#
# Milestone 13.1 implementation note: "diluted_shares" (weighted-average
# diluted shares outstanding, `WeightedAverageNumberOfDilutedSharesOutstanding`)
# is added to this same exception set. This wasn't spelled out explicitly
# in PLAN.md's Milestone 13 task list, but is the same DURATION-shaped,
# weighted-average-over-a-period quantity as EPS Basic/Diluted -- SEC
# filers never report a standalone Q4-only weighted-average share count,
# and summing/subtracting weighted averages across quarters (what a Q4
# plug formula would do) isn't a meaningful operation for a weighted
# average the way it is for an additive dollar flow. Treating it
# identically to EPS's already-established exception is the correct
# extension of the existing convention, not a new decision.
NO_Q4_PLUG_FIELDS = {"eps_basic", "eps_diluted", "diluted_shares"}

# free_cash_flow has no ConceptSpec in facts_parser.CONCEPT_TABLE (it's a
# purely derived metric, not a direct-tag lookup -- see Milestone 7.3).
# Both of its inputs (Operating Cash Flow, Capital Expenditures) are
# DURATION concepts, and the derived value itself is additive across
# quarters the same way, so it is classified DURATION here as the one
# necessary fallback beyond CONCEPT_TABLE's own classification.
_FIELD_KIND_FALLBACK = {"free_cash_flow": DURATION}


def _field_kind(field_name: str) -> str:
    """Return DURATION or INSTANT for a FilingRecord field name, per
    facts_parser.CONCEPT_TABLE's `kind` classification (the single source
    of truth also used by the CSV/GUI), with one named fallback for
    free_cash_flow (not in CONCEPT_TABLE -- see _FIELD_KIND_FALLBACK).

    Milestone 13.5: a SUM-kind concept (facts_parser.SUM) is classified
    DURATION here for Q4-plug purposes -- a summed duration flow (e.g.
    Sales/Maturities of Marketable Securities) is still additive across
    quarters the exact same way any other duration/flow metric is; no new
    Q4-formula shape is needed for it, per PLAN.md's explicit resolution.
    """
    spec = CONCEPT_BY_KEY.get(field_name)
    if spec is not None:
        if spec.kind == SUM:
            return DURATION
        return spec.kind
    if field_name in _FIELD_KIND_FALLBACK:
        return _FIELD_KIND_FALLBACK[field_name]
    raise KeyError(
        f"No duration/instant classification found for field {field_name!r} "
        "-- add it to facts_parser.CONCEPT_TABLE or xlsx_writer._FIELD_KIND_FALLBACK."
    )


def _filter_to_trailing_window(
    records: list[FilingRecord], num_years: int = DEFAULT_TRAILING_WINDOW_YEARS
) -> list[FilingRecord]:
    """Filter `records` to the most recent `num_years` fiscal years present
    in the data (the max `fiscal_year` across all records, plus the
    `num_years - 1` immediately prior).

    Pure pre-filter step (Decision A / Milestone 12.2): a live, per-fetch
    computation off the records' own `fiscal_year` field, not a fixed
    calendar year. If the company's total history is shorter than
    `num_years`, returns however many years exist -- no padding, no error.
    Records with no usable integer `fiscal_year` are dropped (there is no
    year to place them in the pivot).
    """
    years: set[int] = set()
    for r in records:
        try:
            years.add(int(r.fiscal_year))
        except (TypeError, ValueError):
            continue
    if not years:
        return []

    max_year = max(years)
    min_year_in_window = max_year - (num_years - 1)

    def _in_window(r: FilingRecord) -> bool:
        try:
            y = int(r.fiscal_year)
        except (TypeError, ValueError):
            return False
        return min_year_in_window <= y <= max_year

    return [r for r in records if _in_window(r)]


@dataclass(frozen=True)
class PeriodColumn:
    """One period column in the pivoted sheet: a fiscal year plus either a
    quarter label ("Q1".."Q4") or None for the year's FY column.

    `label` is the column header text the template uses ("Q1"/"Q2"/"Q3"/
    "Q4"/"FY" -- the fiscal year itself is shown in the row below, per the
    template's own two-row period-header block).
    """

    fiscal_year: int
    quarter: str | None  # "Q1"/"Q2"/"Q3"/"Q4", or None for the FY column

    @property
    def label(self) -> str:
        return self.quarter if self.quarter is not None else "FY"

    @property
    def is_fy(self) -> bool:
        return self.quarter is None


def _period_columns_for_window(
    records: list[FilingRecord],
) -> list[PeriodColumn]:
    """Determine the ordered list of period columns for an already
    -windowed record list, per Milestone 12.2's exact rule:

    - Years are ordered oldest to newest.
    - The oldest year in the window gets ONLY its FY column (no quarters),
      matching the template's own leftmost-column convention and
      sidestepping the Q4-plug edge case at the window's left edge.
    - Every subsequent year (including the newest/rightmost year, even if
      it has no FY/10-K row yet -- verified live against the real AMZN
      CSV shape) gets `Q1`-`Q4` columns **for whichever quarters actually
      have a filing** (per Decision A's edge-case rule wording exactly)
      plus its own FY column (present even if blank -- see Milestone
      12.2/Decision A's edge-case rule). Since SEC filers never directly
      file a standalone "Q4" period (Q4 is always derived via the plug
      formula, never its own 10-Q), "Q4 has a filing" is operationalized
      as "Q1, Q2, AND Q3 are all present for this year" -- i.e. the
      quarterly cadence is complete enough that a Q4 figure can be
      derived. A partially-filed year (e.g. the real AMZN FY2026 shape:
      only Q1 filed so far) gets only the quarter columns that actually
      have filings (Q1) -- no Q2/Q3/Q4 columns are fabricated ahead of
      their own filings existing. A year with no quarterly filings at all
      (10-K-only year) gets only its FY column, same shape as the
      oldest-year rule.
    """
    years_present: set[int] = set()
    quarters_by_year: dict[int, set[str]] = {}

    for r in records:
        try:
            year = int(r.fiscal_year)
        except (TypeError, ValueError):
            continue
        years_present.add(year)
        fp = (r.fiscal_period or "").upper()
        if fp in ("Q1", "Q2", "Q3", "Q4"):
            quarters_by_year.setdefault(year, set()).add(fp)

    ordered_years = sorted(years_present)
    columns: list[PeriodColumn] = []
    for i, year in enumerate(ordered_years):
        is_oldest = i == 0
        if is_oldest:
            # Oldest window year: FY-only, no quarters, regardless of
            # whether quarterly filings exist for it in the source data.
            columns.append(PeriodColumn(year, None))
            continue

        quarters_filed = quarters_by_year.get(year, set())
        quarter_columns_to_emit = set(quarters_filed)
        if {"Q1", "Q2", "Q3"}.issubset(quarters_filed):
            # Complete quarterly cadence for this year -- Q4 is derivable
            # via the plug formula even though never itself directly
            # filed, so its column belongs in the layout.
            quarter_columns_to_emit.add("Q4")

        for q in ("Q1", "Q2", "Q3", "Q4"):
            if q in quarter_columns_to_emit:
                columns.append(PeriodColumn(year, q))
        # (A non-oldest year with no quarterly filings at all -- a
        # 10-K-only year -- simply emits no quarter columns above and
        # falls through to the FY-only column below, same shape as the
        # oldest-year rule.)

        # FY column always present for non-oldest years, even if no FY
        # filing exists yet (e.g. the real AMZN FY2026 shape: Q1-only,
        # zero FY rows) -- blank cells are handled at render time, not by
        # omitting the column.
        columns.append(PeriodColumn(year, None))

    return columns


def column_letter(index: int) -> str:
    """1-based column index -> Excel column letter(s) (1 -> 'A', 27 ->
    'AA'). Thin wrapper so this module doesn't need openpyxl imported for
    the pure pivot logic/tests -- openpyxl's own `get_column_letter` does
    the same thing but pulling it in here would make 12.2's tests depend
    on openpyxl being installed, which the plan explicitly wants to avoid."""
    if index < 1:
        raise ValueError(f"Column index must be >= 1, got {index}")
    letters = ""
    n = index
    while n > 0:
        n, remainder = divmod(n - 1, 26)
        letters = chr(ord("A") + remainder) + letters
    return letters


# Fixed leading columns before period-data columns start: A = spacer,
# B = row label. Data columns start at column C (index 3), matching the
# template's own layout where column B holds labels and C+ hold data
# (the template's own leftmost data column happens to be G/1st FY column
# because it also has a Revenue Build section wider than ours -- for our
# narrower 4-section sheet, data starts at column C).
LABEL_COLUMN_INDEX = 2  # column B
FIRST_DATA_COLUMN_INDEX = 3  # column C


@dataclass(frozen=True)
class CellPlan:
    """What to write into one (metric row, period column) cell: either a
    literal value, a formula string, or neither (blank)."""

    value: int | float | None = None
    formula: str | None = None

    @property
    def is_blank(self) -> bool:
        return self.value is None and self.formula is None


@dataclass(frozen=True)
class MetricRow:
    """One metric's pivoted row: display label, FilingRecord field name,
    duration/instant kind, the real 1-based sheet row number this row will
    occupy, and a CellPlan per period column.

    `row_number` is computed by `build_pivot` itself (not deferred to
    render time): the section/row layout is fully fixed ahead of time
    (SHEET_SECTIONS, plus a fixed number of header rows per section --
    see `HEADER_ROWS_PER_SECTION`), so real row numbers -- and therefore
    real Q4-plug formula strings with real cell references -- can be
    materialized here in the pure pivot step, independent of any styling
    code. This is what makes the formula-string-generation function
    independently unit-testable (Milestone 12.2/12.5's most important
    verification target) without needing openpyxl or a real written
    sheet.
    """

    label: str
    field_name: str
    kind: str  # DURATION or INSTANT
    row_number: int
    cells: dict[PeriodColumn, CellPlan]


@dataclass(frozen=True)
class PivotResult:
    """Output of `build_pivot`: the ordered period columns (with their
    resolved column indices) and, per section, per metric row (with its
    real sheet row number), the cell value/formula to write in each
    period column.

    `column_map`: PeriodColumn -> 1-based column index, the same
    structure the cell-reference cross-check (Milestone 12.5 verification
    technique 4) looks up generated Q4 formula coordinates against.
    """

    columns: list[PeriodColumn]
    column_map: dict[PeriodColumn, int]
    sections: list[tuple[str, list[MetricRow]]]


# Fixed number of header/title rows preceding a section's first metric row
# once real styling is applied (Milestone 12.3b): sheet title + company
# identification rows, then per-section a section-title row + a 2-row
# period-header block (label row + fiscal-year row) before the first
# metric row. `build_pivot` needs this to compute *some* real row number
# per metric row so Q4-plug formulas reference real cells -- the exact
# constant is a rendering-layout decision owned here (not re-decided by
# the renderer) so the pivot and the renderer never disagree about which
# row a metric lives on.
#
# SHEET_TITLE_ROWS: number of rows occupied by the sheet title + company
# -identification row + one blank spacer row before the first section
# starts (rows 1-3; the first section's title row is row 4).
SHEET_TITLE_ROWS = 3
# SECTION_HEADER_ROWS: number of rows from a section's OWN title row
# (exclusive) to its first metric row (inclusive) -- i.e. section-title
# row + period-label row + fiscal-year row = 3 header rows, so the first
# metric row is (section title row) + 3.
SECTION_HEADER_ROWS = 3


def _q4_formula(fy_col_letter: str, q1_col_letter: str, q3_col_letter: str, row_number: int) -> str:
    """Build the Q4-plug formula string for one metric row, matching the
    template's own verified convention exactly:
    `=<FY_cell>-SUM(<Q1_cell>:<Q3_cell>)`, e.g. `=L18-SUM(H18:J18)`."""
    fy_cell = f"{fy_col_letter}{row_number}"
    q1_cell = f"{q1_col_letter}{row_number}"
    q3_cell = f"{q3_col_letter}{row_number}"
    return f"={fy_cell}-SUM({q1_cell}:{q3_cell})"


# -- Milestone 13.3 (Capability 2): Beginning Cash cross-period derivation --
#
# "Beginning Cash" is NOT a CONCEPT_TABLE/FilingRecord field -- it has no
# per-filing fact to extract at all. It exists only as an xlsx-render-time
# pivot row: for each period column, a live Excel formula referencing the
# immediately-preceding period column's Ending Cash cell (same row, prior
# column), extending the already-shipped `_q4_mirror_formula`
# cross-column-reference pattern to reference a PRIOR COLUMN's cell
# (rather than the SAME column's FY cell).
#
# Named generically (not "beginning cash"-specific in its core logic) so
# a later milestone adding Change in NWC can reuse the same adjacency rule
# -- see PLAN.md's Capability 3 deferred note.


def _prior_period_column(col: "PeriodColumn", columns_by_year: dict[int, list["PeriodColumn"]]) -> "PeriodColumn | None":
    """Return the PeriodColumn that is the "immediately prior period" for
    `col`, per Milestone 13.3's resolved adjacency rule, or `None` if there
    is no prior column at all (the window's oldest column).

    Purely positional (left-to-right in the rendered sheet) for every
    column kind EXCEPT a non-oldest year's own FY column:
    - Q2's predecessor is Q1 in the same year (positional).
    - Q3's predecessor is Q2 in the same year (positional).
    - Q1's predecessor is the prior year's FY column (positional -- the
      prior year's FY column is the immediately-preceding column in the
      sheet's left-to-right layout).
    - A non-oldest year's OWN FY column's predecessor is the PRIOR FISCAL
      YEAR'S FY column (semantic beginning-of-year convention) -- this is
      an explicit special case, not "one column to the left" (which would
      wrongly resolve to that same year's own Q4 column, since Q4 sits
      positionally immediately left of FY in the sheet). Implemented as a
      direct year-to-year lookup so this one non-positional case is
      structurally distinguishable in the code from the three positional
      cases above, rather than relying on the sheet's left-to-right
      column order coincidentally lining up.
    - The oldest column in the window (always an FY-only column, per
      `_period_columns_for_window`) has no predecessor at all: `None`.
    """
    years_sorted = sorted(columns_by_year)
    if not years_sorted:
        return None
    oldest_year = years_sorted[0]

    if col.is_fy:
        if col.fiscal_year == oldest_year:
            # Oldest window year's FY column: nothing precedes it at all.
            return None
        # Semantic beginning-of-year rule: the PRIOR FISCAL YEAR'S FY
        # column, not that same year's positionally-adjacent Q4 column.
        prior_year = col.fiscal_year - 1
        for c in columns_by_year.get(prior_year, []):
            if c.is_fy:
                return c
        # Prior fiscal year isn't in this window at all (shouldn't happen
        # for a non-oldest year, since every year in the window has an FY
        # column per _period_columns_for_window, but handled defensively).
        return None

    # Quarter columns: purely positional, same-year predecessor for
    # Q2/Q3/Q4 (Q1<-none within year), Q1's predecessor is the prior
    # year's FY column.
    quarter_order = {"Q1": 1, "Q2": 2, "Q3": 3, "Q4": 4}
    this_q = quarter_order[col.quarter]
    if this_q > 1:
        target_q = {2: "Q1", 3: "Q2", 4: "Q3"}[this_q]
        for c in columns_by_year.get(col.fiscal_year, []):
            if c.quarter == target_q:
                return c
        return None  # that quarter's column doesn't exist in this window

    # Q1: predecessor is the prior year's FY column.
    prior_year = col.fiscal_year - 1
    for c in columns_by_year.get(prior_year, []):
        if c.is_fy:
            return c
    return None


def _prior_column_cell(
    col: "PeriodColumn",
    row_number: int,
    columns_by_year: dict[int, list["PeriodColumn"]],
    column_map: dict["PeriodColumn", int],
) -> str | None:
    """Return the cell reference (e.g. "C71") for `row_number` in the
    column immediately preceding `col`, per `_prior_period_column`'s
    adjacency rule, or `None` if there is no preceding column."""
    prior_col = _prior_period_column(col, columns_by_year)
    if prior_col is None or prior_col not in column_map:
        return None
    return f"{column_letter(column_map[prior_col])}{row_number}"


def _beginning_cash_formula(prior_cell: str | None) -> str | None:
    """Build the Beginning Cash formula string for one period column: a
    direct reference to the prior period column's Ending Cash cell (e.g.
    `=C155`), or `None` (blank cell, no formula) if there is no prior
    column to reference."""
    if prior_cell is None:
        return None
    return f"={prior_cell}"


def _q4_mirror_formula(fy_col_letter: str, row_number: int) -> str:
    """Build the Q4-mirror formula string for one INSTANT-kind metric row,
    matching the template's own verified convention exactly: the Q4 column
    simply references that same fiscal year's FY column cell, e.g. `=L71`
    (verified against the real template's Cash & Cash Equivalents row).

    A fiscal-year-end balance sheet snapshot IS the Q4-end snapshot -- same
    date, same value -- so unlike duration/flow metrics (which need a
    plug/subtraction formula to derive a standalone Q4 figure), instant
    metrics' Q4 column should just mirror the FY column, never be blank."""
    fy_cell = f"{fy_col_letter}{row_number}"
    return f"={fy_cell}"


def build_pivot(
    records: list[FilingRecord],
    num_years: int = DEFAULT_TRAILING_WINDOW_YEARS,
) -> PivotResult:
    """Build the full long-to-wide pivot for one company's records: apply
    the trailing-window filter, determine period columns, and compute
    every section/row/cell's value or formula -- including real Q4-plug
    formula strings with real column letters and row numbers.

    Pure data transform -- no openpyxl dependency, no styling.
    """
    windowed = _filter_to_trailing_window(records, num_years=num_years)
    columns = _period_columns_for_window(windowed)
    column_map = {col: FIRST_DATA_COLUMN_INDEX + i for i, col in enumerate(columns)}
    by_year_period = _index_records_by_year_period(windowed)
    all_by_year_period = _index_records_by_year_period(records)

    # Group columns by fiscal year for Q4-plug eligibility checks (needs
    # FY + Q1 + Q2 + Q3 all present as columns for that year).
    columns_by_year: dict[int, list[PeriodColumn]] = {}
    for col in columns:
        columns_by_year.setdefault(col.fiscal_year, []).append(col)
    year_col_letters: dict[int, dict[str, str]] = {
        year: {c.label: column_letter(column_map[c]) for c in cols}
        for year, cols in columns_by_year.items()
    }

    def _record_for(col: PeriodColumn) -> FilingRecord | None:
        return by_year_period.get((col.fiscal_year, col.label))

    sections: list[tuple[str, list[MetricRow]]] = []
    # `row_number` tracks the last row already used. The sheet occupies
    # rows 1..SHEET_TITLE_ROWS (title, company ID, blank spacer) before
    # the first section's title row.
    row_number = SHEET_TITLE_ROWS
    for section_title, row_specs in SHEET_SECTIONS:
        # Section title row = row_number + 1; then SECTION_HEADER_ROWS
        # (title + period-label + fiscal-year) rows total before the
        # first metric row.
        row_number += SECTION_HEADER_ROWS + 1
        metric_rows: list[MetricRow] = []
        for label, field_name in row_specs:
            if field_name.startswith("__"):
                metric_rows.append(
                    MetricRow(
                        label=label,
                        field_name=field_name,
                        kind=DURATION,
                        row_number=row_number,
                        cells={col: CellPlan() for col in columns},
                    )
                )
                row_number += 1
                continue
            if field_name == BEGINNING_CASH_FIELD_NAME:
                # Beginning Cash sits immediately before Ending Cash in the
                # user-approved row layout. Its formulas reference Ending
                # Cash's next row in the prior period column.
                ending_cash_row_number = row_number + 1
                beginning_cash_cells: dict[PeriodColumn, CellPlan] = {}
                for col in columns:
                    prior_cell = _prior_column_cell(
                        col, ending_cash_row_number, columns_by_year, column_map
                    )
                beginning_cash_cells[col] = CellPlan(
                    formula=_beginning_cash_formula(prior_cell)
                    )
                metric_rows.append(
                    MetricRow(
                        label=label,
                        field_name=field_name,
                        kind=DURATION,
                        row_number=row_number,
                        cells=beginning_cash_cells,
                    )
                )
                row_number += 1
                continue
            kind = _field_kind(field_name)
            cells: dict[PeriodColumn, CellPlan] = {}
            for col in columns:
                cells[col] = _cell_plan_for(
                    field_name,
                    kind,
                    col,
                    year_col_letters,
                    row_number,
                    _record_for,
                    by_year_period,
                )
            metric_rows.append(
                MetricRow(
                    label=label,
                    field_name=field_name,
                    kind=kind,
                    row_number=row_number,
                    cells=cells,
                )
            )
            row_number += 1

        if section_title == CASH_FLOW_SECTION_TITLE and not any(
            row.field_name == BEGINNING_CASH_FIELD_NAME for row in metric_rows
        ):
            ending_cash_row_number = metric_rows[-1].row_number
            beginning_cash_cells: dict[PeriodColumn, CellPlan] = {}
            for col in columns:
                prior_cell = _prior_column_cell(
                    col, ending_cash_row_number, columns_by_year, column_map
                )
                if prior_cell is not None:
                    beginning_cash_cells[col] = CellPlan(
                        formula=_beginning_cash_formula(prior_cell)
                    )
                else:
                    prior_fy = all_by_year_period.get((col.fiscal_year - 1, "FY"))
                    beginning_cash_cells[col] = CellPlan(
                        value=prior_fy.ending_cash if prior_fy is not None else None
                    )
            metric_rows.append(
                MetricRow(
                    label=BEGINNING_CASH_LABEL,
                    field_name=BEGINNING_CASH_FIELD_NAME,
                    kind=DURATION,
                    row_number=row_number,
                    cells=beginning_cash_cells,
                )
            )
            row_number += 1

        sections.append((section_title, metric_rows))
        row_number += 1  # blank spacer row between sections

    return PivotResult(columns=columns, column_map=column_map, sections=sections)


def _cell_plan_for(
    field_name: str,
    kind: str,
    col: PeriodColumn,
    year_col_letters: dict[int, dict[str, str]],
    row_number: int,
    record_for,
    records_by_year_period: dict[tuple[int, str], FilingRecord],
) -> CellPlan:
    """Compute the CellPlan for one (metric, period column) cell.

    - Instant metrics, non-Q4 columns (FY, Q1, Q2, Q3): the real value
      directly from the matching record (if any).
    - Instant metrics, Q4 column: a Q4-mirror formula string referencing
      that same fiscal year's FY column cell (e.g. `=L71`) IF this fiscal
      year's column set includes an FY column -- a balance-sheet snapshot
      dated at fiscal-year-end IS the Q4-end snapshot, so Q4 simply
      mirrors FY rather than deriving via a plug/subtraction formula (that
      derivation only applies to duration/flow metrics, since SEC filers
      never report a standalone "Q4" instant fact). Otherwise the real
      direct Q4 value if a Q4 record happens to exist, else blank.
    - Duration metrics, non-Q4 columns (FY, Q1, Q2, Q3): the real value
      directly from the matching record (if any).
    - Duration metrics, Q4 column: a real Q4-plug formula string (with
      real column letters and this row's real row number) IF this field
      isn't in NO_Q4_PLUG_FIELDS (the EPS exception) AND this fiscal
      year's column set AND this field has a non-blank value in FY + Q1 +
      Q2 + Q3 (all four needed to plug); otherwise the real direct Q4
      value if a Q4 record happens to exist (it won't in practice for SEC
      filers), else blank.
    - Blank-FY-column case (newest window year, no FY filing yet): no
      record exists for the FY column, so it naturally resolves to blank
      for both instant and duration metrics -- no special case needed
      beyond "no matching record -> blank".

    Milestone 14.3 note: Q2/Q3 columns for a concept that hit the
    YTD-duration fallback (Milestone 14.1/14.2) need NO special handling
    here at all -- `facts_parser.derive_ytd_fallback_values` (wired into
    `fetch_and_parse_company_facts`, upstream of `build_pivot`) already
    overwrote that concept's field on the record with the correct derived
    plain value before this function ever runs, so the existing
    `getattr(record, field_name)` read above simply returns the real
    number instead of `None`. The Q4 eligibility check below additionally
    verifies that each of FY/Q1/Q2/Q3 has a real value for this field;
    this prevents Excel's `SUM()` from silently treating a missing Q2/Q3
    as zero and generating a wrong Q4 plug.
    """
    record = record_for(col)

    if col.quarter != "Q4":
        # FY/Q1/Q2/Q3 columns, for both instant and duration metrics: real
        # value directly, blank if no record.
        value = getattr(record, field_name) if record is not None else None
        return CellPlan(value=value)

    if kind == INSTANT:
        # Instant metric, Q4 column: mirror the FY column for this fiscal
        # year (same date, same value -- see docstring above).
        letters = year_col_letters.get(col.fiscal_year, {})
        if "FY" in letters:
            formula = _q4_mirror_formula(letters["FY"], row_number)
            return CellPlan(formula=formula)
        # No FY column present this year (shouldn't occur given how
        # _period_columns_for_window builds columns, since a Q4 column
        # only ever appears alongside its year's FY column, but handled
        # defensively): fall back to a direct value if one exists, else
        # blank.
        value = getattr(record, field_name) if record is not None else None
        return CellPlan(value=value)

    # Duration metric, Q4 column.
    if field_name in NO_Q4_PLUG_FIELDS:
        # EPS exception: no plug, ever. Only a direct reported Q4 value
        # (which won't exist in practice) would populate this cell.
        value = getattr(record, field_name) if record is not None else None
        return CellPlan(value=value)

    letters = year_col_letters.get(col.fiscal_year, {})
    required_periods = ("FY", "Q1", "Q2", "Q3")
    has_required_values = all(
        (
            required_record := records_by_year_period.get((col.fiscal_year, period))
        ) is not None
        and getattr(required_record, field_name) is not None
        for period in required_periods
    )
    if all(period in letters for period in required_periods) and has_required_values:
        formula = _q4_formula(letters["FY"], letters["Q1"], letters["Q3"], row_number)
        return CellPlan(formula=formula)

    # Not enough columns present this year to plug (e.g. a 10-K-only year
    # with no quarterly breakdown at all -- shouldn't occur given how
    # _period_columns_for_window builds columns, but handled defensively):
    # fall back to a direct value if one exists, else blank.
    value = getattr(record, field_name) if record is not None else None
    return CellPlan(value=value)


# -- Milestone 12.3a: styling scaffolding (parallel-safe with 12.2) ----------
#
# Everything below needs `openpyxl` importable (call `_require_openpyxl()`
# first), but does NOT need Milestone 12.2's real pivot output -- these
# functions style a given worksheet/cell directly and are unit-tested
# against a synthetic/fixture sheet shape. Milestone 12.3b applies these
# functions to the real, finalized pivot from `build_pivot`.

FONT_NAME = "Aptos Narrow"

FONT_SIZE_DATA = 12
FONT_SIZE_SHEET_TITLE = 14
FONT_SIZE_SECTION_HEADER = 12
FONT_SIZE_PERIOD_HEADER = 10

# Light gray solid fill for the FY period-header cell only (matches the
# template's verified effective color -- theme 0/white with tint
# -0.0499893185216834 resolves to RGB F2F2F2; using an explicit literal
# fill here rather than depending on a copied theme palette, since we are
# not copying the template's theme, only its visual result).
FY_HEADER_FILL_RGB = "FFF2F2F2"

# Number formats, verified verbatim against the real template file.
DOLLAR_NUMBER_FORMAT = r"#,##0;[Red]\(#,##0\);\-"
EPS_NUMBER_FORMAT = r"0.00;[Red]\(0.00\);\-"
SHARE_COUNT_NUMBER_FORMAT = "#,##0"
DATE_NUMBER_FORMAT = "mm-dd-yy"

# Fields that get the EPS number format instead of the blanket dollar
# format (per-share dollar figures, not whole-dollar amounts).
EPS_FIELDS = {"eps_basic", "eps_diluted"}

# Fields that get the share-count number format (plain integer count, no
# dollar sign, no red-negative-parens convention) instead of the blanket
# dollar format.
SHARE_COUNT_FIELDS = {
    "shares_outstanding",
    "dei_common_stock_shares_outstanding",
    "rsu_count",
    "diluted_shares",
}

# Column widths (Excel's "characters" width unit), matching the
# template's own observed A/B/data-column widths.
COLUMN_WIDTH_SPACER = 7.5  # column A
COLUMN_WIDTH_LABEL = 36  # column B
# Large-dollar SEC values need enough room to display rather than silently
# showing clipped leading digits in the analyst view.
COLUMN_WIDTH_DATA = 16  # every period data column, C onward

SHEET_TITLE_TEXT = "Historical Financial Data"


def number_format_for_field(field_name: str) -> str:
    """Return the appropriate number format string for a given
    FilingRecord field name: EPS format for EPS fields, share-count
    format for share-count fields, blanket dollar format otherwise."""
    if field_name in EPS_FIELDS:
        return EPS_NUMBER_FORMAT
    if field_name in SHARE_COUNT_FIELDS:
        return SHARE_COUNT_NUMBER_FORMAT
    return DOLLAR_NUMBER_FORMAT


def _label_font(openpyxl, bold: bool = False):
    return openpyxl.styles.Font(name=FONT_NAME, size=FONT_SIZE_DATA, bold=bold, color="FF000000")


def _sheet_title_font(openpyxl):
    return openpyxl.styles.Font(
        name=FONT_NAME, size=FONT_SIZE_SHEET_TITLE, bold=True, color="FF000000"
    )


def _section_header_font(openpyxl):
    return openpyxl.styles.Font(
        name=FONT_NAME, size=FONT_SIZE_SECTION_HEADER, bold=True, color="FF000000"
    )


def _period_header_font(openpyxl):
    return openpyxl.styles.Font(
        name=FONT_NAME, size=FONT_SIZE_PERIOD_HEADER, bold=True, color="FF000000"
    )


def _data_font(openpyxl):
    return openpyxl.styles.Font(name=FONT_NAME, size=FONT_SIZE_DATA, bold=False, color="FF000000")


def _fy_header_fill(openpyxl):
    return openpyxl.styles.PatternFill(
        fill_type="solid", fgColor=FY_HEADER_FILL_RGB, bgColor=FY_HEADER_FILL_RGB
    )


def _thin_bottom_border(openpyxl):
    return openpyxl.styles.Border(bottom=openpyxl.styles.Side(style="thin"))


def style_sheet_title(openpyxl, ws, row: int, col: int) -> None:
    """Style the sheet title cell ("Historical Financial Data", 14pt
    bold), matching the template's own sheet-level title treatment (not
    the template's separate 26pt cover-banner style, which belongs to its
    Cover sheet -- out of scope here since this feature has no cover
    sheet)."""
    cell = ws.cell(row=row, column=col, value=SHEET_TITLE_TEXT)
    cell.font = _sheet_title_font(openpyxl)


def style_company_identification(openpyxl, ws, row: int, text: str) -> None:
    """Style the company/ticker/CIK identification row (this feature
    produces a single-sheet workbook with no Cover sheet, so this row is
    what makes an opened .xlsx self-identifying without a filename
    lookup)."""
    cell = ws.cell(row=row, column=LABEL_COLUMN_INDEX, value=text)
    cell.font = _label_font(openpyxl, bold=True)


def style_section_header(openpyxl, ws, row: int, title: str) -> None:
    """Style one section header cell (e.g. "Income Statement"): bold
    12pt in column B, own row, no fill -- matching the template exactly."""
    cell = ws.cell(row=row, column=LABEL_COLUMN_INDEX, value=title)
    cell.font = _section_header_font(openpyxl)


def style_period_header_row(
    openpyxl,
    ws,
    label_row: int,
    columns: list[PeriodColumn],
    column_map: dict[PeriodColumn, int],
) -> None:
    """Style and write the period-header label row (FY/Q1/Q2/Q3/Q4
    labels): bold 10pt, centered; the FY column header cell alone gets
    the light gray solid fill; quarter header cells get no fill. Data
    cells (written separately, in 12.3b) never get this fill -- it is a
    header-label-only style, matching the template's actual convention
    exactly.
    """
    font = _period_header_font(openpyxl)
    fill = _fy_header_fill(openpyxl)
    center = openpyxl.styles.Alignment(horizontal="center")
    for col in columns:
        col_idx = column_map[col]
        cell = ws.cell(row=label_row, column=col_idx, value=col.label)
        cell.font = font
        cell.alignment = center
        if col.is_fy:
            cell.fill = fill


def style_fiscal_year_row(
    openpyxl,
    ws,
    year_row: int,
    columns: list[PeriodColumn],
    column_map: dict[PeriodColumn, int],
) -> None:
    """Style and write the fiscal-year sub-row beneath the period-header
    label row (e.g. 2023, 2023, 2023, 2023, 2023 under Q1/Q2/Q3/Q4/FY),
    bold 10pt centered, no fill (fill belongs only to the label row's FY
    cell, per style_period_header_row)."""
    font = _period_header_font(openpyxl)
    center = openpyxl.styles.Alignment(horizontal="center")
    for col in columns:
        col_idx = column_map[col]
        cell = ws.cell(row=year_row, column=col_idx, value=col.fiscal_year)
        cell.font = font
        cell.alignment = center


def style_metric_row_label(openpyxl, ws, row: int, label: str, bold: bool = False) -> None:
    """Style one metric row's label cell in column B: 12pt, not bold, no
    fill -- matching the template's plain data-label convention."""
    cell = ws.cell(row=row, column=LABEL_COLUMN_INDEX, value=label)
    cell.font = _label_font(openpyxl, bold=bold)


def style_data_cell(openpyxl, ws, row: int, col_idx: int, field_name: str, cell_plan: CellPlan) -> None:
    """Write and style one data cell: real value or formula, 12pt plain
    font (no color-coding -- matches the template's own plain-black-text
    convention, an explicit exception to this project's usual blue-input/
    black-formula guidance), and the number format appropriate to this
    field (dollar/EPS/share-count)."""
    if cell_plan.formula is not None:
        cell = ws.cell(row=row, column=col_idx, value=cell_plan.formula)
    elif cell_plan.value is not None:
        cell = ws.cell(row=row, column=col_idx, value=cell_plan.value)
    else:
        cell = ws.cell(row=row, column=col_idx)  # blank
    cell.font = _data_font(openpyxl)
    cell.number_format = number_format_for_field(field_name)


def style_date_cell(openpyxl, ws, row: int, col_idx: int, value) -> None:
    """Style a date sub-row cell: mm-dd-yy number format, thin bottom
    border, centered, 10pt (matching the template's period-header block
    convention -- used for an optional per-column period-end-date row,
    same convention as the template's own Dates section)."""
    cell = ws.cell(row=row, column=col_idx, value=value)
    cell.number_format = DATE_NUMBER_FORMAT
    cell.border = _thin_bottom_border(openpyxl)
    cell.font = _period_header_font(openpyxl)
    cell.alignment = openpyxl.styles.Alignment(horizontal="center")


def apply_sheet_level_settings(ws) -> None:
    """Apply sheet-level settings that aren't per-cell: column widths, no
    merged cells (never call ws.merge_cells -- simply don't), gridlines
    visible, no freeze panes. Matches the template's own sheet-level
    conventions exactly, per PLAN.md's 12.3a task list."""
    ws.column_dimensions["A"].width = COLUMN_WIDTH_SPACER
    ws.column_dimensions["B"].width = COLUMN_WIDTH_LABEL
    ws.sheet_view.showGridLines = True
    ws.freeze_panes = None


def set_data_column_widths(ws, num_data_columns: int) -> None:
    """Set a consistent width for every period data column (C onward),
    rather than varying per section, per PLAN.md's 12.3a task list."""
    for i in range(num_data_columns):
        col_idx = FIRST_DATA_COLUMN_INDEX + i
        ws.column_dimensions[column_letter(col_idx)].width = COLUMN_WIDTH_DATA


# -- Milestone 12.3b: render real pivoted data into the styled sheet --------
#
# Ties Milestone 12.2's pure pivot (`build_pivot`) together with 12.3a's
# styling functions into one real worksheet, using the EXACT same
# sheet-title-rows/section-header-rows layout constants `build_pivot`
# already used to compute its Q4-plug formulas' row numbers -- so the
# rows this renderer writes to are guaranteed to be the same rows the
# pivot's formula strings reference. Row numbers are never independently
# recomputed here.

SHEET_TITLE_ROW = 1
COMPANY_IDENTIFICATION_ROW = 2
# Row 3 is a deliberate blank spacer row (see SHEET_TITLE_ROWS = 3 above).

FIRST_SECTION_TITLE_ROW = SHEET_TITLE_ROWS + 1  # 4: first row after the spacer


def render_workbook(openpyxl, pivot: PivotResult, company_label: str):
    """Render a full styled worksheet from a `PivotResult`. Returns the
    `Workbook` (single sheet, "Historical Financial Data"). Pure
    in-memory rendering -- callers write it to disk."""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Historical Financial Data"

    apply_sheet_level_settings(ws)
    set_data_column_widths(ws, num_data_columns=len(pivot.columns))

    style_sheet_title(openpyxl, ws, row=SHEET_TITLE_ROW, col=LABEL_COLUMN_INDEX)
    style_company_identification(openpyxl, ws, row=COMPANY_IDENTIFICATION_ROW, text=company_label)

    for section_title, metric_rows in pivot.sections:
        # Derive this section's header-block row numbers directly from
        # its own first metric row's real row_number (already computed,
        # guaranteed correct/consistent with every Q4 formula that
        # references it) rather than independently re-deriving/
        # accumulating a running row counter here -- two independent
        # accumulators for the same layout is exactly the kind of
        # off-by-one risk that already produced a real bug during this
        # milestone's own manual verification (a section-title row
        # collided with the previous section's last metric row). Deriving
        # from `metric_rows[0].row_number` means there is only ever one
        # source of truth for row placement.
        first_metric_row_number = metric_rows[0].row_number
        section_title_row = first_metric_row_number - SECTION_HEADER_ROWS
        period_label_row = section_title_row + 1
        fiscal_year_row = section_title_row + 2

        style_section_header(openpyxl, ws, row=section_title_row, title=section_title)
        style_period_header_row(openpyxl, ws, label_row=period_label_row, columns=pivot.columns, column_map=pivot.column_map)
        style_fiscal_year_row(openpyxl, ws, year_row=fiscal_year_row, columns=pivot.columns, column_map=pivot.column_map)

        for metric_row in metric_rows:
            style_metric_row_label(
                openpyxl,
                ws,
                row=metric_row.row_number,
                label=metric_row.label,
                bold=metric_row.field_name.startswith("__header_"),
            )
            for col in pivot.columns:
                col_idx = pivot.column_map[col]
                cell_plan = metric_row.cells[col]
                style_data_cell(
                    openpyxl,
                    ws,
                    row=metric_row.row_number,
                    col_idx=col_idx,
                    field_name=metric_row.field_name,
                    cell_plan=cell_plan,
                )

    return wb


def write_company_xlsx(
    records: list[FilingRecord],
    out_dir,
    ticker_or_cik: str,
    company_label: str | None = None,
    num_years: int = DEFAULT_TRAILING_WINDOW_YEARS,
):
    """Build the pivot and write one company's styled `.xlsx` workbook to
    `out_dir`, using the exact same `safe_filename_stem` helper
    `csv_writer.write_company_csv` uses, so the CSV and xlsx stems always
    match. Returns the path to the written file.

    Raises `XlsxWriterUnavailableError` if `openpyxl` isn't importable
    (callers must catch this and continue -- CSV output is unaffected).
    """
    from pathlib import Path

    openpyxl = _require_openpyxl()

    pivot = build_pivot(records, num_years=num_years)

    if company_label is None:
        first = records[0] if records else None
        if first is not None:
            company_label = f"{first.company} ({first.ticker}, CIK {first.cik})"
        else:
            company_label = ticker_or_cik

    wb = render_workbook(openpyxl, pivot, company_label)

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    filename = f"{safe_filename_stem(ticker_or_cik)}.xlsx"
    out_path = out_dir / filename
    wb.save(out_path)
    return out_path
