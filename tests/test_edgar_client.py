"""Tests for edgar_client.py. Fixtures/mocks only — no live network calls."""

from __future__ import annotations

import json
import os
import time
import urllib.error
from unittest import mock

import pytest

from sec_financials import edgar_client


# -- resolve_user_agent -------------------------------------------------


def test_resolve_user_agent_explicit_wins(monkeypatch):
    monkeypatch.setenv(edgar_client.USER_AGENT_ENV_VAR, "env agent env@example.com")
    assert edgar_client.resolve_user_agent("explicit agent x@example.com") == (
        "explicit agent x@example.com"
    )


def test_resolve_user_agent_env_var_used_when_no_explicit(monkeypatch):
    monkeypatch.setenv(edgar_client.USER_AGENT_ENV_VAR, "env agent env@example.com")
    assert edgar_client.resolve_user_agent(None) == "env agent env@example.com"


def test_resolve_user_agent_default_when_neither_set(monkeypatch):
    monkeypatch.delenv(edgar_client.USER_AGENT_ENV_VAR, raising=False)
    assert edgar_client.resolve_user_agent(None) == edgar_client.DEFAULT_USER_AGENT


# -- get / get_json -------------------------------------------------


def _make_client(tmp_path):
    client = edgar_client.EdgarClient(
        user_agent="test agent test@example.com",
        request_delay=0.0,
        cache_dir=tmp_path / "cache",
    )
    return client


def test_get_returns_body_on_200(tmp_path):
    client = _make_client(tmp_path)
    fake_response = mock.MagicMock()
    fake_response.read.return_value = b'{"ok": true}'
    fake_response.__enter__.return_value = fake_response
    fake_response.__exit__.return_value = False

    with mock.patch("urllib.request.urlopen", return_value=fake_response) as m:
        body = client.get("https://example.com/x")
    assert body == b'{"ok": true}'
    m.assert_called_once()


def test_get_json_parses_valid_json(tmp_path):
    client = _make_client(tmp_path)
    with mock.patch.object(client, "get", return_value=b'{"a": 1}'):
        assert client.get_json("https://example.com/x") == {"a": 1}


def test_get_json_raises_clear_error_on_malformed_json(tmp_path):
    client = _make_client(tmp_path)
    with mock.patch.object(client, "get", return_value=b"not json"):
        with pytest.raises(edgar_client.EdgarClientError, match="Malformed JSON"):
            client.get_json("https://example.com/x")


def test_get_retries_on_429_then_succeeds(tmp_path):
    client = _make_client(tmp_path)
    client.max_retries = 2

    fake_response = mock.MagicMock()
    fake_response.read.return_value = b"ok"
    fake_response.__enter__.return_value = fake_response
    fake_response.__exit__.return_value = False

    error = urllib.error.HTTPError(
        "https://example.com/x", 429, "Too Many Requests", {}, None
    )

    with mock.patch("time.sleep", return_value=None):
        with mock.patch(
            "urllib.request.urlopen", side_effect=[error, fake_response]
        ) as m:
            body = client.get("https://example.com/x")
    assert body == b"ok"
    assert m.call_count == 2


def test_get_raises_after_exhausting_retries(tmp_path):
    client = _make_client(tmp_path)
    client.max_retries = 1
    error = urllib.error.HTTPError(
        "https://example.com/x", 503, "Service Unavailable", {}, None
    )

    with mock.patch("time.sleep", return_value=None):
        with mock.patch("urllib.request.urlopen", side_effect=[error, error]):
            with pytest.raises(edgar_client.EdgarClientError, match="HTTP 503"):
                client.get("https://example.com/x")


def test_get_raises_immediately_on_non_retryable_status(tmp_path):
    client = _make_client(tmp_path)
    error = urllib.error.HTTPError("https://example.com/x", 404, "Not Found", {}, None)

    with mock.patch("urllib.request.urlopen", side_effect=[error]) as m:
        with pytest.raises(edgar_client.EdgarClientError, match="HTTP 404"):
            client.get("https://example.com/x")
    assert m.call_count == 1


# -- company_tickers.json cache -------------------------------------------------


def test_get_company_tickers_uses_fresh_cache(tmp_path):
    client = _make_client(tmp_path)
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir(parents=True)
    cache_path = cache_dir / edgar_client.COMPANY_TICKERS_CACHE_FILENAME
    cache_path.write_text(json.dumps({"0": {"cik_str": 1, "ticker": "X"}}))

    with mock.patch.object(client, "get_json") as m:
        data = client.get_company_tickers()
    m.assert_not_called()
    assert data == {"0": {"cik_str": 1, "ticker": "X"}}


def test_get_company_tickers_refetches_when_stale(tmp_path):
    client = _make_client(tmp_path)
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir(parents=True)
    cache_path = cache_dir / edgar_client.COMPANY_TICKERS_CACHE_FILENAME
    cache_path.write_text(json.dumps({"stale": True}))
    old_time = time.time() - edgar_client.CACHE_MAX_AGE_SECONDS - 100
    os.utime(cache_path, (old_time, old_time))

    fresh_data = {"0": {"cik_str": 2, "ticker": "Y"}}
    with mock.patch.object(client, "get_json", return_value=fresh_data) as m:
        data = client.get_company_tickers()
    m.assert_called_once()
    assert data == fresh_data
    assert json.loads(cache_path.read_text()) == fresh_data


def test_get_company_tickers_refetches_when_missing(tmp_path):
    client = _make_client(tmp_path)
    fresh_data = {"0": {"cik_str": 3, "ticker": "Z"}}
    with mock.patch.object(client, "get_json", return_value=fresh_data) as m:
        data = client.get_company_tickers()
    m.assert_called_once()
    assert data == fresh_data


def test_get_company_tickers_force_refresh_ignores_fresh_cache(tmp_path):
    client = _make_client(tmp_path)
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir(parents=True)
    cache_path = cache_dir / edgar_client.COMPANY_TICKERS_CACHE_FILENAME
    cache_path.write_text(json.dumps({"cached": True}))

    fresh_data = {"0": {"cik_str": 4, "ticker": "W"}}
    with mock.patch.object(client, "get_json", return_value=fresh_data) as m:
        data = client.get_company_tickers(force_refresh=True)
    m.assert_called_once()
    assert data == fresh_data
