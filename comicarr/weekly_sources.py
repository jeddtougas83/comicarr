"""Provider-independent Weekly Pull source aggregation.

Provider adapters are deliberately kept outside the aggregation policy.
Each provider callable receives ``weeknumber`` and ``year`` and returns
an iterable of normalized-ish release dictionaries.

Required release fields:
    series
    issue
    shipdate

Optional fields:
    publisher
    source_id
    raw_title

The aggregator:
  * isolates provider failures,
  * normalizes the shared release shape,
  * merges duplicate releases from multiple sources,
  * preserves source provenance,
  * succeeds when at least one provider supplies usable releases.
"""

from __future__ import annotations

import re
from datetime import date, datetime
from typing import Callable, Iterable


Provider = tuple[str, Callable[[int, int], Iterable[dict]]]


def _clean_text(value):
    if value is None:
        return None

    cleaned = re.sub(r"\s+", " ", str(value)).strip()
    return cleaned or None


def _normalize_issue(value):
    issue = _clean_text(value)

    if issue is None:
        raise ValueError("release is missing issue")

    return issue.lstrip("#").strip()


def _normalize_shipdate(value):
    if isinstance(value, datetime):
        return value.date().isoformat()

    if isinstance(value, date):
        return value.isoformat()

    text = _clean_text(value)

    if text is None:
        raise ValueError("release is missing shipdate")

    for fmt in (
        "%Y-%m-%d",
        "%Y%m%d",
        "%m/%d/%Y",
        "%m-%d-%Y",
    ):
        try:
            return datetime.strptime(
                text,
                fmt,
            ).date().isoformat()
        except ValueError:
            continue

    raise ValueError(
        f"unsupported shipdate: {value!r}"
    )


def normalize_release(item, source):
    """Return one canonical provider-independent release record."""
    if not isinstance(item, dict):
        raise ValueError(
            "release must be a dictionary"
        )

    series = _clean_text(
        item.get("series")
    )

    if series is None:
        raise ValueError(
            "release is missing series"
        )

    source_name = _clean_text(source)

    if source_name is None:
        raise ValueError(
            "provider source name is empty"
        )

    return {
        "series": series,
        "issue": _normalize_issue(
            item.get("issue")
        ),
        "publisher": _clean_text(
            item.get("publisher")
        ),
        "shipdate": _normalize_shipdate(
            item.get("shipdate")
        ),
        "source": source_name,
        "source_id": _clean_text(
            item.get("source_id")
        ),
        "raw_title": _clean_text(
            item.get("raw_title")
        ),
    }


def _series_identity(series):
    return re.sub(
        r"[^a-z0-9]+",
        "",
        series.casefold(),
    )


def release_identity(release):
    """Identity used only for cross-provider release de-duplication."""
    return (
        _series_identity(
            release["series"]
        ),
        release["issue"].casefold(),
        release["shipdate"],
    )


def _merge_release(existing, incoming):
    merged = dict(existing)

    for field in (
        "publisher",
        "source_id",
        "raw_title",
    ):
        if (
            merged.get(field) is None
            and incoming.get(field) is not None
        ):
            merged[field] = incoming[field]

    sources = list(
        merged.get(
            "sources",
            [merged["source"]],
        )
    )

    if incoming["source"] not in sources:
        sources.append(
            incoming["source"]
        )

    merged["sources"] = sources

    source_ids = dict(
        merged.get(
            "source_ids",
            {},
        )
    )

    if incoming.get("source_id"):
        source_ids[
            incoming["source"]
        ] = incoming["source_id"]

    if merged.get("source_id"):
        source_ids.setdefault(
            merged["source"],
            merged["source_id"],
        )

    merged["source_ids"] = source_ids

    return merged


def aggregate_weekly_sources(
    providers,
    weeknumber,
    year,
):
    """Query providers independently and merge usable Weekly Pull releases.

    Provider failure is fail-soft. A failing source is recorded but does not
    prevent another provider from supplying the week.
    """
    weeknumber = int(weeknumber)
    year = int(year)

    provider_results = []
    merged = {}

    for provider_name, fetch in providers:
        status = {
            "provider": provider_name,
            "status": "failure",
            "count": 0,
            "rejected": 0,
            "error": None,
        }

        try:
            raw_items = fetch(
                weeknumber,
                year,
            )

            if raw_items is None:
                raw_items = []

            accepted = 0

            for raw in raw_items:
                try:
                    release = normalize_release(
                        raw,
                        provider_name,
                    )
                except (
                    TypeError,
                    ValueError,
                ):
                    status[
                        "rejected"
                    ] += 1
                    continue

                key = release_identity(
                    release
                )

                release["sources"] = [
                    provider_name
                ]

                release["source_ids"] = {}

                if release.get(
                    "source_id"
                ):
                    release[
                        "source_ids"
                    ][
                        provider_name
                    ] = release[
                        "source_id"
                    ]

                if key in merged:
                    merged[key] = (
                        _merge_release(
                            merged[key],
                            release,
                        )
                    )
                else:
                    merged[key] = release

                accepted += 1

            status["count"] = accepted

            if accepted > 0:
                status[
                    "status"
                ] = "success"
            else:
                status[
                    "status"
                ] = "empty"

        except Exception as exc:
            status["error"] = (
                f"{type(exc).__name__}: "
                f"{exc}"
            )

        provider_results.append(
            status
        )

    releases = sorted(
        merged.values(),
        key=lambda item: (
            item["shipdate"],
            item["series"].casefold(),
            item["issue"].casefold(),
        ),
    )

    return {
        "status": (
            "success"
            if releases
            else "failure"
        ),
        "weeknumber": weeknumber,
        "year": year,
        "count": len(releases),
        "releases": releases,
        "providers": provider_results,
    }
