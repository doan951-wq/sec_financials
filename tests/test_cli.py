"""Tests for cli.py. Network calls mocked -- no live network access."""

from __future__ import annotations

import csv
from unittest import mock

import pytest

from sec_financials import cli
from sec_financials.company_lookup import CompanyLookupError
from sec_financials.facts_parser import FilingRecord

SAMPLE_TICKERS = {
    "0": {"cik_str": 320193, "ticker": "AAPL", "title": "Apple Inc."},
    "1": {"cik_str": 1652044, "ticker": "GOOGL", "title": "Alphabet Inc."},
}


def _sample_records(ticker):
    return [
        FilingRecord(
            company="Test Co",
            ticker=ticker,
            cik="0000320193",
            form="10-K",
            fiscal_year=2022,
            fiscal_period="FY",
            period_end="2022-09-24",
            filed="2022-10-28",
            revenue=394328000000,
            cost_of_sales=223546000000,
            gross_profit=170782000000,
        )
    ]


@pytest.fixture(autouse=True)
def mock_company_tickers(monkeypatch):
    monkeypatch.setattr(
        "sec_financials.edgar_client.EdgarClient.get_company_tickers",
        lambda self, force_refresh=False: SAMPLE_TICKERS,
    )


# -- argument parsing -------------------------------------------------


def test_requires_at_least_one_ticker_or_url(capsys):
    with pytest.raises(SystemExit) as exc_info:
        cli.main(["--out-dir", "/tmp/whatever"])
    assert exc_info.value.code != 0
    captured = capsys.readouterr()
    assert "At least one of --ticker or --url" in captured.err


def test_out_dir_is_required():
    with pytest.raises(SystemExit) as exc_info:
        cli.main(["--ticker", "AAPL"])
    assert exc_info.value.code != 0


# -- orchestration: success path -------------------------------------------------


def test_ticker_success_writes_csv(tmp_path, capsys):
    with mock.patch.object(
        cli, "fetch_and_parse_company_facts", return_value=_sample_records("AAPL")
    ):
        exit_code = cli.main(
            ["--ticker", "AAPL", "--out-dir", str(tmp_path)]
        )

    assert exit_code == 0
    out_file = tmp_path / "AAPL.csv"
    assert out_file.exists()
    with out_file.open() as f:
        rows = list(csv.DictReader(f))
    assert len(rows) == 1
    assert rows[0]["Revenue"] == "394328000000"

    captured = capsys.readouterr()
    assert "succeeded" in captured.err


def test_url_success_resolves_cik_and_writes_csv(tmp_path):
    with mock.patch.object(
        cli, "fetch_and_parse_company_facts", return_value=_sample_records("GOOGL")
    ):
        exit_code = cli.main(
            [
                "--url",
                "https://www.sec.gov/edgar/browse/?CIK=1652044&owner=exclude",
                "--out-dir",
                str(tmp_path),
            ]
        )

    assert exit_code == 0
    assert (tmp_path / "GOOGL.csv").exists()


def test_multiple_tickers_each_get_own_csv(tmp_path):
    with mock.patch.object(
        cli, "fetch_and_parse_company_facts", side_effect=lambda *a, **k: _sample_records("X")
    ):
        exit_code = cli.main(
            ["--ticker", "AAPL", "--ticker", "GOOGL", "--out-dir", str(tmp_path)]
        )

    assert exit_code == 0
    assert (tmp_path / "AAPL.csv").exists()
    assert (tmp_path / "GOOGL.csv").exists()


# -- orchestration: partial / total failure -------------------------------------------------


def test_bad_ticker_among_others_is_partial_failure_not_abort(tmp_path, capsys):
    with mock.patch.object(
        cli, "fetch_and_parse_company_facts", return_value=_sample_records("AAPL")
    ):
        exit_code = cli.main(
            [
                "--ticker",
                "AAPL",
                "--ticker",
                "NOTATICKER_NOTANAME",
                "--out-dir",
                str(tmp_path),
            ]
        )

    assert exit_code == 0  # partial failure should not abort the whole run
    assert (tmp_path / "AAPL.csv").exists()
    captured = capsys.readouterr()
    assert "warning: some targets failed" in captured.err


def test_all_targets_failing_returns_nonzero(tmp_path, capsys):
    exit_code = cli.main(
        ["--ticker", "NOTATICKER_NOTANAME", "--out-dir", str(tmp_path)]
    )
    assert exit_code != 0
    captured = capsys.readouterr()
    assert "fatal: all targets failed" in captured.err


def test_company_tickers_fetch_failure_is_fatal(tmp_path, capsys, monkeypatch):
    from sec_financials.edgar_client import EdgarClientError

    monkeypatch.setattr(
        "sec_financials.edgar_client.EdgarClient.get_company_tickers",
        mock.Mock(side_effect=EdgarClientError("network down")),
    )
    exit_code = cli.main(["--ticker", "AAPL", "--out-dir", str(tmp_path)])
    assert exit_code != 0
    captured = capsys.readouterr()
    assert "fatal" in captured.err


def test_facts_parser_error_is_skipped_not_fatal(tmp_path, capsys):
    from sec_financials.facts_parser import FactsParserError

    with mock.patch.object(
        cli,
        "fetch_and_parse_company_facts",
        side_effect=FactsParserError("boom"),
    ):
        exit_code = cli.main(["--ticker", "AAPL", "--out-dir", str(tmp_path)])
    assert exit_code != 0  # only target given, and it failed -> total failure
    captured = capsys.readouterr()
    assert "boom" in captured.err


# -- forms parsing -------------------------------------------------


def test_forms_argument_parsed_and_passed_through(tmp_path):
    captured_forms = {}

    def fake_fetch(client, cik, company, ticker, forms=None):
        captured_forms["forms"] = forms
        return _sample_records(ticker)

    with mock.patch.object(cli, "fetch_and_parse_company_facts", side_effect=fake_fetch):
        cli.main(
            ["--ticker", "AAPL", "--forms", "10-K", "--out-dir", str(tmp_path)]
        )

    assert captured_forms["forms"] == ["10-K"]


def test_forms_default_is_both():
    forms = cli._parse_forms("10-K,10-Q")
    assert forms == ["10-K", "10-Q"]


# -- Milestone 11.1: single-string ticker-vs-URL disambiguation ---------


def test_looks_like_url_plain_ticker_is_not_a_url():
    assert cli.looks_like_url("AMZN") is False


def test_looks_like_url_full_https_url_is_a_url():
    assert (
        cli.looks_like_url("https://www.sec.gov/edgar/browse/?CIK=1018724&owner=exclude")
        is True
    )


def test_looks_like_url_scheme_less_sec_domain_is_a_url():
    # No "https://" prefix -- urlparse(...).scheme alone would be empty and
    # would misroute this into a failed ticker/name lookup.
    assert (
        cli.looks_like_url("www.sec.gov/edgar/browse/?CIK=1018724&owner=exclude")
        is True
    )


def test_refresh_company_resolves_ticker(tmp_path, monkeypatch):
    tickers = {
        "0": {"cik_str": 1018724, "ticker": "AMZN", "title": "AMAZON COM INC"},
    }
    monkeypatch.setattr(
        "sec_financials.edgar_client.EdgarClient.get_company_tickers",
        lambda self, force_refresh=False: tickers,
    )
    with mock.patch.object(
        cli, "fetch_and_parse_company_facts", return_value=_sample_records("AMZN")
    ):
        result = cli.refresh_company("AMZN", str(tmp_path))

    assert result.ticker == "AMZN"
    assert (tmp_path / "AMZN.csv").exists()


def test_refresh_company_resolves_full_url(tmp_path, monkeypatch):
    tickers = {
        "0": {"cik_str": 1018724, "ticker": "AMZN", "title": "AMAZON COM INC"},
    }
    monkeypatch.setattr(
        "sec_financials.edgar_client.EdgarClient.get_company_tickers",
        lambda self, force_refresh=False: tickers,
    )
    with mock.patch.object(
        cli, "fetch_and_parse_company_facts", return_value=_sample_records("AMZN")
    ):
        result = cli.refresh_company(
            "https://www.sec.gov/edgar/browse/?CIK=1018724&owner=exclude", str(tmp_path)
        )

    assert result.cik == "0001018724"
    assert result.ticker == "AMZN"


def test_refresh_company_resolves_scheme_less_url(tmp_path, monkeypatch):
    tickers = {
        "0": {"cik_str": 1018724, "ticker": "AMZN", "title": "AMAZON COM INC"},
    }
    monkeypatch.setattr(
        "sec_financials.edgar_client.EdgarClient.get_company_tickers",
        lambda self, force_refresh=False: tickers,
    )
    with mock.patch.object(
        cli, "fetch_and_parse_company_facts", return_value=_sample_records("AMZN")
    ):
        result = cli.refresh_company(
            "www.sec.gov/edgar/browse/?CIK=1018724&owner=exclude", str(tmp_path)
        )

    assert result.cik == "0001018724"
    assert result.ticker == "AMZN"
