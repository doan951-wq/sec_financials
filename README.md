# sec_financials

## Desktop app (Windows and macOS)

The GUI can be packaged as a self-contained desktop application; end users do
not need Python installed. Build on each target operating system (PyInstaller
does not cross-compile):

```bash
python -m pip install -r requirements-build.txt
python scripts/build_app.py
```

On macOS, the result is `dist/SEC Financials.app`. On Windows, it is the
`dist\\SEC Financials` folder containing `SEC Financials.exe`; keep that folder
together when distributing it. Zip the whole app/folder before sharing.

The GitHub Actions workflow at `.github/workflows/build-desktop.yml` builds and
uploads zipped Windows and macOS artifacts. Run it manually from the Actions
tab, or push a tag such as `v1.0.0`.

The macOS app is currently unsigned, so first launch may require right-clicking
the app and choosing **Open**. Windows may show a SmartScreen warning for the
same reason. Production distribution should add platform code-signing
certificates to the workflow.

CLI tool that pulls ~74 financial concepts (Revenue, Cost of Sales, Gross
Profit, Operating Income, Net Income, balance-sheet items, cash-flow items,
per-share items, and more -- see "CSV columns" below) from SEC EDGAR's XBRL
Company Facts API for 10-K and 10-Q filings, for one or more companies, and
writes the results to a CSV file.

Status and task tracking: see `../PLAN.md` at the repo root.

## Usage

```
python -m sec_financials.cli --ticker AAPL --forms 10-K,10-Q --out-dir ./out
python -m sec_financials.cli --url "https://www.sec.gov/edgar/browse/?CIK=1652044&owner=exclude" --out-dir ./out
python -m sec_financials.cli --ticker AAPL --ticker MSFT --out-dir ./out
```

`--out-dir` is a required output directory (not a single file): each
resolved company gets its own CSV named after its ticker (or CIK, if no
ticker is known), e.g. `./out/AAPL.csv`. Run `python -m sec_financials.cli
--help` for the full flag reference and more examples.

## CSV columns

Each output CSV is flat/wide, one row per filing/period, 83 columns.
Any concept a given company/filing doesn't report under a recognized XBRL
tag is left blank rather than causing an error or a skipped row -- this is
common and expected (e.g. most companies have no Short Term Debt tag; a
retailer typically has no Research and Development tag; a bank often has
no Depreciation and Amortization or Marketable Securities Current tag).

Identity columns (unchanged from v1): `Company`, `Ticker`, `CIK`, `Form`,
`Fiscal Year`, `Fiscal Period`, `Period End Date`, `Filed Date`.

Income Statement: `Revenue`, `Cost of Sales`, `Gross Profit`, `Operating
Expenses`, `Sales & Marketing`, `General & Administrative`, `Other
Operating Expenses/Income`, `Operating Income`, `Interest Income`,
`Interest Expense`, `Other Income / Expense, Net`, `Net Income`,
`Research and Development`, `Income Tax Expense`, `Pre-Tax Income`,
`Diluted Shares` (weighted-average diluted shares -- distinct from
`Shares Outstanding` below, which is an instant/point-in-time balance).

Balance Sheet: `Cash`, `Marketable Securities Current`, `Accounts
Receivable`, `Inventory`, `Other Current Assets`, `Property & Equipment
Net`, `Operating Lease ROU Asset`, `Goodwill`, `Other Assets Noncurrent`,
`Total Assets`, `Accounts Payable`, `Accrued Expenses and Other`,
`Unearned Revenue`, `Long Term Debt Current`, `Current Finance Lease
Liabilities`, `Current Operating Lease Liabilities`, `Long Term Debt`,
`Long Term Debt Noncurrent`, `Short Term Debt`, `Operating Lease
Liabilities`, `Finance Lease Liabilities`, `Long-Term Finance Lease
Liabilities`, `Long-Term Operating Lease Liabilities`, `Other Long-Term
Liabilities`, `Total Liabilities`, `Stockholders Equity`, `Total
Liabilities & Stockholders' Equity`. (Total Current Assets, Total
Current Liabilities, Net Working Capital, and Change in NWC are
deliberately not included -- deferred to a future addition; see
`../PLAN.md` Milestone 13.)

Cash Flow: `Operating Cash Flow`, `Depreciation and Amortization`,
`Stock-Based Compensation`, `Deferred Taxes`, `Other Non-Cash Items`,
`Unearned Revenue CF Change`, `Inventory CF Change`, `Accounts Receivable
CF Change`, `Other Assets CF Change`, `Accounts Payable CF Change`,
`Accrued Expenses CF Change`, `Capital Expenditures`, `Purchases of
Marketable Securities`, `Sales / Maturities of Marketable Securities`
(sum of two XBRL tags when either/both are reported for a filing, falling
back to a third combined tag only when neither is reported -- see
"Multi-tag-sum concept" below), `Acquisitions`, `Net Cash Used in
Investing Activities`, `Proceeds from Short-Term Debt and Other`,
`Repayments of Short-Term Debt and Other`, `Proceeds from Long-Term
Debt`, `Repayments of Long-Term Debt`, `Finance Lease Principal
Payments`, `Financing Obligation Principal Payments`, `Net Cash Used in
Financing Activities`, `Other Financing Activities`, `Free Cash Flow`
(computed: Operating Cash Flow - Capital Expenditures), `Net Change in
Cash`, `Ending Cash` (includes restricted cash -- a distinct, separate
figure from `Cash` above, which excludes restricted cash). **"Beginning
Cash" is intentionally NOT a CSV column** -- it has no per-filing XBRL
fact at all; it only exists as a live cross-column formula in the
`.xlsx` output (see below).

Per-Share & Other: `Shares Outstanding`, `EPS Basic`, `EPS Diluted`,
`Common Stock Outstanding Shares (dei)`, `RSU Count`.

`Cost of Sales` and `Gross Profit` fall back to being computed from each
other (`Revenue - <the other>`) when only one has a direct XBRL tag for a
given filing; `Free Cash Flow` is always computed, never a direct tag.
See `../PLAN.md` (Milestone 7.3) for the exact fallback/circularity rule.

### Multi-tag-sum concept: Sales / Maturities of Marketable Securities

Unlike every other concept (first-tag-present-wins fallback), this one
concept sums two XBRL tags
(`ProceedsFromSaleOfAvailableForSaleSecuritiesDebt` +
`ProceedsFromMaturitiesPrepaymentsAndCallsOfAvailableForSaleSecurities`)
whenever either or both are reported for a filing (a missing individual
tag is treated as 0, not blank) -- common among banks/financial
institutions with available-for-sale securities portfolios. Only when
**neither** tag is reported does it fall back to a third, combined tag
(`ProceedsFromSaleMaturityAndCollectionsOfInvestments`); if all three are
absent, the cell is blank, same as any other concept. See `../PLAN.md`
Milestone 13.2 for the full design and live-data verification.

### Q2/Q3 YTD-duration fallback (Cash Flow concepts, and any other concept it applies to)

Some filers report certain duration (flow) concepts' Q2/Q3 10-Qs with
**only** a cumulative year-to-date (YTD) figure in their XBRL facts, not a
standalone ~90-day discrete-quarter figure -- confirmed live for GOOGL and
AAPL on `Operating Cash Flow`, `Depreciation and Amortization`,
`Stock-Based Compensation`, `Capital Expenditures`, and `Net Cash Used in
Investing/Financing Activities` (Amazon, by contrast, tags a genuine
discrete fact for every one of these every quarter -- this is a
per-filer, per-concept difference, not a universal rule). Without this
fallback, those cells would simply be blank for Q2/Q3 every year.

When no discrete ~90-day fact exists for a given (concept, filing), the
extraction step falls back to the filer's reported YTD figure instead,
then derives the actual standalone quarter:
`Q2 = Q2_YTD - Q1`, `Q3 = Q3_YTD - Q1 - Q2` (using Q2's own resolved
value, whether that Q2 itself came from a discrete fact or was derived
the same way). Q1 never needs this: a fiscal year's Q1 YTD figure and
Q1-alone are numerically identical.

This is a genuine per-(concept, filing) mechanism, not a blanket
"Cash Flow always uses YTD math" switch -- it is a **true no-op**
whenever a discrete fact already exists (confirmed for 100% of Amazon's
cash-flow filings and 100% of every Income Statement concept checked
across GOOGL/AAPL/AMZN), and it applies independently per concept even
within the same filing (e.g. GOOGL's Stock-Based Compensation has some
quarters with a discrete fact and some without, within the very same
company).

**Edge cases:**
- If Q1 is missing (e.g. a company's first-ever 10-Q is a Q2, or Q1
  simply isn't present in the data), Q2 cannot be derived and is left
  blank -- no guess, no error. Q3 needs both Q1 and Q2 known; if either
  is missing, Q3 is blank too.
- Applies per-fiscal-year only -- it never crosses a fiscal-year
  boundary, unlike Beginning Cash's adjacency rule below.
- Non-calendar fiscal years (e.g. Costco) need no special handling: the
  duration-band computation is based on each fact's own `start`/`end`
  dates, never an assumed calendar-quarter alignment.
- The multi-tag-sum concept (`Sales / Maturities of Marketable
  Securities` above) deliberately does **not** use this fallback: its two
  summand tags can independently be discrete or YTD-only for the same
  filing, and summing a discrete figure with a raw YTD figure would
  produce a number that is neither a valid quarter total nor a valid YTD
  total. That concept remains discrete-only extraction with its
  pre-existing missing-summand-treated-as-0 rule.

**Representation in both outputs**: unlike the Q4-plug and Beginning Cash
mechanisms below (which are live Excel formulas), a YTD-fallback-derived
Q2/Q3 value is a **plain, already-computed number** in both the CSV and
the `.xlsx` output -- computed once, upstream of both writers, so they
never disagree on the same figure. This is a deliberate exception to this
project's general "prefer formulas" convention for `.xlsx` output: the
tradeoff is that these specific Q2/Q3 cells will not recalculate if a
user later hand-edits Q1 in Excel (they are frozen at whatever value was
correct at generation time), unlike the Q4-plug/Beginning-Cash cells in
the same sheet, which do reference other cells live.

**Side effect: fixes a previously-silent Q4 bug.** The existing Q4-plug
formula (`=<FY>-SUM(<Q1>:<Q3>)`, see below) was already being generated
for these rows even when Q2/Q3 were blank -- and Excel's `SUM()` treats a
blank cell as `0`, so Q4 was silently computing as `FY - Q1` only
(overstating Q4 by the true Q2+Q3 total) rather than erroring or looking
obviously wrong. Once Q2/Q3 are populated by this mechanism, the exact
same pre-existing formula starts producing the arithmetically correct
Q4 value automatically -- no change to the Q4-plug formula itself was
needed.

## Excel (`.xlsx`) output

Alongside each company's CSV, this tool also writes a styled `.xlsx`
workbook (e.g. `./out/AAPL.xlsx` next to `./out/AAPL.csv`), visually
modeled on a user-provided analyst template ("Historical Financial Data"
sheet layout: period-header row with `FY`/`Q1`-`Q4` columns per fiscal
year, section headers, dollar/date number formats, Aptos Narrow font).
See PLAN.md's Milestone 12 for the full design.

**One-time setup required.** The `.xlsx` writer depends on the third
-party `openpyxl` package, which is not part of the Python standard
library and is not installed by default. Install it once with:

```
pip3 install --user --break-system-packages openpyxl
```

`--break-system-packages` is required on macOS's system Python (PEP
668's "externally managed environment" protection blocks a plain `pip
install` otherwise); `--user` keeps the install scoped to your user
account rather than modifying the system Python installation.

**If `openpyxl` isn't installed, `.xlsx` output is skipped, not an
error.** CSV output has zero new dependencies and is always written
successfully regardless of whether `openpyxl` is available. When
`.xlsx` writing is skipped, the CLI prints a one-line notice to stderr
(and the GUI shows the same notice in its fetch-status area) pointing at
the install command above -- run it once, then re-fetch to also get the
`.xlsx` file.

**Content and scope.** The `.xlsx` covers a **5-fiscal-year trailing
window** (the most recent fiscal year present in the company's data,
plus the 4 immediately prior) -- not full history like the CSV, which is
unaffected and always contains full history. Within that window, each
fiscal year (other than the oldest) gets `Q1`, `Q2`, `Q3` columns for
whichever quarters actually have a filed 10-Q, plus a `Q4` column
(present once Q1-Q3 are all filed for that year, since Q4 is always
derived via a plug formula and never itself directly filed by SEC
filers) and its own `FY` column. The oldest year in the window shows
only its `FY` column (matching the source template's own leftmost
-column convention); the newest/most-recent window year gets its `FY`
column too even if the 10-K hasn't been filed yet for it (left entirely
blank in that case, e.g. a company's most recent fiscal year showing
only a Q1 10-Q so far). Duration metrics (Revenue, Net Income, etc.) get
a Q4 column computed as an Excel formula (`FY - (Q1+Q2+Q3)`), matching
the template's own convention; instant metrics (Cash, Total Assets,
etc.) show the real reported value directly in every column, no formula.
EPS Basic/Diluted and Diluted Shares (weighted-average) are an explicit
exception: no Q4 formula is generated for them (their Q4 column is blank
unless a direct reported Q4 figure exists, which SEC filers never report
standalone for a weighted-average quantity) -- matching the template's
own literal-values-in-every-column convention for these fields. The
same four groupings as the GUI's tabs are used as sheet sections: Income
Statement, Balance Sheet (the largest section, 27 rows), Cash Flow,
Per-Share & Other -- only metrics we actually have a CSV column for are
included; the template's own segment-revenue build and Amazon-only
extension tags we have no generalizable data for are intentionally
omitted, never fabricated.

**Beginning Cash (cross-period formula).** The Cash Flow section ends
with `Ending Cash` (a real reported/plugged value, includes restricted
cash) immediately followed by `Beginning Cash` -- an xlsx-only row with
no CSV column and no underlying XBRL fact at all. Its value in every
period column is a live Excel formula referencing the *immediately prior
period column's* `Ending Cash` cell: `Q2`'s Beginning Cash references
`Q1`'s Ending Cash; `Q1`'s references the prior year's `FY` column's
Ending Cash. A fiscal year's own `FY` column is the one exception to
pure left-to-right adjacency: its Beginning Cash references the **prior
fiscal year's `FY` column**, not that same year's positionally-adjacent
`Q4` column (semantic "beginning of year" convention). The window's
oldest column has no formula (nothing precedes it) and is left blank.

**Q2/Q3 YTD-fallback derivation.** See "Q2/Q3 YTD-duration fallback"
under "CSV columns" above for the full mechanism and edge cases -- the
`.xlsx` output shows the exact same already-derived number the CSV does
for these cells, computed once upstream of both writers. Unlike Q4-plug/
Beginning Cash above, a YTD-fallback-derived Q2/Q3 cell is a **plain
value, not a live formula** (a deliberate, one-off exception to this
project's "prefer formulas" convention -- see `../PLAN.md` Milestone 14
for the full rationale): it will not recalculate if Q1 is later
hand-edited in the sheet. Once Q2/Q3 are populated this way, the
pre-existing Q4-plug formula for the same row automatically starts
computing the correct Q4 value (previously silently wrong when Q2/Q3
were blank, since Excel's `SUM()` treats a blank cell as `0`) -- no
change to the Q4-plug formula itself was needed.

## Configuration

SEC requires a descriptive `User-Agent` header (name + contact email) on every
request. Configure it via:

- `--user-agent "Your Name your.email@example.com"`, or
- the `SEC_EDGAR_USER_AGENT` environment variable

Default falls back to a generic contact if neither is set (see PLAN.md open
decisions).

## GUI viewer

A small customtkinter GUI (`sec_financials/gui.py`) is included for
browsing the CSVs this CLI produces:

```
python3 -m sec_financials.gui
```

Open one or more CSVs via "Open CSV(s)" (multi-select, filtered to
`*.csv`), pick a company from the global dropdown, and browse that
company's filings across four grouped tabs: **Income Statement**,
**Balance Sheet**, **Cash Flow**, **Per-Share & Other** (see "CSV
columns" above for each tab's exact metric list). Every tab repeats the
shared identity columns (Form, Fiscal Year, Fiscal Period, Period End
Date, Filed Date), sorted by Period End Date ascending. The Income
Statement tab additionally shows a computed Gross Margin % column (Gross
Profit / Revenue), same as v1. The Company dropdown is global across all
four tabs -- switching tabs shows a different metric group for the same
selected company, not a different company. Blank cells (a concept the
company/filing doesn't report) display as blank rather than crashing.

### Fetch/refresh a company directly from the GUI

Next to "Open CSV(s)" is a text field (placeholder "Ticker or SEC.gov
URL") and a "Fetch" button, so you don't need a terminal to get a
company's data or to regenerate a stale/narrower CSV from an earlier
version of this tool. Type a ticker (e.g. `AMZN`) or paste a SEC.gov URL
(browse URL, companyfacts JSON URL, or filing/Archives URL -- with or
without the `https://` prefix) and press Enter or click Fetch.

The first time you fetch in a given run, you'll be asked to choose an
output folder via a folder picker; that choice is remembered for the
rest of the session (not saved to disk between app restarts) and reused
for every later fetch without asking again. Cancelling the picker
aborts the fetch silently, same as cancelling "Open CSV(s)".

The fetch runs in the background so the UI stays responsive; a status
message next to "Open CSV(s)" shows progress (e.g. "Fetching AMZN...")
and the input/button are disabled until it finishes. On success, the
freshly written CSV is loaded automatically and the dropdown jumps to
that company -- no separate "Open CSV(s)" step needed. Fetching a
company that's already loaded overwrites/refreshes it in place rather
than adding a duplicate dropdown entry. On failure (bad ticker,
unresolvable URL, network error, or a write failure), an error dialog
explains what went wrong and the input/button re-enable so you can
retry.

## Relationship to 10k_viewer.py

`10k_viewer.py` (repo root) is a separate GUI tool that parses one
manually-downloaded 10-K HTML file at a time from local disk. This CLI tool
is automated, multi-filing, multi-company, and network-driven via the XBRL
API rather than HTML parsing. They currently share no code. A future
integration could have the GUI shell out to this CLI's CSV output, or factor
out a shared tag-priority/fallback list, but that is out of scope for now.
