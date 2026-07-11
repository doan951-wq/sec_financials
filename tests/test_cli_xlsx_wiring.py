"""Tests for Milestone 12.4: wiring the `.xlsx` writer into `cli.py`'s
`_process_company` (the shared seam both `main()` and
`refresh_company`/`_refresh_target` call), including Decision B's
soft-fail behavior. Network calls mocked -- no live network access.
"""

from __future__ import annotations

from unittest import mock

import pytest

from sec_financials import cli, xlsx_writer
from sec_financials.company_lookup import CompanyMatch
from sec_financials.edgar_client import EdgarClient
from sec_financials.tests.test_cli import SAMPLE_TICKERS, _sample_records  # noqa: E402


@pytest.fixture(autouse=True)
def mock_company_tickers(monkeypatch):
    monkeypatch.setattr(
        "sec_financials.edgar_client.EdgarClient.get_company_tickers",
        lambda self, force_refresh=False: SAMPLE_TICKERS,
    )


class TestProcessCompanyWritesBothFilesWhenAvailable:
    def test_process_company_calls_xlsx_writer_with_right_records_and_path(self, tmp_path):
        match = CompanyMatch(cik="0000320193", name="Apple Inc.", ticker="AAPL")
        client = EdgarClient()
        records = _sample_records("AAPL")

        with mock.patch.object(
            cli, "fetch_and_parse_company_facts", return_value=records
        ), mock.patch.object(
            cli, "write_company_xlsx", return_value=tmp_path / "AAPL.xlsx"
        ) as mock_write_xlsx:
            csv_path, row_count, xlsx_path, xlsx_skip_notice = cli._process_company(
                match, client, ["10-K", "10-Q"], str(tmp_path)
            )

        mock_write_xlsx.assert_called_once()
        call_args = mock_write_xlsx.call_args
        assert call_args.args[0] == records
        assert call_args.args[1] == str(tmp_path)
        assert call_args.args[2] == "AAPL"

        assert row_count == 1
        assert xlsx_path == str(tmp_path / "AAPL.xlsx")
        assert xlsx_skip_notice is None
        assert csv_path == str(tmp_path / "AAPL.csv")

    def test_real_openpyxl_write_produces_both_files_on_disk(self, tmp_path):
        # No mocking of write_company_xlsx here -- exercises the real
        # openpyxl write path end to end (this test venv has openpyxl
        # installed per Milestone 12.1).
        match = CompanyMatch(cik="0000320193", name="Apple Inc.", ticker="AAPL")
        client = EdgarClient()
        records = _sample_records("AAPL")

        with mock.patch.object(cli, "fetch_and_parse_company_facts", return_value=records):
            csv_path, row_count, xlsx_path, xlsx_skip_notice = cli._process_company(
                match, client, ["10-K", "10-Q"], str(tmp_path)
            )

        assert xlsx_skip_notice is None
        assert xlsx_path is not None
        assert (tmp_path / "AAPL.csv").exists()
        assert (tmp_path / "AAPL.xlsx").exists()


class TestSoftFailWhenOpenpyxlUnavailable:
    def test_process_company_soft_fails_leaves_csv_written(self, tmp_path, monkeypatch):
        match = CompanyMatch(cik="0000320193", name="Apple Inc.", ticker="AAPL")
        client = EdgarClient()
        records = _sample_records("AAPL")

        def _raise_unavailable(*args, **kwargs):
            raise xlsx_writer.XlsxWriterUnavailableError(
                "The '.xlsx' output feature requires the 'openpyxl' package... "
                f"{xlsx_writer.OPENPYXL_INSTALL_COMMAND}"
            )

        with mock.patch.object(
            cli, "fetch_and_parse_company_facts", return_value=records
        ), mock.patch.object(cli, "write_company_xlsx", side_effect=_raise_unavailable):
            csv_path, row_count, xlsx_path, xlsx_skip_notice = cli._process_company(
                match, client, ["10-K", "10-Q"], str(tmp_path)
            )

        assert (tmp_path / "AAPL.csv").exists()
        assert xlsx_path is None
        assert xlsx_skip_notice is not None
        assert xlsx_writer.OPENPYXL_INSTALL_COMMAND in xlsx_skip_notice

    def test_main_soft_fail_still_exits_zero_and_writes_csv(self, tmp_path, capsys):
        records = _sample_records("AAPL")

        def _raise_unavailable(*args, **kwargs):
            raise xlsx_writer.XlsxWriterUnavailableError(
                f"install via {xlsx_writer.OPENPYXL_INSTALL_COMMAND}"
            )

        with mock.patch.object(
            cli, "fetch_and_parse_company_facts", return_value=records
        ), mock.patch.object(cli, "write_company_xlsx", side_effect=_raise_unavailable):
            exit_code = cli.main(["--ticker", "AAPL", "--out-dir", str(tmp_path)])

        assert exit_code == 0
        assert (tmp_path / "AAPL.csv").exists()
        assert not (tmp_path / "AAPL.xlsx").exists()

        captured = capsys.readouterr()
        assert xlsx_writer.OPENPYXL_INSTALL_COMMAND in captured.err
        assert "succeeded" in captured.err


class TestRefreshResultXlsxFields:
    def test_refresh_result_has_xlsx_fields_populated_on_success(self, tmp_path):
        records = _sample_records("AAPL")
        with mock.patch.object(cli, "fetch_and_parse_company_facts", return_value=records):
            result = cli.refresh_company("AAPL", str(tmp_path))

        assert result.xlsx_path is not None
        assert result.xlsx_path.endswith("AAPL.xlsx")
        assert result.xlsx_skip_notice is None

    def test_refresh_result_xlsx_fields_none_on_soft_fail(self, tmp_path):
        records = _sample_records("AAPL")

        def _raise_unavailable(*args, **kwargs):
            raise xlsx_writer.XlsxWriterUnavailableError("skip me")

        with mock.patch.object(
            cli, "fetch_and_parse_company_facts", return_value=records
        ), mock.patch.object(cli, "write_company_xlsx", side_effect=_raise_unavailable):
            result = cli.refresh_company("AAPL", str(tmp_path))

        assert result.xlsx_path is None
        assert result.xlsx_skip_notice == "skip me"

    def test_refresh_result_backward_compatible_without_xlsx_kwargs(self):
        # Existing consumers constructing RefreshResult positionally/by
        # the original 5 keyword args must still work unchanged (additive
        # field rule).
        result = cli.RefreshResult(
            company="Apple Inc.",
            ticker="AAPL",
            cik="0000320193",
            out_path="/tmp/AAPL.csv",
            row_count=1,
        )
        assert result.xlsx_path is None
        assert result.xlsx_skip_notice is None
