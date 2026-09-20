"""Tests for provider-independent Weekly Pull aggregation."""

import pytest

from comicarr.weekly_sources import (
    aggregate_weekly_sources,
    normalize_release,
)


def test_normalize_release_accepts_common_date_formats():
    release = normalize_release(
        {
            "series": "  X-Men  ",
            "issue": "#37",
            "publisher": "Marvel Universe",
            "shipdate": "09/16/2026",
            "source_id": "75960620920003711",
        },
        "prh",
    )

    assert release["series"] == "X-Men"
    assert release["issue"] == "37"
    assert release["publisher"] == "Marvel Universe"
    assert release["shipdate"] == "2026-09-16"
    assert release["source"] == "prh"


def test_provider_failure_does_not_block_successful_fallback():
    def broken(_week, _year):
        raise RuntimeError(
            "provider unavailable"
        )

    def fallback(_week, _year):
        return [
            {
                "series": "X-Men",
                "issue": "37",
                "publisher": "Marvel Universe",
                "shipdate": "2026-09-16",
            }
        ]

    result = aggregate_weekly_sources(
        [
            ("walksoftly", broken),
            ("prh", fallback),
        ],
        37,
        2026,
    )

    assert result["status"] == "success"
    assert result["count"] == 1

    assert result["providers"][0]["status"] == "failure"
    assert "provider unavailable" in result["providers"][0]["error"]

    assert result["providers"][1]["status"] == "success"


def test_duplicate_release_merges_provider_provenance():
    def prh(_week, _year):
        return [
            {
                "series": "X-Men",
                "issue": "#37",
                "publisher": "Marvel Universe",
                "shipdate": "09/16/2026",
                "source_id": "prh-37",
            }
        ]

    def comichub(_week, _year):
        return [
            {
                "series": "X MEN",
                "issue": "37",
                "publisher": None,
                "shipdate": "2026-09-16",
                "source_id": "hub-37",
                "raw_title": "X-MEN #37",
            }
        ]

    result = aggregate_weekly_sources(
        [
            ("prh", prh),
            ("comichub", comichub),
        ],
        37,
        2026,
    )

    assert result["count"] == 1

    release = result["releases"][0]

    assert release["series"] == "X-Men"
    assert release["issue"] == "37"
    assert release["publisher"] == "Marvel Universe"

    assert release["sources"] == [
        "prh",
        "comichub",
    ]

    assert release["source_ids"] == {
        "prh": "prh-37",
        "comichub": "hub-37",
    }


def test_malformed_release_is_rejected_without_losing_provider():
    def provider(_week, _year):
        return [
            {
                "series": "Valid Series",
                "issue": "5",
                "shipdate": "2026-09-16",
            },
            {
                "series": "Missing Issue",
                "shipdate": "2026-09-16",
            },
        ]

    result = aggregate_weekly_sources(
        [
            ("fixture", provider),
        ],
        37,
        2026,
    )

    assert result["status"] == "success"
    assert result["count"] == 1

    provider_state = result["providers"][0]

    assert provider_state["count"] == 1
    assert provider_state["rejected"] == 1


def test_all_failed_or_empty_sources_return_failure():
    def broken(_week, _year):
        raise ConnectionError(
            "offline"
        )

    def empty(_week, _year):
        return []

    result = aggregate_weekly_sources(
        [
            ("walksoftly", broken),
            ("lunar", empty),
        ],
        37,
        2026,
    )

    assert result["status"] == "failure"
    assert result["count"] == 0
    assert result["releases"] == []


def test_releases_are_stably_sorted():
    def provider(_week, _year):
        return [
            {
                "series": "Zeta",
                "issue": "2",
                "shipdate": "2026-09-16",
            },
            {
                "series": "Alpha",
                "issue": "1",
                "shipdate": "2026-09-16",
            },
            {
                "series": "Earlier",
                "issue": "9",
                "shipdate": "2026-09-15",
            },
        ]

    result = aggregate_weekly_sources(
        [
            ("fixture", provider),
        ],
        37,
        2026,
    )

    assert [
        release["series"]
        for release in result["releases"]
    ] == [
        "Earlier",
        "Alpha",
        "Zeta",
    ]


@pytest.mark.parametrize(
    "item",
    [
        {
            "issue": "1",
            "shipdate": "2026-09-16",
        },
        {
            "series": "X-Men",
            "shipdate": "2026-09-16",
        },
        {
            "series": "X-Men",
            "issue": "37",
            "shipdate": "not-a-date",
        },
    ],
)
def test_normalize_release_rejects_incomplete_records(item):
    with pytest.raises(ValueError):
        normalize_release(
            item,
            "fixture",
        )
