#  Copyright (C) 2026 Comicarr contributors
#
#  This file is part of Comicarr.
#
#  Comicarr is free software: you can redistribute it and/or modify
#  it under the terms of the GNU General Public License as published by
#  the Free Software Foundation, either version 3 of the License, or
#  (at your option) any later version.

"""Weekly pull-list upstream outage fallback tests."""

import inspect
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import insert

import comicarr
from comicarr import db, locg, weeklypull
from comicarr.tables import metadata, weekly


@pytest.fixture
def weekly_db(tmp_path, monkeypatch):
    monkeypatch.setattr(comicarr, "DATA_DIR", str(tmp_path))
    monkeypatch.setattr(comicarr, "CONFIG", SimpleNamespace())
    monkeypatch.delenv("DATABASE_URL", raising=False)
    db.shutdown_engine()
    engine = db.get_engine()
    metadata.create_all(engine)
    yield engine
    db.shutdown_engine()


def test_weekly_pull_has_data_runs_the_real_query(weekly_db):
    with weekly_db.begin() as conn:
        conn.execute(
            insert(weekly),
            [
                {
                    "COMIC": "Cached title",
                    "ISSUE": "1",
                    "ComicID": "cached",
                    "IssueID": "cached-1",
                    "SHIPDATE": "20260827",
                    "weeknumber": "33",
                    "year": "2026",
                }
            ],
        )

    assert weeklypull._weekly_pull_has_data(33, 2026) is True
    assert weeklypull._weekly_pull_has_data("33", "2026") is True
    assert weeklypull._weekly_pull_has_data(34, 2026) is False


def test_pullit_uses_cached_week_when_upstream_fails(monkeypatch):
    config = MagicMock()
    config.ALT_PULL = 2
    config.CACHE_DIR = "/tmp"
    monkeypatch.setattr(comicarr, "CONFIG", config)
    monkeypatch.setattr(
        weeklypull.helpers,
        "weekly_info",
        lambda: {"weeknumber": 33, "year": 2026, "prev_weeknumber": 32, "prev_year": 2026},
    )
    monkeypatch.setattr(weeklypull, "_weekly_pull_has_data", lambda week, year: week == 33 and year == 2026)
    monkeypatch.setattr(weeklypull.locg, "provider_locg", lambda **kwargs: {"status": "failure"})
    new_pullcheck = MagicMock()
    monkeypatch.setattr(weeklypull, "new_pullcheck", new_pullcheck)
    monkeypatch.setattr(weeklypull.time, "sleep", lambda *_args: None)

    with patch.object(weeklypull.db, "select_one", return_value={"SHIPDATE": "20260827"}):
        result = weeklypull.pullit()

    assert result == {"status": "success"}
    new_pullcheck.assert_called_once_with(33, 2026)


def test_pullit_still_fails_without_cached_week(monkeypatch):
    config = MagicMock()
    config.ALT_PULL = 2
    config.CACHE_DIR = "/tmp"
    monkeypatch.setattr(comicarr, "CONFIG", config)
    monkeypatch.setattr(
        weeklypull.helpers,
        "weekly_info",
        lambda: {"weeknumber": 33, "year": 2026, "prev_weeknumber": 32, "prev_year": 2026},
    )
    monkeypatch.setattr(weeklypull, "_weekly_pull_has_data", lambda *_args: False)
    monkeypatch.setattr(weeklypull.locg, "provider_locg", lambda **kwargs: {"status": "failure"})
    monkeypatch.setattr(weeklypull, "new_pullcheck", MagicMock())
    monkeypatch.setattr(weeklypull.time, "sleep", lambda *_args: None)

    with patch.object(weeklypull.db, "select_one", return_value={"SHIPDATE": "20260827"}):
        result = weeklypull.pullit()

    assert result == {"status": "failure"}


def test_pullit_forwards_origin_outage_on_cached_week(monkeypatch):
    config = MagicMock()
    config.ALT_PULL = 2
    config.CACHE_DIR = "/tmp"
    monkeypatch.setattr(comicarr, "CONFIG", config)
    monkeypatch.setattr(
        weeklypull.helpers,
        "weekly_info",
        lambda: {"weeknumber": 33, "year": 2026, "prev_weeknumber": 32, "prev_year": 2026},
    )
    monkeypatch.setattr(weeklypull, "_weekly_pull_has_data", lambda week, year: week == 33 and year == 2026)
    monkeypatch.setattr(
        weeklypull.locg,
        "provider_locg",
        lambda **kwargs: {
            "status": "failure",
            "retry_after": 120,
            "origin_error": True,
            "cause": "Walksoftly is unreachable. The pull-list source is down upstream.",
        },
    )
    monkeypatch.setattr(weeklypull, "new_pullcheck", MagicMock())
    monkeypatch.setattr(weeklypull.time, "sleep", lambda *_args: None)

    with patch.object(weeklypull.db, "select_one", return_value={"SHIPDATE": "20260827"}):
        result = weeklypull.pullit()

    assert result["status"] == "success"
    assert result["retry_after"] == 120
    assert result["origin_error"] is True
    assert "Walksoftly" in result["cause"]


def test_pullit_surfaces_retry_hint_when_serving_cached_week(monkeypatch):
    config = MagicMock()
    config.ALT_PULL = 2
    config.CACHE_DIR = "/tmp"
    monkeypatch.setattr(comicarr, "CONFIG", config)
    monkeypatch.setattr(
        weeklypull.helpers,
        "weekly_info",
        lambda: {"weeknumber": 33, "year": 2026, "prev_weeknumber": 32, "prev_year": 2026},
    )
    monkeypatch.setattr(weeklypull, "_weekly_pull_has_data", lambda week, year: week == 33 and year == 2026)
    monkeypatch.setattr(
        weeklypull.locg,
        "provider_locg",
        lambda **kwargs: {"status": "failure", "retry_after": 120, "origin_error": True},
    )
    monkeypatch.setattr(weeklypull, "new_pullcheck", MagicMock())
    monkeypatch.setattr(weeklypull.time, "sleep", lambda *_args: None)

    with patch.object(weeklypull.db, "select_one", return_value={"SHIPDATE": "20260827"}):
        result = weeklypull.pullit()

    assert result == {"status": "success", "retry_after": 120, "origin_error": True}


def test_pullit_forwards_origin_outage_cause_on_failure(monkeypatch):
    config = MagicMock()
    config.ALT_PULL = 2
    config.CACHE_DIR = "/tmp"
    monkeypatch.setattr(comicarr, "CONFIG", config)
    monkeypatch.setattr(
        weeklypull.helpers,
        "weekly_info",
        lambda: {"weeknumber": 33, "year": 2026, "prev_weeknumber": 32, "prev_year": 2026},
    )
    monkeypatch.setattr(weeklypull, "_weekly_pull_has_data", lambda *_args: False)
    monkeypatch.setattr(
        weeklypull.locg,
        "provider_locg",
        lambda **kwargs: {
            "status": "failure",
            "retry_after": 120,
            "origin_error": True,
            "cause": "Walksoftly is unreachable. The pull-list source is down upstream.",
        },
    )
    monkeypatch.setattr(weeklypull, "new_pullcheck", MagicMock())
    monkeypatch.setattr(weeklypull.time, "sleep", lambda *_args: None)

    with patch.object(weeklypull.db, "select_one", return_value={"SHIPDATE": "20260827"}):
        result = weeklypull.pullit()

    assert result["status"] == "failure"
    assert result["retry_after"] == 120
    assert result["origin_error"] is True
    assert "Walksoftly" in result["cause"]
    assert "upstream" in result["cause"].lower()


def test_pullit_surfaces_retry_hint_on_failure(monkeypatch):
    config = MagicMock()
    config.ALT_PULL = 2
    config.CACHE_DIR = "/tmp"
    monkeypatch.setattr(comicarr, "CONFIG", config)
    monkeypatch.setattr(
        weeklypull.helpers,
        "weekly_info",
        lambda: {"weeknumber": 33, "year": 2026, "prev_weeknumber": 32, "prev_year": 2026},
    )
    monkeypatch.setattr(weeklypull, "_weekly_pull_has_data", lambda *_args: False)
    monkeypatch.setattr(
        weeklypull.locg,
        "provider_locg",
        lambda **kwargs: {"status": "failure", "retry_after": 120, "origin_error": True},
    )
    monkeypatch.setattr(weeklypull, "new_pullcheck", MagicMock())
    monkeypatch.setattr(weeklypull.time, "sleep", lambda *_args: None)

    with patch.object(weeklypull.db, "select_one", return_value={"SHIPDATE": "20260827"}):
        result = weeklypull.pullit()

    assert result == {"status": "failure", "retry_after": 120, "origin_error": True}


def test_pullit_drops_origin_metadata_when_later_week_fails_without_origin(monkeypatch):
    config = MagicMock()
    config.ALT_PULL = 2
    config.CACHE_DIR = "/tmp"
    monkeypatch.setattr(comicarr, "CONFIG", config)
    monkeypatch.setattr(
        weeklypull.helpers,
        "weekly_info",
        lambda: {"weeknumber": 33, "year": 2026, "prev_weeknumber": 32, "prev_year": 2026},
    )
    monkeypatch.setattr(weeklypull, "_weekly_pull_has_data", lambda *_args: False)

    def locg_by_week(**kwargs):
        if int(kwargs["weeknumber"]) == 32:
            return {
                "status": "failure",
                "retry_after": 120,
                "origin_error": True,
                "cause": "Walksoftly is unreachable. The pull-list source is down upstream.",
            }
        return {"status": "failure"}

    monkeypatch.setattr(weeklypull.locg, "provider_locg", locg_by_week)
    monkeypatch.setattr(weeklypull, "new_pullcheck", MagicMock())
    monkeypatch.setattr(weeklypull.time, "sleep", lambda *_args: None)

    with patch.object(weeklypull.db, "select_one", return_value={"SHIPDATE": "20260827"}):
        result = weeklypull.pullit()

    assert result == {"status": "failure"}
    assert "origin_error" not in result
    assert "cause" not in result
    assert "retry_after" not in result

def test_weekly_release_ingestion_preserves_legacy_key_semantics(tmp_path, monkeypatch):
    """Weekly ingestion must work without UNIQUE(ComicID, IssueID)."""
    monkeypatch.setattr(comicarr, "DATA_DIR", str(tmp_path))
    monkeypatch.setattr(comicarr, "CONFIG", SimpleNamespace())
    monkeypatch.delenv("DATABASE_URL", raising=False)

    db.shutdown_engine()
    engine = db.get_engine()

    try:
        # Legacy installed schema: rowid primary key only.
        with engine.begin() as conn:
            conn.exec_driver_sql(
                """
                CREATE TABLE weekly (
                    SHIPDATE TEXT,
                    PUBLISHER TEXT,
                    ISSUE TEXT,
                    COMIC VARCHAR(150),
                    EXTRA TEXT,
                    STATUS TEXT,
                    ComicID TEXT,
                    IssueID TEXT,
                    CV_Last_Update TEXT,
                    DynamicName TEXT,
                    weeknumber TEXT,
                    year TEXT,
                    volume TEXT,
                    seriesyear TEXT,
                    annuallink TEXT,
                    format TEXT,
                    rowid INTEGER PRIMARY KEY AUTOINCREMENT
                )
                """
            )

        control = {
            "DynamicName": "doctorstrange",
            "ISSUE": "10",
        }

        first_values = {
            "SHIPDATE": "2026-09-02",
            "PUBLISHER": "Marvel",
            "STATUS": "Skipped",
            "COMIC": "Doctor Strange",
            "ComicID": "168938",
            "IssueID": None,
            "weeknumber": "35",
            "annuallink": None,
            "year": "2026",
            "volume": "2025",
            "seriesyear": "2025",
            "format": "Print",
        }

        result = locg._upsert_weekly_release(
            control,
            first_values,
        )

        assert result == "inserted"

        with engine.connect() as conn:
            rows = conn.exec_driver_sql(
                """
                SELECT
                    rowid,
                    DynamicName,
                    ISSUE,
                    STATUS,
                    ComicID,
                    IssueID,
                    weeknumber,
                    year
                FROM weekly
                """
            ).mappings().all()

        assert len(rows) == 1

        original_rowid = rows[0]["rowid"]

        assert rows[0]["DynamicName"] == "doctorstrange"
        assert rows[0]["ISSUE"] == "10"
        assert str(rows[0]["weeknumber"]) == "35"

        # A later provider refresh for the same logical issue should
        # UPDATE that existing release, not require an SQL UNIQUE key
        # and not create a duplicate.
        second_values = {
            **first_values,
            "SHIPDATE": "2026-09-09",
            "STATUS": "Downloaded",
            "IssueID": "1234567",
            "weeknumber": "36",
        }

        result = locg._upsert_weekly_release(
            control,
            second_values,
        )

        assert result == "updated"

        with engine.connect() as conn:
            rows = conn.exec_driver_sql(
                """
                SELECT
                    rowid,
                    DynamicName,
                    ISSUE,
                    STATUS,
                    ComicID,
                    IssueID,
                    weeknumber,
                    year
                FROM weekly
                """
            ).mappings().all()

        assert len(rows) == 1

        row = rows[0]

        assert row["rowid"] == original_rowid
        assert row["DynamicName"] == "doctorstrange"
        assert row["ISSUE"] == "10"
        assert row["STATUS"] == "Downloaded"
        assert row["ComicID"] == "168938"
        assert row["IssueID"] == "1234567"
        assert str(row["weeknumber"]) == "36"
        assert str(row["year"]) == "2026"

    finally:
        db.shutdown_engine()


def test_update_weekly_row_uses_rowid_without_upsert_constraint(tmp_path, monkeypatch):
    """Existing weekly rows must update even on legacy DBs without the newer unique constraint."""
    monkeypatch.setattr(comicarr, "DATA_DIR", str(tmp_path))
    monkeypatch.setattr(comicarr, "CONFIG", SimpleNamespace())
    monkeypatch.delenv("DATABASE_URL", raising=False)

    db.shutdown_engine()
    engine = db.get_engine()

    try:
        # Deliberately mirror an older installed weekly schema:
        # rowid is the primary key, but there is NO UNIQUE(ComicID, IssueID).
        with engine.begin() as conn:
            conn.exec_driver_sql(
                """
                CREATE TABLE weekly (
                    SHIPDATE TEXT,
                    PUBLISHER TEXT,
                    ISSUE TEXT,
                    COMIC VARCHAR(150),
                    EXTRA TEXT,
                    STATUS TEXT,
                    ComicID TEXT,
                    IssueID TEXT,
                    CV_Last_Update TEXT,
                    DynamicName TEXT,
                    weeknumber TEXT,
                    year TEXT,
                    volume TEXT,
                    seriesyear TEXT,
                    annuallink TEXT,
                    format TEXT,
                    rowid INTEGER PRIMARY KEY AUTOINCREMENT
                )
                """
            )

            conn.exec_driver_sql(
                """
                INSERT INTO weekly (
                    ISSUE,
                    COMIC,
                    STATUS,
                    ComicID,
                    weeknumber,
                    year,
                    rowid
                )
                VALUES (
                    '10',
                    'Doctor Strange',
                    'Skipped',
                    '168938',
                    '36',
                    '2026',
                    317
                )
                """
            )

        weeklypull._update_weekly_row(
            317,
            {
                "ISSUE": "10",
                "COMIC": "Doctor Strange",
                "STATUS": "Downloaded",
                "ComicID": "168938",
                "weeknumber": 36,
                "year": 2026,
            },
        )

        with engine.connect() as conn:
            rows = conn.exec_driver_sql(
                """
                SELECT
                    rowid,
                    ISSUE,
                    COMIC,
                    STATUS,
                    ComicID,
                    weeknumber,
                    year
                FROM weekly
                ORDER BY rowid
                """
            ).mappings().all()

        assert len(rows) == 1

        row = rows[0]

        assert row["rowid"] == 317
        assert row["ISSUE"] == "10"
        assert row["COMIC"] == "Doctor Strange"
        assert row["STATUS"] == "Downloaded"
        assert row["ComicID"] == "168938"
        assert str(row["weeknumber"]) == "36"
        assert str(row["year"]) == "2026"

    finally:
        db.shutdown_engine()


def test_new_pullcheck_uses_canonical_weekly_key_casing():
    """Regression for the v0.38.16 weekly pull SQLAlchemy/key-casing crash."""
    source = inspect.getsource(weeklypull.new_pullcheck)

    # Raw SQL in new_pullcheck aliases this field as lowercase "issue".
    assert 'week["ISSUE"]' not in source
    assert 'week["Issue"]' not in source

    # SQLAlchemy weekly table columns use these exact names.
    # Reject these invalid weekly column names in every syntax form,
    # including both subscript assignments and dictionary literals.
    assert '"WEEKNUMBER"' not in source
    assert '"YEAR"' not in source
    assert 'newValue["Status"]' not in source

    assert 'newValue["weeknumber"]' in source
    assert 'newValue["year"]' in source
    assert 'newValue["STATUS"]' in source

    # This path updates an existing weekly row identified by rowid.
    # It must never use the generic table-identity upsert helper.
    assert 'db.upsert("weekly"' not in source
    assert '_update_weekly_row(week["rowid"], newValue)' in source



def test_locg_weekly_ingestion_uses_legacy_logical_key_helper():
    """Live pull ingestion must not use the table-level weekly UPSERT conflict target."""
    source = inspect.getsource(locg.locg)
    helper = inspect.getsource(locg._upsert_weekly_release)

    assert 'db.upsert("weekly"' not in source
    assert "_upsert_weekly_release(controlValueDict, newValueDict)" in source

    assert 'control_values["DynamicName"]' in helper
    assert 'control_values["ISSUE"]' in helper
    assert "weekly.update()" in helper
    assert "weekly.insert()" in helper


def test_provider_locg_prefers_aggregated_sources(monkeypatch):
    config = SimpleNamespace(
        IGNORED_PUBLISHERS=None,
    )
    monkeypatch.setattr(
        comicarr,
        "CONFIG",
        config,
    )

    provider_result = {
        "status": "success",
        "providers": [
            {
                "provider": "prh",
                "status": "success",
                "count": 1,
                "rejected": 0,
                "error": None,
            },
            {
                "provider": "lunar",
                "status": "success",
                "count": 1,
                "rejected": 0,
                "error": None,
            },
        ],
        "releases": [
            {
                "series": "X-Men",
                "issue": "37",
                "publisher": "Marvel",
                "shipdate": "2026-09-16",
                "source": "prh",
                "source_id": "prh-xmen-37",
                "raw_title": "X-MEN #37",
                "sources": [
                    "prh",
                ],
                "source_ids": {
                    "prh": "prh-xmen-37",
                },
            }
        ],
    }

    monkeypatch.setattr(
        locg,
        "fetch_aggregated_weekly_releases",
        lambda weeknumber, year: provider_result,
    )

    ingest = MagicMock(
        return_value={
            "status": "success",
            "count": 1,
            "weeknumber": 37,
            "year": 2026,
        }
    )

    monkeypatch.setattr(
        locg,
        "_ingest_provider_pull",
        ingest,
    )

    legacy = MagicMock(
        side_effect=AssertionError(
            "Walksoftly fallback must not run "
            "when PRH/Lunar succeed"
        )
    )

    monkeypatch.setattr(
        locg,
        "locg",
        legacy,
    )

    result = locg.provider_locg(
        weeknumber=37,
        year=2026,
    )

    assert result["status"] == "success"
    assert result["count"] == 1

    legacy.assert_not_called()
    ingest.assert_called_once()

    pull = ingest.call_args.args[0]

    assert len(pull) == 1
    assert pull[0]["series"] == "X-Men"
    assert pull[0]["issue"] == "37"
    assert pull[0]["publisher"] == "Marvel"
    assert pull[0]["shipdate"] == "2026-09-16"

    assert pull[0]["comicid"] is None
    assert pull[0]["issueid"] is None
    assert pull[0]["format"] is None
    assert pull[0]["annuallink"] is None


def test_provider_locg_falls_back_to_legacy_locg(monkeypatch):
    monkeypatch.setattr(
        locg,
        "fetch_aggregated_weekly_releases",
        lambda weeknumber, year: {
            "status": "failure",
            "providers": [
                {
                    "provider": "prh",
                    "status": "failure",
                    "count": 0,
                    "rejected": 0,
                    "error": "offline",
                },
                {
                    "provider": "lunar",
                    "status": "failure",
                    "count": 0,
                    "rejected": 0,
                    "error": "offline",
                },
            ],
            "releases": [],
        },
    )

    legacy_result = {
        "status": "failure",
        "origin_error": True,
        "cause": locg.ORIGIN_OUTAGE_CAUSE,
    }

    legacy = MagicMock(
        return_value=legacy_result
    )

    monkeypatch.setattr(
        locg,
        "locg",
        legacy,
    )

    result = locg.provider_locg(
        weeknumber=37,
        year=2026,
    )

    assert result == legacy_result

    legacy.assert_called_once_with(
        weeknumber=37,
        year=2026,
    )