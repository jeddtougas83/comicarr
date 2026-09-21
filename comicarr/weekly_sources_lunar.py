from __future__ import annotations

import io
import re
import zipfile
from datetime import date, datetime, timedelta
from xml.etree import ElementTree as ET

import requests


SOURCE_NAME = "lunar"

LUNAR_BASE_URL = (
    "https://www.lunardistribution.com"
)

LUNAR_HOME_URL = (
    LUNAR_BASE_URL + "/"
)

LUNAR_INSTORE_URL = (
    LUNAR_BASE_URL
    + "/home/instoreproducts"
)

LUNAR_DATAFILE_URL = (
    LUNAR_BASE_URL
    + "/home/productdatafile"
)

DEFAULT_TIMEOUT = 45

_MAIN_NS = (
    "http://schemas."
    "openxmlformats.org/"
    "spreadsheetml/2006/main"
)

_REL_NS = (
    "http://schemas."
    "openxmlformats.org/"
    "package/2006/"
    "relationships"
)

_DOC_REL_NS = (
    "http://schemas."
    "openxmlformats.org/"
    "officeDocument/2006/"
    "relationships"
)

_ISSUE_HASH_RE = re.compile(
    r"^(?P<series>.+?)\s+#"
    r"(?P<issue>"
    r"\d+(?:\.\d+)?(?:[A-Za-z]+)?"
    r")\b",
    re.IGNORECASE,
)

_ISSUE_WORD_RE = re.compile(
    r"^(?P<series>.+?)"
    r"(?:,\s*|\s+)"
    r"ISSUE\s+#"
    r"(?P<issue>"
    r"\d+(?:\.\d+)?(?:[A-Za-z]+)?"
    r")\b",
    re.IGNORECASE,
)

_NON_COMIC_MARKERS = (
    " REUSABLE BAG",
    " MASK",
    " MASKS ",
    " POSTER",
    " T-SHIRT",
    " T SHIRT",
    " STATUE",
    " FIGURE",
    " ACTION FIGURE",
    " PLUSH",
    " KEYCHAIN",
    " KEY CHAIN",
    " TRADING CARD",
    " PLAYMAT",
    " CARD SLEEVES",
    "BUNDLE OF",
    "BUNDLES OF",
)


class LunarSourceError(RuntimeError):
    """Raised when Lunar release data cannot be consumed safely."""


def _clean(
    value,
) -> str:
    return re.sub(
        r"\s+",
        " ",
        str(value or ""),
    ).strip()


def _week_bounds(
    weeknumber: int,
    year: int,
) -> tuple[date, date]:
    """
    Comicarr uses Sunday-based %U weeks.

    Example:
        2026 week 37 =
        2026-09-13 through 2026-09-19.
    """

    try:
        weeknumber = int(
            weeknumber
        )
        year = int(
            year
        )
    except (
        TypeError,
        ValueError,
    ) as exc:
        raise ValueError(
            "weeknumber and year must be integers"
        ) from exc

    if not 0 <= weeknumber <= 53:
        raise ValueError(
            "weeknumber must be between 0 and 53"
        )

    try:
        start = datetime.strptime(
            f"{year:04d} {weeknumber:02d} 0",
            "%Y %U %w",
        ).date()
    except ValueError as exc:
        raise ValueError(
            f"invalid year/week: "
            f"{year}/{weeknumber}"
        ) from exc

    return (
        start,
        start + timedelta(
            days=6
        ),
    )


def _format_lunar_date(
    value: date,
) -> str:
    return (
        f"{value.month}/"
        f"{value.day}/"
        f"{value.year}"
    )


def _parse_lunar_date(
    value,
) -> date | None:
    text = _clean(
        value
    )

    if not text:
        return None

    for fmt in (
        "%m/%d/%Y",
        "%m/%d/%y",
    ):
        try:
            return datetime.strptime(
                text,
                fmt,
            ).date()
        except ValueError:
            continue

    return None


def _parse_series_issue(
    title: str,
) -> tuple[str | None, str | None]:
    title = _clean(
        title
    )

    if not title:
        return None, None

    upper = title.upper()

    if any(
        marker in upper
        for marker in _NON_COMIC_MARKERS
    ):
        return None, None

    for pattern in (
        _ISSUE_WORD_RE,
        _ISSUE_HASH_RE,
    ):
        match = pattern.match(
            title
        )

        if match is None:
            continue

        series = _clean(
            match.group(
                "series"
            )
        ).rstrip(
            " ,"
        )

        issue = _clean(
            match.group(
                "issue"
            )
        )

        if series and issue:
            return (
                series,
                issue,
            )

    return None, None


def _response_json(
    response,
    label: str,
) -> dict:
    try:
        response.raise_for_status()
    except requests.RequestException as exc:
        raise LunarSourceError(
            f"{label} HTTP request failed"
        ) from exc

    try:
        payload = response.json()
    except ValueError as exc:
        raise LunarSourceError(
            f"{label} did not return JSON"
        ) from exc

    if not isinstance(
        payload,
        dict,
    ):
        raise LunarSourceError(
            f"{label} returned a non-object JSON root"
        )

    return payload


def _discover_release_dates(
    client,
    week_start: date,
    week_end: date,
    *,
    timeout: int,
) -> list[date]:
    """
    Ask Lunar about each calendar day in the requested week.

    Lunar's JSON endpoint tells us the canonical current release date and
    whether that date contains products. We use that only to discover
    release dates. Product metadata comes from Lunar's XLSX data file.
    """

    discovered = set()

    current_day = (
        week_start
    )

    while current_day <= week_end:
        request_value = (
            _format_lunar_date(
                current_day
            )
        )

        try:
            response = client.post(
                LUNAR_INSTORE_URL,
                json=request_value,
                headers={
                    "Referer": (
                        LUNAR_HOME_URL
                        + "?release="
                        + request_value
                    ),
                    "Origin": (
                        LUNAR_BASE_URL
                    ),
                    "X-Requested-With": (
                        "XMLHttpRequest"
                    ),
                },
                timeout=timeout,
            )
        except requests.RequestException as exc:
            raise LunarSourceError(
                "Lunar in-store date discovery failed"
            ) from exc

        payload = _response_json(
            response,
            (
                "Lunar in-store products "
                f"{request_value}"
            ),
        )

        if payload.get(
            "success"
        ) is not True:
            raise LunarSourceError(
                "Lunar in-store endpoint "
                f"returned success=false for {request_value}"
            )

        products = payload.get(
            "products"
        )

        # Lunar's live behavior for a valid non-release day is:
        #
        #     {"success": true}
        #
        # with no products/current/previous/next fields. Treat that
        # specific absence as an empty release day. Any other non-list
        # products value remains an unexpected schema and fails closed.
        if products is None:
            products = []
        elif not isinstance(
            products,
            list,
        ):
            raise LunarSourceError(
                "Lunar in-store endpoint "
                "returned products as a non-list"
            )

        canonical_date = (
            _parse_lunar_date(
                payload.get(
                    "current"
                )
            )
        )

        if (
            canonical_date is not None
            and week_start
            <= canonical_date
            <= week_end
            and len(
                products
            )
            > 0
        ):
            discovered.add(
                canonical_date
            )

        current_day += timedelta(
            days=1
        )

    return sorted(
        discovered
    )


def _column_number(
    letters: str,
) -> int:
    result = 0

    for char in letters:
        result = (
            result * 26
            + ord(
                char.upper()
            )
            - ord(
                "A"
            )
            + 1
        )

    return result


def _xlsx_first_sheet_rows(
    blob: bytes,
) -> list[list[str]]:
    """
    Read the first XLSX worksheet using only the Python standard library.

    Lunar's product data files are ordinary XLSX ZIP containers.
    """

    if not blob.startswith(
        b"PK\x03\x04"
    ):
        raise LunarSourceError(
            "Lunar product data file is not an XLSX ZIP"
        )

    try:
        archive = zipfile.ZipFile(
            io.BytesIO(
                blob
            )
        )
    except zipfile.BadZipFile as exc:
        raise LunarSourceError(
            "Lunar product data file is not a valid ZIP"
        ) from exc

    with archive:
        names = set(
            archive.namelist()
        )

        required = {
            "xl/workbook.xml",
            "xl/_rels/workbook.xml.rels",
        }

        if not required.issubset(
            names
        ):
            raise LunarSourceError(
                "Lunar XLSX is missing workbook metadata"
            )

        shared_strings = []

        if (
            "xl/sharedStrings.xml"
            in names
        ):
            root = ET.fromstring(
                archive.read(
                    "xl/sharedStrings.xml"
                )
            )

            ns = {
                "x": _MAIN_NS,
            }

            for item in root.findall(
                "x:si",
                ns,
            ):
                shared_strings.append(
                    "".join(
                        node.text
                        or ""
                        for node
                        in item.findall(
                            ".//x:t",
                            ns,
                        )
                    )
                )

        workbook = ET.fromstring(
            archive.read(
                "xl/workbook.xml"
            )
        )

        workbook_rels = (
            ET.fromstring(
                archive.read(
                    "xl/_rels/"
                    "workbook.xml.rels"
                )
            )
        )

        rels = {}

        for relation in (
            workbook_rels.findall(
                f"{{{_REL_NS}}}Relationship"
            )
        ):
            rels[
                relation.attrib[
                    "Id"
                ]
            ] = relation.attrib[
                "Target"
            ]

        sheets = workbook.find(
            f"{{{_MAIN_NS}}}sheets"
        )

        if sheets is None:
            raise LunarSourceError(
                "Lunar XLSX has no sheets"
            )

        first_sheet = sheets.find(
            f"{{{_MAIN_NS}}}sheet"
        )

        if first_sheet is None:
            raise LunarSourceError(
                "Lunar XLSX has no first worksheet"
            )

        relationship_id = (
            first_sheet.attrib.get(
                f"{{{_DOC_REL_NS}}}id"
            )
        )

        target = rels.get(
            relationship_id
        )

        if not target:
            raise LunarSourceError(
                "Lunar XLSX worksheet relationship missing"
            )

        if target.startswith(
            "/"
        ):
            sheet_path = (
                target.lstrip(
                    "/"
                )
            )
        elif target.startswith(
            "xl/"
        ):
            sheet_path = (
                target
            )
        else:
            sheet_path = (
                "xl/"
                + target.lstrip(
                    "/"
                )
            )

        if sheet_path not in names:
            raise LunarSourceError(
                "Lunar XLSX first worksheet is missing"
            )

        sheet = ET.fromstring(
            archive.read(
                sheet_path
            )
        )

        rows = []

        for row in sheet.findall(
            (
                f".//{{{_MAIN_NS}}}"
                "sheetData/"
                f"{{{_MAIN_NS}}}row"
            )
        ):
            cells = {}

            for cell in row.findall(
                f"{{{_MAIN_NS}}}c"
            ):
                reference = (
                    cell.attrib.get(
                        "r",
                        "",
                    )
                )

                match = re.match(
                    r"([A-Z]+)",
                    reference,
                )

                if match is None:
                    continue

                column = (
                    _column_number(
                        match.group(1)
                    )
                )

                cell_type = (
                    cell.attrib.get(
                        "t"
                    )
                )

                value = ""

                if (
                    cell_type
                    == "inlineStr"
                ):
                    inline = cell.find(
                        f"{{{_MAIN_NS}}}is"
                    )

                    if inline is not None:
                        value = "".join(
                            node.text
                            or ""
                            for node
                            in inline.findall(
                                f".//{{{_MAIN_NS}}}t"
                            )
                        )
                else:
                    node = cell.find(
                        f"{{{_MAIN_NS}}}v"
                    )

                    if node is not None:
                        raw = (
                            node.text
                            or ""
                        )

                        if (
                            cell_type
                            == "s"
                            and raw.isdigit()
                        ):
                            index = int(
                                raw
                            )

                            if (
                                0
                                <= index
                                < len(
                                    shared_strings
                                )
                            ):
                                value = (
                                    shared_strings[
                                        index
                                    ]
                                )
                            else:
                                value = raw
                        else:
                            value = raw

                cells[
                    column
                ] = _clean(
                    value
                )

            if cells:
                width = max(
                    cells
                )

                rows.append(
                    [
                        cells.get(
                            column,
                            "",
                        )
                        for column
                        in range(
                            1,
                            width + 1,
                        )
                    ]
                )

        return rows


def _xlsx_records(
    blob: bytes,
) -> list[dict]:
    rows = _xlsx_first_sheet_rows(
        blob
    )

    if not rows:
        raise LunarSourceError(
            "Lunar XLSX contains no rows"
        )

    raw_headers = rows[0]

    headers = [
        _clean(
            header
        )
        for header
        in raw_headers
    ]

    required = {
        "ProductCode",
        "Title",
        "Publisher",
        "InstoreDate",
        "UPC",
        "ISBN",
    }

    available = {
        header
        for header in headers
        if header
    }

    missing = (
        required
        - available
    )

    if missing:
        raise LunarSourceError(
            "Lunar XLSX missing required columns: "
            + ", ".join(
                sorted(
                    missing
                )
            )
        )

    records = []

    for row in rows[1:]:
        record = {}

        for index, header in enumerate(
            headers
        ):
            if not header:
                continue

            value = (
                row[index]
                if index
                < len(
                    row
                )
                else ""
            )

            record[
                header
            ] = _clean(
                value
            )

        if any(
            record.values()
        ):
            records.append(
                record
            )

    return records


def _download_release_records(
    client,
    release_date: date,
    *,
    timeout: int,
) -> list[dict]:
    release_value = (
        _format_lunar_date(
            release_date
        )
    )

    try:
        response = client.get(
            LUNAR_DATAFILE_URL,
            params={
                "release": (
                    release_value
                ),
            },
            headers={
                "Referer": (
                    LUNAR_HOME_URL
                    + "?release="
                    + release_value
                ),
            },
            timeout=timeout,
        )
    except requests.RequestException as exc:
        raise LunarSourceError(
            "Lunar product data file request failed"
        ) from exc

    try:
        response.raise_for_status()
    except requests.RequestException as exc:
        raise LunarSourceError(
            "Lunar product data file returned "
            "an HTTP error"
        ) from exc

    return _xlsx_records(
        response.content
    )


def _record_to_release(
    record: dict,
    *,
    expected_date: date,
) -> dict | None:
    title = _clean(
        record.get(
            "Title"
        )
    )

    series, issue = (
        _parse_series_issue(
            title
        )
    )

    if (
        not series
        or not issue
    ):
        return None

    shipdate = (
        _parse_lunar_date(
            record.get(
                "InstoreDate"
            )
        )
    )

    if shipdate != expected_date:
        return None

    publisher = _clean(
        record.get(
            "Publisher"
        )
    )

    source_id = (
        _clean(
            record.get(
                "ProductCode"
            )
        )
        or _clean(
            record.get(
                "UPC"
            )
        )
        or _clean(
            record.get(
                "ISBN"
            )
        )
        or _clean(
            record.get(
                "EAN"
            )
        )
    )

    if (
        not publisher
        or not source_id
    ):
        return None

    return {
        "series": series,
        "issue": issue,
        "publisher": publisher,
        "shipdate": (
            shipdate.isoformat()
        ),
        "source_id": source_id,
        "raw_title": title,
    }


def fetch_lunar_weekly_releases(
    weeknumber: int,
    year: int,
    *,
    session=None,
    timeout: int = DEFAULT_TIMEOUT,
) -> list[dict]:
    """
    Fetch Lunar's releases for Comicarr's requested Sunday-Saturday week.

    Protocol:
      1. Probe Lunar's structured instoreproducts endpoint for each day.
      2. Identify actual release dates in the requested week.
      3. Download Lunar's official XLSX data file for each release date.
      4. Keep issue-bearing comic releases.
      5. Collapse variant covers by series + issue + ship date.

    This module performs no Comicarr database work.
    """

    week_start, week_end = (
        _week_bounds(
            weeknumber,
            year,
        )
    )

    client = (
        session
        if session is not None
        else requests.Session()
    )

    if hasattr(
        client,
        "headers",
    ):
        client.headers.update(
            {
                "User-Agent": (
                    "Comicarr/"
                    "Lunar-Weekly-Source"
                ),
            }
        )

    # Match the validated browser/session flow before structured requests.
    try:
        home = client.get(
            LUNAR_HOME_URL,
            timeout=timeout,
        )

        home.raise_for_status()
    except requests.RequestException as exc:
        raise LunarSourceError(
            "Lunar homepage request failed"
        ) from exc

    release_dates = (
        _discover_release_dates(
            client,
            week_start,
            week_end,
            timeout=timeout,
        )
    )

    releases = []
    seen = set()

    for release_date in (
        release_dates
    ):
        records = (
            _download_release_records(
                client,
                release_date,
                timeout=timeout,
            )
        )

        for record in records:
            release = (
                _record_to_release(
                    record,
                    expected_date=(
                        release_date
                    ),
                )
            )

            if release is None:
                continue

            shipdate = (
                date.fromisoformat(
                    release[
                        "shipdate"
                    ]
                )
            )

            if not (
                week_start
                <= shipdate
                <= week_end
            ):
                continue

            identity = (
                release[
                    "series"
                ].casefold(),
                release[
                    "issue"
                ].casefold(),
                release[
                    "shipdate"
                ],
            )

            if identity in seen:
                continue

            seen.add(
                identity
            )

            releases.append(
                release
            )

    releases.sort(
        key=lambda item: (
            item[
                "shipdate"
            ],
            item[
                "series"
            ].casefold(),
            item[
                "issue"
            ].casefold(),
            item[
                "source_id"
            ],
        )
    )

    return releases


def get_weekly_releases(
    weeknumber: int,
    year: int,
    **kwargs,
) -> list[dict]:
    """Provider-friendly alias."""

    return fetch_lunar_weekly_releases(
        weeknumber,
        year,
        **kwargs,
    )


__all__ = [
    "LunarSourceError",
    "fetch_lunar_weekly_releases",
    "get_weekly_releases",
]