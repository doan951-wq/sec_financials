"""Ticker/name -> CIK resolution, plus SEC URL -> CIK extraction.

Implements (Milestone 2):
- Fetch and index company_tickers.json by ticker (case-insensitive) and by
  fuzzy/substring name match.
- Given a ticker or company name, return (CIK zero-padded to 10 digits,
  canonical company name, ticker).
- Given a pasted SEC.gov URL, extract a CIK from several supported URL
  shapes.
- Ambiguous/no-match name lookups raise a clear error listing candidates.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import parse_qs, urlparse

from sec_financials.edgar_client import EdgarClient


class CompanyLookupError(Exception):
    """Raised when a ticker/name/URL cannot be resolved to a CIK, or is
    ambiguous."""


@dataclass(frozen=True)
class CompanyMatch:
    cik: str  # zero-padded to 10 digits
    name: str
    ticker: str


def format_cik(cik: int | str) -> str:
    """Zero-pad a CIK to 10 digits."""
    return str(int(cik)).zfill(10)


class CompanyIndex:
    """Indexes company_tickers.json for ticker and name lookups."""

    def __init__(self, raw_data: dict[str, Any]):
        # raw_data is a dict of numeric-string keys -> {"cik_str", "ticker", "title"}
        self._entries: list[CompanyMatch] = []
        self._by_ticker: dict[str, CompanyMatch] = {}

        for entry in raw_data.values():
            match = CompanyMatch(
                cik=format_cik(entry["cik_str"]),
                name=entry["title"],
                ticker=entry["ticker"],
            )
            self._entries.append(match)
            self._by_ticker[match.ticker.upper()] = match

    @classmethod
    def fetch(cls, client: EdgarClient, force_refresh: bool = False) -> "CompanyIndex":
        raw_data = client.get_company_tickers(force_refresh=force_refresh)
        return cls(raw_data)

    def lookup_ticker(self, ticker: str) -> CompanyMatch | None:
        return self._by_ticker.get(ticker.strip().upper())

    def lookup_name(self, name: str) -> CompanyMatch:
        """Fuzzy/substring, case-insensitive match on company name.

        Returns the single match if exactly one candidate is found. Raises
        CompanyLookupError with a clear message listing candidates if there
        are zero or multiple matches.
        """
        needle = name.strip().lower()
        if not needle:
            raise CompanyLookupError("Empty company name given for lookup.")

        exact_matches = [e for e in self._entries if e.name.lower() == needle]
        if len(exact_matches) == 1:
            return exact_matches[0]

        substring_matches = [e for e in self._entries if needle in e.name.lower()]

        if len(substring_matches) == 1:
            return substring_matches[0]
        if len(substring_matches) == 0:
            raise CompanyLookupError(
                f"No company found matching name {name!r}."
            )

        # Ambiguous: list close candidates (cap to keep error readable).
        candidates = ", ".join(
            f"{e.name!r} ({e.ticker}, CIK {e.cik})" for e in substring_matches[:10]
        )
        more = (
            f" (+{len(substring_matches) - 10} more)"
            if len(substring_matches) > 10
            else ""
        )
        raise CompanyLookupError(
            f"Ambiguous company name {name!r} matched {len(substring_matches)} "
            f"companies: {candidates}{more}. Please use a ticker or a more "
            f"specific name instead."
        )

    def resolve(self, ticker_or_name: str) -> CompanyMatch:
        """Resolve a string that may be a ticker or a company name.

        Tries ticker lookup first (fast, unambiguous); falls back to name
        lookup.
        """
        by_ticker = self.lookup_ticker(ticker_or_name)
        if by_ticker is not None:
            return by_ticker
        return self.lookup_name(ticker_or_name)

    def lookup_cik(self, cik: str) -> CompanyMatch | None:
        """Look up a company by (unpadded or padded) CIK string."""
        padded = format_cik(cik)
        for entry in self._entries:
            if entry.cik == padded:
                return entry
        return None


# -- URL -> CIK extraction -------------------------------------------------

# .../Archives/edgar/data/{cik}/... (CIK may or may not be zero-padded)
_ARCHIVES_CIK_RE = re.compile(r"/Archives/edgar/data/(\d{1,10})(?:/|$)")

# .../companyfacts/CIK##########.json
_COMPANYFACTS_CIK_RE = re.compile(r"CIK(\d{10})\.json", re.IGNORECASE)

# Generic fallback: a "CIK" segment followed by digits, anywhere in the path
# (covers e.g. /cgi-bin/browse-edgar?action=getcompany&CIK=0000320193 as well
# as bare CIK=... query params handled separately below).
_CIK_DIGITS_RE = re.compile(r"CIK[=/]?(\d{1,10})", re.IGNORECASE)


def extract_cik_from_url(url: str) -> str:
    """Extract a CIK from a pasted SEC.gov URL.

    Supports:
    - EDGAR browse URLs (...action=getcompany&CIK=...)
    - data.sec.gov/api/xbrl/companyfacts/CIK##########.json URLs
    - Filing index/archive URLs (/Archives/edgar/data/{cik}/...)

    Raises CompanyLookupError if no CIK can be found in the URL.
    """
    parsed = urlparse(url)

    # 1. companyfacts JSON URL.
    match = _COMPANYFACTS_CIK_RE.search(url)
    if match:
        return format_cik(match.group(1))

    # 2. Archives filing index URLs.
    match = _ARCHIVES_CIK_RE.search(parsed.path)
    if match:
        return format_cik(match.group(1))

    # 3. Query-string CIK param (browse-edgar and similar).
    query_params = parse_qs(parsed.query)
    for key in ("CIK", "cik"):
        if key in query_params and query_params[key]:
            value = query_params[key][0]
            if value.isdigit():
                return format_cik(value)

    # 4. Generic fallback pattern anywhere in the URL.
    match = _CIK_DIGITS_RE.search(url)
    if match:
        return format_cik(match.group(1))

    raise CompanyLookupError(f"Could not extract a CIK from URL {url!r}.")
