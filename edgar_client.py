"""Low-level HTTP client for data.sec.gov / www.sec.gov.

Implements (Milestone 1):
- urllib.request-based GET helper with configurable User-Agent header,
  resolved as: explicit argument > SEC_EDGAR_USER_AGENT env var > default.
- Fixed inter-request delay plus retry/backoff on 429/403/5xx responses.
- JSON response parsing with a clear error on non-200 / malformed JSON.
- A simple on-disk cache for company_tickers.json (refreshed if stale).
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

DEFAULT_USER_AGENT = "sec_financials CLI doan951@gmail.com"
USER_AGENT_ENV_VAR = "SEC_EDGAR_USER_AGENT"

# Fixed small delay between requests to stay within SEC's fair-access limits.
REQUEST_DELAY_SECONDS = 0.11

# Retry behavior for transient failures.
RETRYABLE_STATUS_CODES = {429, 403, 500, 502, 503, 504}
MAX_RETRIES = 4
RETRY_BACKOFF_BASE_SECONDS = 1.0

# On-disk cache for company_tickers.json.
DEFAULT_CACHE_DIR = Path(__file__).resolve().parent / ".cache"
COMPANY_TICKERS_CACHE_FILENAME = "company_tickers.json"
COMPANY_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
CACHE_MAX_AGE_SECONDS = 24 * 60 * 60  # 1 day


class EdgarClientError(Exception):
    """Raised for any unrecoverable failure talking to SEC EDGAR."""


def resolve_user_agent(explicit: str | None = None) -> str:
    """Resolve the User-Agent string to send with requests.

    Priority: explicit argument (e.g. CLI flag) > SEC_EDGAR_USER_AGENT env
    var > default contact string.
    """
    if explicit:
        return explicit
    env_value = os.environ.get(USER_AGENT_ENV_VAR)
    if env_value:
        return env_value
    return DEFAULT_USER_AGENT


@dataclass
class EdgarClient:
    """Thin HTTP client wrapping urllib.request for SEC EDGAR endpoints."""

    user_agent: str | None = None
    request_delay: float = REQUEST_DELAY_SECONDS
    max_retries: int = MAX_RETRIES
    cache_dir: Path = DEFAULT_CACHE_DIR
    _last_request_time: float = 0.0

    def __post_init__(self) -> None:
        self.user_agent = resolve_user_agent(self.user_agent)

    # -- core HTTP -----------------------------------------------------

    def _throttle(self) -> None:
        elapsed = time.monotonic() - self._last_request_time
        remaining = self.request_delay - elapsed
        if remaining > 0:
            time.sleep(remaining)

    def get(self, url: str) -> bytes:
        """GET a URL, returning the raw response body as bytes.

        Applies a fixed inter-request delay and retries with exponential
        backoff on 429/403/5xx responses. Raises EdgarClientError on
        non-retryable failures or after exhausting retries.
        """
        headers = {"User-Agent": self.user_agent}
        last_error: Exception | None = None

        for attempt in range(self.max_retries + 1):
            self._throttle()
            request = urllib.request.Request(url, headers=headers)
            try:
                with urllib.request.urlopen(request, timeout=30) as response:
                    body = response.read()
                self._last_request_time = time.monotonic()
                return body
            except urllib.error.HTTPError as exc:
                self._last_request_time = time.monotonic()
                if exc.code in RETRYABLE_STATUS_CODES and attempt < self.max_retries:
                    last_error = exc
                    backoff = RETRY_BACKOFF_BASE_SECONDS * (2 ** attempt)
                    time.sleep(backoff)
                    continue
                raise EdgarClientError(
                    f"HTTP {exc.code} error fetching {url!r}: {exc.reason}"
                ) from exc
            except urllib.error.URLError as exc:
                self._last_request_time = time.monotonic()
                if attempt < self.max_retries:
                    last_error = exc
                    backoff = RETRY_BACKOFF_BASE_SECONDS * (2 ** attempt)
                    time.sleep(backoff)
                    continue
                raise EdgarClientError(
                    f"Network error fetching {url!r}: {exc.reason}"
                ) from exc

        # Should be unreachable, but guard just in case.
        raise EdgarClientError(
            f"Failed to fetch {url!r} after {self.max_retries} retries"
        ) from last_error

    def get_json(self, url: str) -> Any:
        """GET a URL and parse the response body as JSON.

        Raises EdgarClientError with a clear message if the response is not
        valid JSON.
        """
        body = self.get(url)
        try:
            return json.loads(body)
        except json.JSONDecodeError as exc:
            raise EdgarClientError(
                f"Malformed JSON response from {url!r}: {exc}"
            ) from exc

    # -- company_tickers.json cache -------------------------------------

    def get_company_tickers(self, force_refresh: bool = False) -> Any:
        """Return the parsed company_tickers.json, using an on-disk cache.

        The cache is refreshed if missing, unreadable, or older than
        CACHE_MAX_AGE_SECONDS.
        """
        cache_path = self.cache_dir / COMPANY_TICKERS_CACHE_FILENAME

        if not force_refresh and cache_path.exists():
            age = time.time() - cache_path.stat().st_mtime
            if age <= CACHE_MAX_AGE_SECONDS:
                try:
                    with cache_path.open("r", encoding="utf-8") as f:
                        return json.load(f)
                except (OSError, json.JSONDecodeError):
                    pass  # fall through to refetch

        data = self.get_json(COMPANY_TICKERS_URL)

        try:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            with cache_path.open("w", encoding="utf-8") as f:
                json.dump(data, f)
        except OSError:
            pass  # cache write failure is non-fatal

        return data
