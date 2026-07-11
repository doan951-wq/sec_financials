"""Tests for company_lookup.py. Fixtures only — no live network calls."""

from __future__ import annotations

import pytest

from sec_financials.company_lookup import (
    CompanyIndex,
    CompanyLookupError,
    extract_cik_from_url,
    format_cik,
)

SAMPLE_RAW_DATA = {
    "0": {"cik_str": 320193, "ticker": "AAPL", "title": "Apple Inc."},
    "1": {"cik_str": 789019, "ticker": "MSFT", "title": "MICROSOFT CORP"},
    "2": {"cik_str": 1018724, "ticker": "AMZN", "title": "AMAZON COM INC"},
    "3": {"cik_str": 1467373, "ticker": "GM", "title": "General Motors Co"},
    "4": {"cik_str": 40545, "ticker": "GE", "title": "General Electric Co"},
}


@pytest.fixture
def index():
    return CompanyIndex(SAMPLE_RAW_DATA)


# -- format_cik -------------------------------------------------


def test_format_cik_pads_to_10_digits():
    assert format_cik(320193) == "0000320193"
    assert format_cik("320193") == "0000320193"
    assert format_cik("0000320193") == "0000320193"


# -- ticker lookup -------------------------------------------------


def test_lookup_ticker_case_insensitive(index):
    match = index.lookup_ticker("aapl")
    assert match.cik == "0000320193"
    assert match.ticker == "AAPL"
    assert match.name == "Apple Inc."


def test_lookup_ticker_no_match_returns_none(index):
    assert index.lookup_ticker("NOTATICKER") is None


# -- name lookup -------------------------------------------------


def test_lookup_name_exact_match(index):
    match = index.lookup_name("Apple Inc.")
    assert match.ticker == "AAPL"


def test_lookup_name_substring_case_insensitive(index):
    match = index.lookup_name("microsoft")
    assert match.ticker == "MSFT"


def test_lookup_name_no_match_raises(index):
    with pytest.raises(CompanyLookupError, match="No company found"):
        index.lookup_name("Totally Fake Company Name")


def test_lookup_name_ambiguous_raises_with_candidates(index):
    with pytest.raises(CompanyLookupError) as exc_info:
        index.lookup_name("General")
    message = str(exc_info.value)
    assert "Ambiguous" in message
    assert "General Motors Co" in message
    assert "General Electric Co" in message


def test_lookup_name_empty_raises(index):
    with pytest.raises(CompanyLookupError, match="Empty company name"):
        index.lookup_name("   ")


# -- resolve (ticker-or-name) -------------------------------------------------


def test_resolve_prefers_ticker_match(index):
    match = index.resolve("AMZN")
    assert match.name == "AMAZON COM INC"


def test_resolve_falls_back_to_name(index):
    match = index.resolve("Apple Inc.")
    assert match.ticker == "AAPL"


# -- lookup_cik -------------------------------------------------


def test_lookup_cik_matches_unpadded_and_padded(index):
    assert index.lookup_cik("320193").ticker == "AAPL"
    assert index.lookup_cik("0000320193").ticker == "AAPL"


def test_lookup_cik_no_match_returns_none(index):
    assert index.lookup_cik("9999999999") is None


# -- extract_cik_from_url -------------------------------------------------


def test_extract_cik_from_browse_edgar_url():
    url = "https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK=0000320193&type=10-K"
    assert extract_cik_from_url(url) == "0000320193"


def test_extract_cik_from_browse_edgar_url_unpadded():
    url = "https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK=320193&type=10-K"
    assert extract_cik_from_url(url) == "0000320193"


def test_extract_cik_from_companyfacts_url():
    url = "https://data.sec.gov/api/xbrl/companyfacts/CIK0000320193.json"
    assert extract_cik_from_url(url) == "0000320193"


def test_extract_cik_from_archives_url_padded():
    url = "https://www.sec.gov/Archives/edgar/data/0000320193/000032019323000106/aapl-20230930.htm"
    assert extract_cik_from_url(url) == "0000320193"


def test_extract_cik_from_archives_url_unpadded():
    url = "https://www.sec.gov/Archives/edgar/data/320193/000032019323000106/"
    assert extract_cik_from_url(url) == "0000320193"


def test_extract_cik_from_modern_edgar_browse_url():
    url = "https://www.sec.gov/edgar/browse/?CIK=1652044&owner=exclude"
    assert extract_cik_from_url(url) == "0001652044"


def test_extract_cik_from_url_no_match_raises():
    url = "https://www.sec.gov/some/unrelated/page.htm"
    with pytest.raises(CompanyLookupError, match="Could not extract a CIK"):
        extract_cik_from_url(url)
