from __future__ import annotations

import json
import re
from datetime import date, datetime, timedelta
from urllib.parse import parse_qs, urlparse

import requests
from bs4 import BeautifulSoup


SOURCE_NAME = "prh"

PRH_DYNAMIC_TITLELIST_URL = (
    "https://prhcomics.com/"
    "dynamic-titlelist/"
    "new-comics-in-stores-now/"
)

PRH_EXPECTED_AJAX_URL = (
    "https://prhcomics.com/"
    "wp/wp-admin/admin-ajax.php"
)

DEFAULT_TIMEOUT = 30
MAX_PAGES = 25

_PRODUCT_CODE_RE = re.compile(
    r"\b\d{13,17}\b"
)

_ISSUE_WORD_RE = re.compile(
    r"^(?P<series>.+?)"
    r"(?:,\s*|\s+)"
    r"Issue\s+#"
    r"(?P<issue>\d+(?:\.\d+)?(?:[A-Za-z]+)?)\b",
    re.IGNORECASE,
)

_ISSUE_HASH_RE = re.compile(
    r"^(?P<series>.+?)\s+#"
    r"(?P<issue>\d+(?:\.\d+)?(?:[A-Za-z]+)?)\b",
    re.IGNORECASE,
)

_ON_SALE_RE = re.compile(
    r"\bOn\s+sale\s+"
    r"(?P<date>"
    r"[A-Z][a-z]{2}\s+\d{1,2},\s+\d{4}"
    r")\b",
    re.IGNORECASE,
)


class PRHSourceError(RuntimeError):
    """Raised when PRH's title-list protocol cannot be consumed safely."""


def _clean(value: str | None) -> str:
    return re.sub(
        r"\s+",
        " ",
        value or "",
    ).strip()


def _week_bounds(
    weeknumber: int,
    year: int,
) -> tuple[date, date]:
    """
    Convert Comicarr's Sunday-based %U week number to a date range.

    Example:
        2026 / week 37 -> 2026-09-13 through 2026-09-19
    """

    try:
        weeknumber = int(weeknumber)
        year = int(year)
    except (TypeError, ValueError) as exc:
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
            f"invalid year/week combination: "
            f"{year}/{weeknumber}"
        ) from exc

    return (
        start,
        start + timedelta(days=6),
    )


def _text(
    node,
    selector: str,
) -> str:
    selected = node.select_one(
        selector
    )

    if selected is None:
        return ""

    return _clean(
        selected.get_text(
            " ",
            strip=True,
        )
    )


def _parse_series_issue(
    title: str,
) -> tuple[str | None, str | None]:
    title = _clean(
        title
    )

    if not title:
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
            return series, issue

    return None, None


def _parse_on_sale_date(
    row,
) -> date | None:
    nodes = row.select(
        '[data-component="prod-list-meta-onsale"]'
    )

    for node in nodes:
        value = _clean(
            node.get_text(
                " ",
                strip=True,
            )
        )

        match = _ON_SALE_RE.search(
            value
        )

        if match is None:
            continue

        try:
            return datetime.strptime(
                match.group(
                    "date"
                ),
                "%b %d, %Y",
            ).date()
        except ValueError:
            continue

    return None


def _source_id(
    row,
) -> str:
    tagged = row.select_one(
        "[data-isbn]"
    )

    if tagged is not None:
        value = _clean(
            tagged.get(
                "data-isbn"
            )
        )

        if _PRODUCT_CODE_RE.fullmatch(
            value
        ):
            return value

    isbn_node = row.select_one(
        '[data-component="prod-list-meta-isbn"]'
    )

    if isbn_node is not None:
        match = _PRODUCT_CODE_RE.search(
            _clean(
                isbn_node.get_text(
                    " ",
                    strip=True,
                )
            )
        )

        if match is not None:
            return match.group(0)

    match = _PRODUCT_CODE_RE.search(
        str(row)
    )

    if match is not None:
        return match.group(0)

    return ""


def _publisher_from_row(
    row,
) -> str:
    """
    Prefer PRH's publisher category hierarchy.

    The dynamic list's prod-list-meta-division is often an imprint/division:
      Marvel -> "Marvel Universe"

    The category hierarchy exposes the broader publisher:
      Marvel Universe -> "Marvel"
      IDW Publishing -> "IDW Publishing"
    """

    links = row.select(
        '[data-component="book-detail-meta-category-link"][href]'
    )

    for link in links:
        href = _clean(
            link.get(
                "href"
            )
        )

        text = _clean(
            link.get_text(
                " ",
                strip=True,
            )
        )

        if not href or not text:
            continue

        try:
            parsed = urlparse(
                href
            )

            cat_uri = (
                parse_qs(
                    parsed.query
                )
                .get(
                    "catUri",
                    [""],
                )[0]
            )
        except Exception:
            cat_uri = ""

        if not cat_uri.casefold().startswith(
            "all-publishers-"
        ):
            continue

        # The hierarchy normally follows:
        #   publisher -> product category ("Comics").
        # Prefer the first actual publisher label.
        if text.casefold() == "comics":
            continue

        return text

    division = _text(
        row,
        '[data-component="prod-list-meta-division"]',
    )

    return division


def _parse_product_row(
    row,
) -> dict | None:
    title = _text(
        row,
        '[data-component="prod-list-meta-title"]',
    )

    if not title:
        return None

    product_format = _text(
        row,
        '[data-component="prod-list-meta-format"]',
    )

    if product_format.casefold() != "comic book":
        return None

    source_id = _source_id(
        row
    )

    shipdate = _parse_on_sale_date(
        row
    )

    series, issue = _parse_series_issue(
        title
    )

    if (
        not source_id
        or shipdate is None
        or not series
        or not issue
    ):
        return None

    return {
        "series": series,
        "issue": issue,
        "publisher": _publisher_from_row(
            row
        ),
        "shipdate": shipdate.isoformat(),
        "source_id": source_id,
        "raw_title": title,
    }


def _parse_fragment(
    content: str,
) -> list[dict]:
    soup = BeautifulSoup(
        content or "",
        "html.parser",
    )

    releases = []

    rows = soup.select(
        "div.row.product-list-item.toast-anchor"
    )

    for row in rows:
        release = _parse_product_row(
            row
        )

        if release is not None:
            releases.append(
                release
            )

    return releases


def _required_input(
    soup,
    selector: str,
) -> str:
    node = soup.select_one(
        selector
    )

    if node is None:
        raise PRHSourceError(
            f"PRH page missing required node: {selector}"
        )

    value = node.get(
        "value"
    )

    if value is None:
        raise PRHSourceError(
            f"PRH page node has no value: {selector}"
        )

    return str(
        value
    )


def _discover_contract(
    html: str,
) -> dict:
    soup = BeautifulSoup(
        html,
        "html.parser",
    )

    ajax_url = _required_input(
        soup,
        "#titlelist-admin-url",
    )

    params_json = _required_input(
        soup,
        "#titlelist-api-params-json",
    )

    filters_json = _required_input(
        soup,
        "#prh-product-filter-input",
    )

    isbns_json = _required_input(
        soup,
        "#titlelist-isbns",
    )

    sort_node = soup.select_one(
        ".titlelist-sort .dropdown > p"
    )

    if sort_node is None:
        raise PRHSourceError(
            "PRH page missing default sort"
        )

    sort_value = _clean(
        sort_node.get(
            "value"
        )
    )

    post_type_match = re.search(
        r"var\s+postType\s*=\s*"
        r"['\"]([^'\"]+)['\"]",
        html,
    )

    post_id_match = re.search(
        r"var\s+postId\s*=\s*"
        r"['\"]?(\d+)['\"]?",
        html,
    )

    if post_type_match is None:
        raise PRHSourceError(
            "PRH page missing postType"
        )

    if post_id_match is None:
        raise PRHSourceError(
            "PRH page missing postId"
        )

    if ajax_url != PRH_EXPECTED_AJAX_URL:
        raise PRHSourceError(
            f"unexpected PRH AJAX endpoint: {ajax_url}"
        )

    try:
        json.loads(
            params_json
        )
        json.loads(
            filters_json
        )
    except json.JSONDecodeError as exc:
        raise PRHSourceError(
            "PRH page contains invalid JSON request metadata"
        ) from exc

    return {
        "ajax_url": ajax_url,
        "params": params_json,
        "filters": filters_json,
        "isbns": isbns_json,
        "post_type": post_type_match.group(1),
        "post_id": post_id_match.group(1),
        "sort": sort_value,
    }


def _response_json(
    response,
    label: str,
) -> dict:
    try:
        response.raise_for_status()
    except requests.RequestException as exc:
        raise PRHSourceError(
            f"{label} HTTP request failed"
        ) from exc

    try:
        payload = response.json()
    except ValueError as exc:
        raise PRHSourceError(
            f"{label} did not return JSON"
        ) from exc

    if not isinstance(
        payload,
        dict,
    ):
        raise PRHSourceError(
            f"{label} returned a non-object response"
        )

    return payload


def fetch_prh_weekly_releases(
    weeknumber: int,
    year: int,
    *,
    session=None,
    timeout: int = DEFAULT_TIMEOUT,
) -> list[dict]:
    """
    Fetch PRH's rolling "new comics in stores now" title list and return
    releases whose on-sale date falls inside Comicarr's requested
    Sunday-through-Saturday week.

    This adapter performs no database work and does not update Comicarr.
    """

    week_start, week_end = _week_bounds(
        weeknumber,
        year,
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
                    "PRH-Weekly-Source"
                ),
            }
        )

    try:
        page = client.get(
            PRH_DYNAMIC_TITLELIST_URL,
            timeout=timeout,
        )
    except requests.RequestException as exc:
        raise PRHSourceError(
            "PRH dynamic title-list request failed"
        ) from exc

    try:
        page.raise_for_status()
    except requests.RequestException as exc:
        raise PRHSourceError(
            "PRH dynamic title-list returned an HTTP error"
        ) from exc

    contract = _discover_contract(
        page.text
    )

    ajax_headers = {
        "Referer": PRH_DYNAMIC_TITLELIST_URL,
        "Origin": "https://prhcomics.com",
        "X-Requested-With": "XMLHttpRequest",
        "Accept": (
            "application/json,"
            "text/javascript,"
            "*/*;q=0.01"
        ),
    }

    try:
        nonce_response = client.post(
            contract[
                "ajax_url"
            ],
            data={
                "action": "get_nonce",
            },
            headers=ajax_headers,
            timeout=timeout,
        )
    except requests.RequestException as exc:
        raise PRHSourceError(
            "PRH nonce request failed"
        ) from exc

    nonce_payload = _response_json(
        nonce_response,
        "PRH nonce",
    )

    nonce = nonce_payload.get(
        "nonce"
    )

    if not nonce:
        raise PRHSourceError(
            "PRH nonce response did not contain a nonce"
        )

    base_post = {
        "product_load_nonce": nonce,
        "action": "get_product_list",
        "postType": contract[
            "post_type"
        ],
        "postId": contract[
            "post_id"
        ],
        "isbns": contract[
            "isbns"
        ],
        "params": contract[
            "params"
        ],
        "filters": contract[
            "filters"
        ],
        "layout": "list",
        "sort": contract[
            "sort"
        ],
    }

    releases = []
    release_keys = set()
    request_keys = set()

    next_start = None
    next_rows = None
    saw_product_rows = False

    for page_number in range(
        1,
        MAX_PAGES + 1,
    ):
        post_data = dict(
            base_post
        )

        if next_start is not None:
            post_data[
                "start"
            ] = next_start

        if next_rows is not None:
            post_data[
                "rows"
            ] = next_rows

        request_key = (
            post_data.get(
                "start",
                0,
            ),
            post_data.get(
                "rows",
                0,
            ),
        )

        if request_key in request_keys:
            raise PRHSourceError(
                "PRH pagination loop detected"
            )

        request_keys.add(
            request_key
        )

        try:
            response = client.post(
                contract[
                    "ajax_url"
                ],
                data=post_data,
                headers=ajax_headers,
                timeout=timeout,
            )
        except requests.RequestException as exc:
            raise PRHSourceError(
                "PRH product-list request failed"
            ) from exc

        payload = _response_json(
            response,
            (
                "PRH product-list "
                f"page {page_number}"
            ),
        )

        if payload.get(
            "success"
        ) is not True:
            raise PRHSourceError(
                "PRH product-list returned "
                f"success=false on page {page_number}"
            )

        data = payload.get(
            "data"
        )

        if not isinstance(
            data,
            dict,
        ):
            raise PRHSourceError(
                "PRH product-list response "
                "did not contain a data object"
            )

        content = (
            data.get(
                "content"
            )
            or ""
        )

        soup = BeautifulSoup(
            content,
            "html.parser",
        )

        row_count = len(
            soup.select(
                "div.row.product-list-item.toast-anchor"
            )
        )

        if row_count:
            saw_product_rows = True

        parsed = _parse_fragment(
            content
        )

        for release in parsed:
            try:
                shipdate = date.fromisoformat(
                    release[
                        "shipdate"
                    ]
                )
            except (
                KeyError,
                TypeError,
                ValueError,
            ):
                continue

            if not (
                week_start
                <= shipdate
                <= week_end
            ):
                continue

            key = (
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

            if key in release_keys:
                continue

            release_keys.add(
                key
            )

            releases.append(
                release
            )

        more = data.get(
            "more"
        )

        if isinstance(
            more,
            str,
        ):
            more = (
                more.casefold()
                == "true"
            )

        if not more:
            break

        next_start = data.get(
            "next_start_limit"
        )

        next_rows = data.get(
            "next_load_count"
        )

        if (
            next_start is None
            or next_rows is None
        ):
            raise PRHSourceError(
                "PRH pagination indicated more results "
                "without a next start/count"
            )
    else:
        raise PRHSourceError(
            f"PRH pagination exceeded {MAX_PAGES} pages"
        )

    if not saw_product_rows:
        raise PRHSourceError(
            "PRH title list returned no recognizable product rows"
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
    """Provider-friendly alias for the PRH weekly adapter."""

    return fetch_prh_weekly_releases(
        weeknumber,
        year,
        **kwargs,
    )


__all__ = [
    "PRHSourceError",
    "fetch_prh_weekly_releases",
    "get_weekly_releases",
]