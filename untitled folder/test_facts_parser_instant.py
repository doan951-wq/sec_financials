"""Tests for Milestone 7.2: instant-fact grouping (new code path for
balance-sheet-style concepts with no `start` field). Fixtures pin down the
exact real-data shapes verified live during planning (see PLAN.md's
plan-optimizer note) -- no live network calls here."""

from __future__ import annotations

import json
from pathlib import Path

from sec_financials import facts_parser

FIXTURES_DIR = Path(__file__).parent / "fixtures"


def _load_fixture(name: str) -> dict:
    with (FIXTURES_DIR / name).open("r", encoding="utf-8") as f:
        return json.load(f)


def test_assets_three_comparative_entries_picks_max_end_date():
    """A single 10-K accession with 3 Assets entries (current + two
    comparative prior fiscal year-ends) must resolve to just the current
    year -- the entry with the maximum `end` date. Real Apple figures."""
    facts_json = _load_fixture("facts_instant_assets_three_years.json")
    facts = facts_json["facts"]

    result = facts_parser._best_value_by_accession_for_tag_instant(
        facts, "Assets", ["10-K", "10-Q"]
    )
    assert len(result) == 1
    (key, dp), = result.items()
    assert key == ("0000320193-22-000108", "10-K")
    assert dp["end"] == "2022-09-24"
    assert dp["val"] == 352755000000


def test_stockholders_equity_six_entry_rollforward_picks_max_end_date():
    """The largest fan-out shape found live: a StockholdersEquity
    roll-forward statement inside one 10-Q accession reporting the balance
    at 6 different quarter-ends. Max-`end` must still correctly select
    this 10-Q's own period-end (2019-03-30), not any comparative prior
    quarter. Real Apple figures (accession 0000320193-19-000066)."""
    facts_json = _load_fixture("facts_instant_stockholders_equity_rollforward.json")
    facts = facts_json["facts"]

    result = facts_parser._best_value_by_accession_for_tag_instant(
        facts, "StockholdersEquity", ["10-K", "10-Q"]
    )
    assert len(result) == 1
    (key, dp), = result.items()
    assert key == ("0000320193-19-000066", "10-Q")
    assert dp["end"] == "2019-03-30"
    assert dp["val"] == 105860000000


def test_dei_entity_common_stock_shares_outstanding_single_entry_no_op():
    """dei:EntityCommonStockSharesOutstanding is a cover-page fact with no
    comparative period -- confirms the max-end rule degrades safely to a
    no-op (single candidate trivially "wins") rather than needing a
    special case for concepts that never have duplicates. Namespace is
    "dei", not "us-gaap"."""
    facts_json = _load_fixture("facts_instant_dei_shares_outstanding.json")
    facts = facts_json["facts"]

    result = facts_parser._best_value_by_accession_for_tag_instant(
        facts,
        "EntityCommonStockSharesOutstanding",
        ["10-K", "10-Q"],
        namespace="dei",
        unit="shares",
    )
    assert len(result) == 1
    (key, dp), = result.items()
    assert key == ("0000320193-22-000108", "10-K")
    assert dp["val"] == 15908118000


def test_best_value_by_accession_instant_merges_priority_list():
    """_best_value_by_accession_instant applies the same
    lowest-priority-first merge pattern as the duration version. Uses a
    tag not present in the fixture as the higher-priority entry to
    confirm the lower-priority fallback tag still fills in."""
    facts_json = _load_fixture("facts_instant_assets_three_years.json")
    facts = facts_json["facts"]

    result = facts_parser._best_value_by_accession_instant(
        facts, ["SomeOtherAssetsTag", "Assets"], ["10-K", "10-Q"]
    )
    assert len(result) == 1
    (key, dp), = result.items()
    assert dp["val"] == 352755000000


def test_instant_grouping_ignores_forms_not_requested():
    facts_json = _load_fixture("facts_instant_assets_three_years.json")
    facts = facts_json["facts"]

    result = facts_parser._best_value_by_accession_for_tag_instant(
        facts, "Assets", ["10-Q"]
    )
    assert result == {}
