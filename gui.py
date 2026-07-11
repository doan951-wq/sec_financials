"""Simple customtkinter GUI viewer for CSVs produced by the `sec_financials`
CLI (see `csv_writer.py` for the column layout this reads).

Usage:
    python3 -m sec_financials.gui

Open one or more CSVs via the "Open CSV(s)" button, pick a company from the
global dropdown (populated from the loaded files' Company/Ticker), and
browse that company's filings across four grouped tabs (Milestone 9):
Income Statement, Balance Sheet, Cash Flow, Per-Share & Other. Each tab
repeats the shared identity columns (Form, Fiscal Year, Fiscal Period,
Period End Date, Filed Date) plus that group's metrics, sorted by Period
End Date ascending. The Company dropdown is global -- switching tabs shows
a different metric group for the same currently-selected company, not a
different company.

This module reads CSVs purely by header string (via `csv.DictReader`), not
via `FilingRecord`'s Python field names -- no dependency on
`facts_parser.py` at all. A wider CSV (Milestone 8's expanded schema)
loads without error regardless of which columns this module currently
wires into tabs (extra/missing columns beyond REQUIRED_COLUMNS are simply
ignored/blank), matching the independence PLAN.md's Milestone 9.1/9.2
split relies on.

Separate from and unrelated to `10k_viewer.py` (a different, manual,
single-file-at-a-time GUI for parsing raw 10-K HTML). Visual style
(colors, dark theme, Treeview styling helper) is copied from
`10k_viewer.py` for consistency, not imported/shared, per design decision
recorded in `sec_financials/README.md` and `PLAN.md`.
"""

from __future__ import annotations

import csv
import queue
import threading
from pathlib import Path

import customtkinter as ctk
from tkinter import filedialog, messagebox
from tkinter import ttk
import tkinter as tk

from sec_financials.cli import refresh_company
from sec_financials.company_lookup import CompanyLookupError
from sec_financials.edgar_client import EdgarClientError
from sec_financials.facts_parser import FactsParserError

ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")

# Colors (copied from 10k_viewer.py for visual consistency)
C_BG        = "#1a1a2e"
C_SURFACE   = "#16213e"
C_CARD      = "#0f3460"
C_ACCENT    = "#4f9cf9"
C_ACCENT2   = "#3a7bd5"
C_GREEN     = "#4ade80"
C_RED       = "#f87171"
C_TEXT      = "#e2e8f0"
C_SUBTEXT   = "#94a3b8"
C_BORDER    = "#334155"
C_ROW_A     = "#1e2a3a"
C_ROW_B     = "#162030"

# Shared identity columns, repeated as the leading columns on every tab
# (Milestone 9.1 design decision).
IDENTITY_COLUMNS = [
    "Form",
    "Fiscal Year",
    "Fiscal Period",
    "Period End Date",
    "Filed Date",
]

# Columns required to be present in any CSV this tool loads. Only the
# identity columns plus the always-present v1 trio are strictly required
# -- everything else is optional per-concept data that may be blank or,
# for an older/narrower CSV, entirely absent as a column (handled
# gracefully by DictReader/.get(), not treated as a load error).
REQUIRED_COLUMNS = [
    "Company",
    "Ticker",
    "CIK",
    *IDENTITY_COLUMNS,
    "Revenue",
    "Cost of Sales",
    "Gross Profit",
]

# -- Milestone 9.1/9.2: tab grouping ------------------------------------------
#
# Each tab is (tab title, list of metric column headers shown after the
# shared IDENTITY_COLUMNS). Column headers match csv_writer.CSV_HEADER
# exactly (Milestone 9.2: wired to the real, finished Milestone 8 header
# strings -- see PLAN.md's note that gui.py depends on Milestone 8's
# header list, not FilingRecord's field names).
INCOME_STATEMENT_COLUMNS = [
    "Revenue",
    "Cost of Sales",
    "Gross Profit",
    "Gross Margin %",  # computed here, not read from the CSV (as in v1)
    "Operating Expenses",
    "Sales & Marketing",
    "General & Administrative",
    "Other Operating Expenses/Income",
    "Operating Income",
    "Interest Income",
    "Interest Expense",
    "Other Income / Expense, Net",
    "Net Income",
    "Research and Development",
    "Income Tax Expense",
    "Pre-Tax Income",
    "Diluted Shares",
]

BALANCE_SHEET_COLUMNS = [
    "Cash",
    "Marketable Securities Current",
    "Accounts Receivable",
    "Inventory",
    "Other Current Assets",
    "Property & Equipment Net",
    "Operating Lease ROU Asset",
    "Goodwill",
    "Other Assets Noncurrent",
    "Total Assets",
    "Accounts Payable",
    "Accrued Expenses and Other",
    "Unearned Revenue",
    "Long Term Debt Current",
    "Current Finance Lease Liabilities",
    "Current Operating Lease Liabilities",
    "Long Term Debt",
    "Long Term Debt Noncurrent",
    "Short Term Debt",
    "Operating Lease Liabilities",
    "Finance Lease Liabilities",
    "Long-Term Finance Lease Liabilities",
    "Long-Term Operating Lease Liabilities",
    "Other Long-Term Liabilities",
    "Total Liabilities",
    "Stockholders Equity",
    "Total Liabilities & Stockholders' Equity",
]

CASH_FLOW_COLUMNS = [
    "Operating Cash Flow",
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
    "Capital Expenditures",
    "Purchases of Marketable Securities",
    "Sales / Maturities of Marketable Securities",
    "Acquisitions",
    "Net Cash Used in Investing Activities",
    "Proceeds from Short-Term Debt and Other",
    "Repayments of Short-Term Debt and Other",
    "Proceeds from Long-Term Debt",
    "Repayments of Long-Term Debt",
    "Finance Lease Principal Payments",
    "Financing Obligation Principal Payments",
    "Net Cash Used in Financing Activities",
    "Other Financing Activities",
    "Free Cash Flow",
    "Net Change in Cash",
    "Ending Cash",
    # "Beginning Cash" is NOT a GUI column -- it has no CSV column/
    # FilingRecord field at all (Milestone 13.3, Capability 2: it is an
    # xlsx-render-time-only cross-column formula, never a per-filing
    # fact/CSV value this CSV-driven GUI could read). Do not add one.
]

PER_SHARE_AND_OTHER_COLUMNS = [
    "Shares Outstanding",
    "EPS Basic",
    "EPS Diluted",
    "Common Stock Outstanding Shares (dei)",
    "RSU Count",
]

TABS: list[tuple[str, list[str]]] = [
    ("Income Statement", INCOME_STATEMENT_COLUMNS),
    ("Balance Sheet", BALANCE_SHEET_COLUMNS),
    ("Cash Flow", CASH_FLOW_COLUMNS),
    ("Per-Share & Other", PER_SHARE_AND_OTHER_COLUMNS),
]

# Column display widths, shared across tabs (identity columns) plus each
# tab's own metric columns. Falls back to a sensible default for any
# column not listed here.
COLUMN_WIDTHS = {
    "Form": 70,
    "Fiscal Year": 90,
    "Fiscal Period": 100,
    "Period End Date": 130,
    "Filed Date": 130,
    "Revenue": 140,
    "Cost of Sales": 140,
    "Gross Profit": 130,
    "Gross Margin %": 110,
    "Operating Income": 140,
    "Net Income": 140,
    "Research and Development": 150,
    "Income Tax Expense": 140,
    "Pre-Tax Income": 140,
    "Cash": 130,
    "Total Assets": 140,
    "Total Liabilities": 140,
    "Stockholders Equity": 150,
    "Long Term Debt": 140,
    "Long Term Debt Current": 160,
    "Long Term Debt Noncurrent": 170,
    "Short Term Debt": 140,
    "Operating Lease Liabilities": 170,
    "Finance Lease Liabilities": 160,
    "Marketable Securities Current": 180,
    "Operating Cash Flow": 150,
    "Capital Expenditures": 150,
    "Free Cash Flow": 130,
    "Shares Outstanding": 140,
    "EPS Basic": 100,
    "EPS Diluted": 100,
    "Common Stock Outstanding Shares (dei)": 200,
    "RSU Count": 110,
    # -- Milestone 13.6: widths for the new Milestone 13.1/13.2 columns.
    "Operating Expenses": 150,
    "Sales & Marketing": 140,
    "General & Administrative": 170,
    "Other Operating Expenses/Income": 190,
    "Interest Income": 130,
    "Interest Expense": 140,
    "Other Income / Expense, Net": 170,
    "Diluted Shares": 140,
    "Accounts Receivable": 150,
    "Inventory": 120,
    "Other Current Assets": 160,
    "Property & Equipment Net": 170,
    "Operating Lease ROU Asset": 170,
    "Goodwill": 120,
    "Other Assets Noncurrent": 160,
    "Accounts Payable": 140,
    "Accrued Expenses and Other": 180,
    "Unearned Revenue": 140,
    "Current Finance Lease Liabilities": 190,
    "Current Operating Lease Liabilities": 200,
    "Long-Term Finance Lease Liabilities": 190,
    "Long-Term Operating Lease Liabilities": 200,
    "Other Long-Term Liabilities": 170,
    "Total Liabilities & Stockholders' Equity": 220,
    "Ending Cash": 130,
    "Depreciation and Amortization": 180,
    "Stock-Based Compensation": 170,
    "Deferred Taxes": 130,
    "Other Non-Cash Items": 160,
    "Unearned Revenue CF Change": 180,
    "Inventory CF Change": 150,
    "Accounts Receivable CF Change": 190,
    "Other Assets CF Change": 160,
    "Accounts Payable CF Change": 180,
    "Accrued Expenses CF Change": 180,
    "Acquisitions": 130,
    "Net Cash Used in Investing Activities": 220,
    "Purchases of Marketable Securities": 200,
    "Sales / Maturities of Marketable Securities": 240,
    "Proceeds from Short-Term Debt and Other": 220,
    "Repayments of Short-Term Debt and Other": 220,
    "Proceeds from Long-Term Debt": 180,
    "Repayments of Long-Term Debt": 180,
    "Finance Lease Principal Payments": 200,
    "Financing Obligation Principal Payments": 220,
    "Net Cash Used in Financing Activities": 220,
    "Other Financing Activities": 170,
    "Net Change in Cash": 150,
}
DEFAULT_COLUMN_WIDTH = 120


# ── Data loading / parsing ────────────────────────────────────────────────────

class CsvFormatError(Exception):
    """Raised when a CSV is malformed or missing expected columns."""


def load_csv_rows(path: str) -> list[dict]:
    """Read one CSV file and return its rows as a list of dicts.

    Raises CsvFormatError if the file is malformed or missing any of the
    columns this tool requires (see REQUIRED_COLUMNS). Columns beyond
    REQUIRED_COLUMNS (i.e. the ~24 Milestone 8 concept columns) are
    optional -- an older/narrower CSV missing some of them still loads;
    those cells simply render blank via `.get(col, "")` in
    `build_tab_rows`.
    """
    with open(path, "r", newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        if reader.fieldnames is None:
            raise CsvFormatError(f"{Path(path).name}: file is empty or has no header row")
        missing = [c for c in REQUIRED_COLUMNS if c not in reader.fieldnames]
        if missing:
            raise CsvFormatError(
                f"{Path(path).name}: missing expected column(s): {', '.join(missing)}"
            )
        rows = list(reader)
    return rows


def load_multiple_csvs(paths: list[str]) -> list[dict]:
    """Load and concatenate rows from multiple CSV files.

    Raises CsvFormatError (with the offending filename in the message) on
    the first malformed file encountered.
    """
    all_rows: list[dict] = []
    for path in paths:
        all_rows.extend(load_csv_rows(path))
    return all_rows


def company_key(row: dict) -> str:
    """Dropdown key for a row: "Company (Ticker)", falling back gracefully
    if either field is blank."""
    company = (row.get("Company") or "").strip()
    ticker = (row.get("Ticker") or "").strip()
    if company and ticker:
        return f"{company} ({ticker})"
    return company or ticker or "(unknown company)"


def group_rows_by_company(rows: list[dict]) -> dict[str, list[dict]]:
    """Group loaded rows by company key, preserving first-seen order of
    companies."""
    grouped: dict[str, list[dict]] = {}
    for row in rows:
        key = company_key(row)
        grouped.setdefault(key, []).append(row)
    return grouped


def _parse_number(value: str):
    """Parse a CSV numeric cell; return None for blank/missing, float
    otherwise. Raises ValueError for non-blank, non-numeric content."""
    if value is None:
        return None
    value = value.strip()
    if value == "":
        return None
    return float(value)


def compute_gross_margin_pct(revenue: str, gross_profit: str) -> str:
    """Compute Gross Margin % = Gross Profit / Revenue as a display string,
    e.g. "43.30%". Returns "" if Revenue or Gross Profit is blank, or if
    Revenue is zero."""
    try:
        rev = _parse_number(revenue)
        gp = _parse_number(gross_profit)
    except ValueError:
        return ""
    if rev is None or gp is None or rev == 0:
        return ""
    return f"{gp / rev * 100:.2f}%"


def build_tab_rows(rows: list[dict], metric_columns: list[str]) -> list[list[str]]:
    """Build display rows for one tab: IDENTITY_COLUMNS followed by
    `metric_columns`, for one company's CSV rows, sorted by Period End
    Date ascending.

    Blank cells (a concept the company/CSV lacks) stay blank via
    `.get(col, "")` -- works whether the column is present-but-empty or
    entirely absent from the source CSV. "Gross Margin %" is computed
    here rather than read from the CSV, same as v1.
    """
    sorted_rows = sorted(rows, key=lambda r: r.get("Period End Date") or "")
    out = []
    for r in sorted_rows:
        values = [r.get(col, "") for col in IDENTITY_COLUMNS]
        for col in metric_columns:
            if col == "Gross Margin %":
                values.append(compute_gross_margin_pct(r.get("Revenue", ""), r.get("Gross Profit", "")))
            else:
                values.append(r.get(col, ""))
        out.append(values)
    return out


# Backwards-compatible alias: v1's name for the Income-Statement-equivalent
# table-building function. Kept for any external caller/test still using
# the old name; new code should use build_tab_rows directly.
def build_table_rows(rows: list[dict]) -> list[list[str]]:
    return build_tab_rows(rows, INCOME_STATEMENT_COLUMNS)


# ── Treeview style (copied from 10k_viewer.py) ────────────────────────────────

def style_tree(tree):
    style = ttk.Style()
    style.theme_use("default")
    style.configure("Custom.Treeview",
        background=C_ROW_A, fieldbackground=C_ROW_A,
        foreground=C_TEXT, rowheight=30,
        font=("Segoe UI", 11), borderwidth=0)
    style.configure("Custom.Treeview.Heading",
        background=C_CARD, foreground=C_ACCENT,
        font=("Segoe UI", 11, "bold"), relief="flat", padding=8)
    style.map("Custom.Treeview",
        background=[("selected", C_ACCENT2)],
        foreground=[("selected", "#ffffff")])
    style.map("Custom.Treeview.Heading", relief=[("active", "flat")])
    tree.configure(style="Custom.Treeview")


# ── Main App ──────────────────────────────────────────────────────────────────

class App(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title("SEC Financials Viewer")
        self.geometry("1300x700")
        self.configure(fg_color=C_BG)
        self._grouped: dict[str, list[dict]] = {}
        self._trees: dict[str, ttk.Treeview] = {}
        # Milestone 11.2: session-scoped output directory for GUI-triggered
        # fetches -- chosen once via filedialog.askdirectory() on first use,
        # cached here, reused for the rest of this run.
        self._fetch_out_dir: str | None = None
        self._fetch_result_queue: "queue.Queue" = queue.Queue()
        self._build_ui()

    def _build_ui(self):
        topbar = ctk.CTkFrame(self, fg_color=C_SURFACE, corner_radius=0, height=60)
        topbar.pack(fill="x", side="top")
        topbar.pack_propagate(False)

        ctk.CTkLabel(topbar, text="  SEC Financials Viewer",
                     font=ctk.CTkFont("Segoe UI", 18, "bold"),
                     text_color=C_ACCENT).pack(side="left", padx=16)

        self.file_label = ctk.CTkLabel(topbar, text="No files loaded",
                                        font=ctk.CTkFont("Segoe UI", 11),
                                        text_color=C_SUBTEXT)
        self.file_label.pack(side="left", padx=10)

        # Milestone 11.2: in-progress/status text for GUI-triggered
        # fetches (e.g. "Fetching AMZN...", "Done: wrote 68 rows for...").
        self.fetch_status_label = ctk.CTkLabel(topbar, text="",
                                                font=ctk.CTkFont("Segoe UI", 11),
                                                text_color=C_SUBTEXT)
        self.fetch_status_label.pack(side="left", padx=10)

        right = ctk.CTkFrame(topbar, fg_color="transparent")
        right.pack(side="right", padx=16)

        ctk.CTkButton(right, text="Open CSV(s)", command=self._open_files,
                      font=ctk.CTkFont("Segoe UI", 12, "bold"),
                      fg_color=C_ACCENT, hover_color=C_ACCENT2,
                      text_color="#ffffff", corner_radius=8,
                      width=140, height=34).pack(side="left")

        # Milestone 11.2: ticker/URL fetch-and-refresh input, next to (not
        # replacing) "Open CSV(s)".
        self.fetch_entry = ctk.CTkEntry(
            right, placeholder_text="Ticker or SEC.gov URL",
            font=ctk.CTkFont("Segoe UI", 12),
            fg_color=C_CARD, text_color=C_TEXT, border_color=C_BORDER,
            width=220, height=34)
        self.fetch_entry.pack(side="left", padx=(10, 6))
        self.fetch_entry.bind("<Return>", lambda event: self._on_fetch_submit())

        self.fetch_button = ctk.CTkButton(
            right, text="Fetch", command=self._on_fetch_submit,
            font=ctk.CTkFont("Segoe UI", 12, "bold"),
            fg_color=C_GREEN, hover_color=C_ACCENT2,
            text_color="#0f172a", corner_radius=8,
            width=90, height=34)
        self.fetch_button.pack(side="left")

        # Company selector bar -- global across all tabs (Milestone 9.1
        # design decision: tabs switch metric group, not company).
        selector = ctk.CTkFrame(self, fg_color=C_SURFACE, corner_radius=0, height=48)
        selector.pack(fill="x")
        selector.pack_propagate(False)

        ctk.CTkLabel(selector, text="Company:",
                     font=ctk.CTkFont("Segoe UI", 12),
                     text_color=C_SUBTEXT).pack(side="left", padx=(16, 8))

        self.company_var = ctk.StringVar(value="")
        self.company_menu = ctk.CTkOptionMenu(
            selector, variable=self.company_var, values=["(no companies loaded)"],
            command=self._on_company_selected,
            fg_color=C_CARD, button_color=C_CARD, button_hover_color=C_ACCENT2,
            text_color=C_TEXT, dropdown_fg_color=C_CARD,
            dropdown_text_color=C_TEXT, width=280)
        self.company_menu.pack(side="left", padx=(0, 16), pady=8)

        # Tab bar + per-tab table scaffolding.
        content = ctk.CTkFrame(self, fg_color=C_BG, corner_radius=0)
        content.pack(fill="both", expand=True)

        self.tabview = ctk.CTkTabview(
            content, fg_color=C_SURFACE, segmented_button_fg_color=C_CARD,
            segmented_button_selected_color=C_ACCENT,
            segmented_button_selected_hover_color=C_ACCENT2,
            segmented_button_unselected_color=C_CARD,
            text_color=C_TEXT,
        )
        self.tabview.pack(fill="both", expand=True, padx=12, pady=12)

        for tab_name, metric_columns in TABS:
            self.tabview.add(tab_name)
            self._build_tab_table(self.tabview.tab(tab_name), tab_name, metric_columns)

        self.tabview.set(TABS[0][0])

    def _build_tab_table(self, parent, tab_name: str, metric_columns: list[str]):
        """Build one tab's Treeview: shared identity columns + this tab's
        metric columns, empty until data is loaded."""
        frame = tk.Frame(parent, bg=C_BG)
        frame.pack(fill="both", expand=True, padx=4, pady=4)

        columns = IDENTITY_COLUMNS + metric_columns

        vsb = ttk.Scrollbar(frame, orient="vertical")
        hsb = ttk.Scrollbar(frame, orient="horizontal")
        tree = ttk.Treeview(frame, columns=columns, show="headings",
                             yscrollcommand=vsb.set, xscrollcommand=hsb.set)
        vsb.config(command=tree.yview)
        hsb.config(command=tree.xview)
        vsb.pack(side="right", fill="y")
        hsb.pack(side="bottom", fill="x")
        tree.pack(fill="both", expand=True)

        style_tree(tree)

        for col in columns:
            tree.heading(col, text=col)
            tree.column(col, width=COLUMN_WIDTHS.get(col, DEFAULT_COLUMN_WIDTH),
                        anchor="center", minwidth=60)

        tree.tag_configure("odd", background=C_ROW_A, foreground=C_TEXT)
        tree.tag_configure("even", background=C_ROW_B, foreground=C_TEXT)

        self._trees[tab_name] = tree

    # ── File open ─────────────────────────────────────────────────────────────
    def _open_files(self):
        paths = filedialog.askopenfilenames(
            title="Select sec_financials CSV file(s)",
            filetypes=[("CSV files", "*.csv"), ("All files", "*.*")])
        if not paths:
            return

        try:
            rows = load_multiple_csvs(list(paths))
        except CsvFormatError as e:
            messagebox.showerror("CSV Error", str(e))
            return
        except (OSError, csv.Error) as e:
            messagebox.showerror("CSV Error", f"Could not read file(s): {e}")
            return

        if not rows:
            messagebox.showinfo("No Data", "The selected file(s) contained no rows.")
            return

        self._grouped = group_rows_by_company(rows)
        names = [Path(p).name for p in paths]
        self.file_label.configure(text=", ".join(names))

        company_keys = list(self._grouped.keys())
        self.company_menu.configure(values=company_keys)
        first = company_keys[0]
        self.company_var.set(first)
        self._render_company(first)

    # ── Company selection ─────────────────────────────────────────────────────
    def _on_company_selected(self, choice: str):
        self._render_company(choice)

    def _render_company(self, company: str):
        rows = self._grouped.get(company, [])
        for tab_name, metric_columns in TABS:
            tree = self._trees[tab_name]
            for item in tree.get_children():
                tree.delete(item)
            try:
                tab_rows = build_tab_rows(rows, metric_columns)
            except Exception as e:
                messagebox.showerror(
                    "Data Error", f"Could not render {tab_name} data for {company}: {e}"
                )
                continue
            for i, values in enumerate(tab_rows):
                tag = "odd" if i % 2 == 0 else "even"
                tree.insert("", "end", values=values, tags=(tag,))

    # ── Fetch/refresh (Milestone 11.2) ────────────────────────────────────────
    def _set_fetch_controls_enabled(self, enabled: bool) -> None:
        state = "normal" if enabled else "disabled"
        self.fetch_entry.configure(state=state)
        self.fetch_button.configure(state=state)

    def _on_fetch_submit(self) -> None:
        # Disable input/button first, before anything else -- prevents a
        # fast double-Enter/double-click from queuing a second submit while
        # this one is still resolving the output directory or fetching.
        self._set_fetch_controls_enabled(False)

        ticker_or_url = self.fetch_entry.get().strip()
        if not ticker_or_url:
            self._set_fetch_controls_enabled(True)
            return

        # Resolve the output directory before starting the fetch thread:
        # session-scoped, ask once via askdirectory(), reuse afterward.
        out_dir = self._fetch_out_dir
        if out_dir is None:
            out_dir = filedialog.askdirectory(title="Choose output folder for fetched CSVs")
            if not out_dir:
                # User cancelled the picker: abort silently, same as
                # cancelling "Open CSV(s)" -- no fetch, no error dialog.
                self._set_fetch_controls_enabled(True)
                return
            self._fetch_out_dir = out_dir

        self.fetch_status_label.configure(text=f"Fetching {ticker_or_url}...")

        thread = threading.Thread(
            target=self._fetch_worker,
            args=(ticker_or_url, out_dir),
            daemon=True,
        )
        thread.start()
        self.after(100, self._poll_fetch_result)

    def _fetch_worker(self, ticker_or_url: str, out_dir: str) -> None:
        """Runs off the Tk main thread. Never touches Tk widgets directly --
        puts its result (success payload or exception) on the queue for the
        main thread to pick up via _poll_fetch_result."""
        try:
            result = refresh_company(ticker_or_url, out_dir)
        except (CompanyLookupError, FactsParserError, EdgarClientError, OSError) as exc:
            self._fetch_result_queue.put(("error", exc))
            return
        self._fetch_result_queue.put(("success", result))

    def _poll_fetch_result(self) -> None:
        try:
            kind, payload = self._fetch_result_queue.get_nowait()
        except queue.Empty:
            self.after(100, self._poll_fetch_result)
            return

        if kind == "error":
            self._set_fetch_controls_enabled(True)
            self.fetch_status_label.configure(text="")
            messagebox.showerror("Fetch Error", str(payload))
            return

        # kind == "success": payload is a cli.RefreshResult.
        result = payload
        try:
            rows = load_csv_rows(result.out_path)
        except CsvFormatError as e:
            self._set_fetch_controls_enabled(True)
            self.fetch_status_label.configure(text="")
            messagebox.showerror("CSV Error", str(e))
            return
        except (OSError, csv.Error) as e:
            self._set_fetch_controls_enabled(True)
            self.fetch_status_label.configure(text="")
            messagebox.showerror("CSV Error", f"Could not read file: {e}")
            return

        new_grouped = group_rows_by_company(rows)
        # Merge into self._grouped: overwrite in place if the company was
        # already loaded, add fresh if not -- either way the fetched
        # company's key ends up pointing at the just-written rows.
        self._grouped.update(new_grouped)

        company_keys = list(self._grouped.keys())
        self.company_menu.configure(values=company_keys)

        fetched_key = next(iter(new_grouped.keys()), None)
        if fetched_key is not None:
            self.company_var.set(fetched_key)
            self._render_company(fetched_key)

        existing_names = self.file_label.cget("text")
        out_name = Path(result.out_path).name
        if existing_names == "No files loaded":
            self.file_label.configure(text=out_name)
        elif out_name not in existing_names.split(", "):
            self.file_label.configure(text=f"{existing_names}, {out_name}")

        # Milestone 12: report the .xlsx outcome in the same status label,
        # never as a blocking error dialog -- the CSV already succeeded,
        # so a missing openpyxl is a one-line notice, not a failure.
        status_text = f"Done: wrote {result.row_count} row(s) for {result.company}"
        if result.xlsx_path is not None:
            status_text += f" (+ {Path(result.xlsx_path).name})"
        elif result.xlsx_skip_notice is not None:
            # Full notice (includes the exact one-time install command
            # from Milestone 12.1) shown directly in the status label --
            # not a popup, since the CSV already succeeded and this is
            # informational, not an error to dismiss.
            status_text = (
                f"Done: wrote {result.row_count} row(s) for {result.company}. "
                f"{result.xlsx_skip_notice}"
            )
        self.fetch_status_label.configure(text=status_text)
        self.fetch_entry.delete(0, "end")
        self._set_fetch_controls_enabled(True)


if __name__ == "__main__":
    App().mainloop()
