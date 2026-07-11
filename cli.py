"""Entry point: argument parsing and orchestration.

Implements (Milestone 5):
- argparse interface: --ticker (repeatable), --url (repeatable), at least
  one of the two required; --forms (default 10-K,10-Q); --out-dir
  (required); --user-agent (optional).
- Orchestration: resolve CIK(s) -> fetch facts -> normalize -> write one
  CSV per company into --out-dir.
- Progress/status lines to stderr, never mixed into CSV output.
- Non-zero exit code + clear message on total failure. Partial failure
  across multiple companies does not abort the whole run -- failures are
  skipped and reported at the end.

Usage examples:

    python -m sec_financials.cli --ticker AAPL --out-dir ./out
    python -m sec_financials.cli --ticker AAPL --ticker MSFT --out-dir ./out
    python -m sec_financials.cli \\
        --url https://www.sec.gov/edgar/browse/?CIK=1652044&owner=exclude \\
        --out-dir ./out
    python -m sec_financials.cli --ticker AAPL --forms 10-K --out-dir ./out
    python -m sec_financials.cli --ticker AAPL --out-dir ./out \\
        --user-agent "Your Name your.email@example.com"
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from urllib.parse import urlparse

from sec_financials.company_lookup import (
    CompanyIndex,
    CompanyLookupError,
    CompanyMatch,
    extract_cik_from_url,
)
from sec_financials.csv_writer import write_company_csv
from sec_financials.edgar_client import EdgarClient, EdgarClientError
from sec_financials.facts_parser import FactsParserError, fetch_and_parse_company_facts
from sec_financials.xlsx_writer import XlsxWriterUnavailableError, write_company_xlsx

PROG = "sec_financials"

EPILOG = """\
examples:
  python -m sec_financials.cli --ticker AAPL --out-dir ./out
  python -m sec_financials.cli --ticker AAPL --ticker MSFT --out-dir ./out
  python -m sec_financials.cli --url https://www.sec.gov/edgar/browse/?CIK=1652044&owner=exclude --out-dir ./out
  python -m sec_financials.cli --ticker AAPL --forms 10-K --out-dir ./out
  python -m sec_financials.cli --ticker AAPL --out-dir ./out --user-agent "Your Name your.email@example.com"

configuration:
  SEC requires a descriptive User-Agent header (name + contact email) on
  every request. Resolved in priority order: --user-agent flag >
  SEC_EDGAR_USER_AGENT environment variable > a generic default.
"""


@dataclass
class Target:
    """One company to process, before CIK resolution."""

    kind: str  # "ticker" or "url"
    value: str


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=PROG,
        description=(
            "Fetch Revenue, Cost of Sales, and Gross Profit for one or more "
            "companies' 10-K/10-Q filings from SEC EDGAR's XBRL Company "
            "Facts API, and write one CSV per company."
        ),
        epilog=EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--ticker",
        action="append",
        dest="tickers",
        default=[],
        metavar="NAME",
        help=(
            "Company ticker or name to look up (repeatable). At least one "
            "of --ticker/--url is required."
        ),
    )
    parser.add_argument(
        "--url",
        action="append",
        dest="urls",
        default=[],
        metavar="URL",
        help=(
            "A pasted SEC.gov URL to extract a CIK from (repeatable). "
            "Supports EDGAR browse URLs, companyfacts JSON URLs, and "
            "filing/Archives URLs. At least one of --ticker/--url is "
            "required."
        ),
    )
    parser.add_argument(
        "--forms",
        default="10-K,10-Q",
        metavar="FORMS",
        help="Comma-separated form types to include (default: %(default)s).",
    )
    parser.add_argument(
        "--out-dir",
        required=True,
        metavar="PATH",
        help="Output directory for per-company CSV files (required).",
    )
    parser.add_argument(
        "--user-agent",
        default=None,
        metavar="STRING",
        help=(
            "User-Agent header sent to SEC EDGAR (name + contact email). "
            "Overrides the SEC_EDGAR_USER_AGENT environment variable and "
            "the built-in default."
        ),
    )
    return parser


def _parse_forms(forms_arg: str) -> list[str]:
    forms = [f.strip().upper() for f in forms_arg.split(",") if f.strip()]
    return forms or ["10-K", "10-Q"]


def _resolve_target(
    target: Target, index: CompanyIndex
) -> CompanyMatch:
    """Resolve a Target (ticker/name or URL) to a CompanyMatch.

    Raises CompanyLookupError on failure.
    """
    if target.kind == "url":
        cik = extract_cik_from_url(target.value)
        match = index.lookup_cik(cik)
        if match is not None:
            return match
        # CIK extracted fine but not present in company_tickers.json (e.g.
        # delisted or otherwise untracked) -- still usable, just no known
        # ticker/canonical name.
        return CompanyMatch(cik=cik, name=cik, ticker=cik)
    return index.resolve(target.value)


def _process_company(
    match: CompanyMatch,
    client: EdgarClient,
    forms: list[str],
    out_dir: str,
) -> tuple[str, int, str | None, str | None]:
    """Fetch, normalize, and write one company's CSV, plus its styled
    `.xlsx` workbook (Milestone 12) alongside it.

    Returns (csv_path, row_count, xlsx_path, xlsx_skip_notice).
    `xlsx_path` is None and `xlsx_skip_notice` is a one-line human
    -readable message when `.xlsx` writing was skipped (Decision B's
    soft-fail behavior: a missing `openpyxl` never blocks CSV output,
    which has zero new dependencies -- only the `.xlsx` step is skipped).
    Raises FactsParserError on a fetch/parse failure (this can still
    abort the whole company, same as before Milestone 12); CSV writing
    itself is unaffected by anything xlsx-related.
    """
    records = fetch_and_parse_company_facts(
        client, match.cik, match.name, match.ticker, forms=forms
    )
    filename_key = match.ticker if match.ticker and match.ticker != match.cik else match.cik
    out_path = write_company_csv(records, out_dir, filename_key)

    xlsx_path: str | None = None
    xlsx_skip_notice: str | None = None
    try:
        xlsx_out_path = write_company_xlsx(records, out_dir, filename_key)
        xlsx_path = str(xlsx_out_path)
    except XlsxWriterUnavailableError as exc:
        xlsx_skip_notice = str(exc)

    return str(out_path), len(records), xlsx_path, xlsx_skip_notice


def looks_like_url(text: str) -> bool:
    """Decide whether a single free-form input string (e.g. from the GUI's
    one ticker-or-URL field) should be treated as a SEC.gov URL rather than
    a ticker/company name.

    A bare `urlparse(text).scheme` check is insufficient: a user may paste a
    scheme-less domain like `www.sec.gov/edgar/browse/?CIK=...` (no
    `http(s)://` prefix), which has an empty `.scheme` and would otherwise be
    misrouted into ticker/name lookup. So: treat as a URL if the parsed
    scheme is `http`/`https`, OR -- when there's no scheme -- if re-parsing
    with an assumed `https://` prefix yields a netloc containing "sec.gov".
    """
    parsed = urlparse(text)
    if parsed.scheme in ("http", "https"):
        return True
    if not parsed.scheme:
        reparsed = urlparse("https://" + text)
        if "sec.gov" in reparsed.netloc.lower():
            return True
    return False


@dataclass
class RefreshResult:
    """Structured result of a single-company refresh, for callers (e.g. the
    GUI) that need to react without parsing stderr text."""

    company: str
    ticker: str
    cik: str
    out_path: str
    row_count: int
    # Milestone 12: additive field, None when `.xlsx` writing was
    # skipped (Decision B soft-fail -- `openpyxl` not available). Doesn't
    # break existing consumers that construct/read RefreshResult without
    # it.
    xlsx_path: str | None = None
    xlsx_skip_notice: str | None = None


def _refresh_target(
    target: Target,
    index: CompanyIndex,
    client: EdgarClient,
    forms: list[str],
    out_dir: str,
    *,
    on_resolved=None,
) -> RefreshResult:
    """Resolve one already-classified Target and run the fetch/parse/write
    pipeline for it. Shared body for both `refresh_company` (single
    free-form GUI input) and `main` (explicit --ticker/--url CLI targets),
    so there is exactly one resolve -> fetch -> parse -> write code path.

    `on_resolved`, if given, is called with the resolved CompanyMatch right
    after resolution succeeds and before the fetch/write starts -- used by
    `main()` to preserve its existing progress line to stderr without
    duplicating the resolve/process calls inline.
    """
    match = _resolve_target(target, index)
    if on_resolved is not None:
        on_resolved(match)
    out_path, row_count, xlsx_path, xlsx_skip_notice = _process_company(
        match, client, forms, out_dir
    )
    return RefreshResult(
        company=match.name,
        ticker=match.ticker,
        cik=match.cik,
        out_path=out_path,
        row_count=row_count,
        xlsx_path=xlsx_path,
        xlsx_skip_notice=xlsx_skip_notice,
    )


def refresh_company(
    ticker_or_url: str,
    out_dir: str,
    client: EdgarClient | None = None,
    forms: list[str] | None = None,
) -> RefreshResult:
    """Resolve, fetch, normalize, and write one company's CSV from a single
    free-form input string (bare ticker/name, or a pasted SEC.gov URL --
    see `looks_like_url` for the disambiguation rule).

    Shared by `main()` (CLI) and the GUI's fetch/refresh feature so both
    call the same resolve -> fetch -> parse -> write pipeline. Raises
    CompanyLookupError, FactsParserError, EdgarClientError, or OSError on
    failure -- the same exception types `main()` already catches.
    """
    if client is None:
        client = EdgarClient()
    if forms is None:
        forms = _parse_forms("10-K,10-Q")

    index = CompanyIndex.fetch(client)

    target = Target("url", ticker_or_url) if looks_like_url(ticker_or_url) else Target(
        "ticker", ticker_or_url
    )
    return _refresh_target(target, index, client, forms, out_dir)


def main(argv: list[str] | None = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)

    if not args.tickers and not args.urls:
        parser.error("At least one of --ticker or --url is required.")

    forms = _parse_forms(args.forms)

    targets: list[Target] = [Target("ticker", t) for t in args.tickers] + [
        Target("url", u) for u in args.urls
    ]

    client = EdgarClient(user_agent=args.user_agent)

    print(f"{PROG}: resolving {len(targets)} company target(s)...", file=sys.stderr)
    try:
        index = CompanyIndex.fetch(client)
    except EdgarClientError as exc:
        print(
            f"{PROG}: fatal: could not fetch company_tickers.json: {exc}",
            file=sys.stderr,
        )
        return 1

    successes: list[str] = []
    failures: list[str] = []

    def _report_resolved(match: CompanyMatch) -> None:
        print(
            f"{PROG}: fetching company facts for {match.name} "
            f"(ticker={match.ticker}, CIK={match.cik})...",
            file=sys.stderr,
        )

    for target in targets:
        label = target.value
        try:
            result = _refresh_target(
                target, index, client, forms, args.out_dir, on_resolved=_report_resolved
            )
        except CompanyLookupError as exc:
            print(f"{PROG}: skipping {label!r}: {exc}", file=sys.stderr)
            failures.append(label)
            continue
        except FactsParserError as exc:
            print(f"{PROG}: skipping {label!r}: {exc}", file=sys.stderr)
            failures.append(label)
            continue
        except OSError as exc:
            print(
                f"{PROG}: skipping {label!r}: could not write CSV: {exc}",
                file=sys.stderr,
            )
            failures.append(label)
            continue

        print(
            f"{PROG}: wrote {result.row_count} row(s) for {result.company} to "
            f"{result.out_path}",
            file=sys.stderr,
        )
        if result.xlsx_path is not None:
            print(f"{PROG}: wrote {result.xlsx_path}", file=sys.stderr)
        elif result.xlsx_skip_notice is not None:
            print(f"{PROG}: {result.xlsx_skip_notice}", file=sys.stderr)
        successes.append(label)

    print(
        f"{PROG}: done. {len(successes)} succeeded, {len(failures)} failed.",
        file=sys.stderr,
    )

    if failures and not successes:
        print(
            f"{PROG}: fatal: all targets failed: {', '.join(failures)}",
            file=sys.stderr,
        )
        return 1
    if failures:
        print(
            f"{PROG}: warning: some targets failed: {', '.join(failures)}",
            file=sys.stderr,
        )

    return 0


if __name__ == "__main__":
    sys.exit(main())
