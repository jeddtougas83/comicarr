from __future__ import annotations

import pathlib
import unittest

import requests
from bs4 import BeautifulSoup

from comicarr.weekly_sources_prh import (
    PRHSourceError,
    _parse_product_row,
    _parse_series_issue,
    _publisher_from_row,
    _week_bounds,
    fetch_prh_weekly_releases,
)


AJAX_URL = (
    "https://prhcomics.com/"
    "wp/wp-admin/admin-ajax.php"
)


def dynamic_page():
    return f"""
    <html>
      <body>
        <input
          id="titlelist-admin-url"
          value="{AJAX_URL}"
        />
        <input
          id="titlelist-api-params-json"
          value='{{"catSetId":"CM"}}'
        />
        <input
          id="prh-product-filter-input"
          value='{{"l1_category":"","filters":{{"format":[]}}}}'
        />
        <input
          id="titlelist-isbns"
          value='[]'
        />

        <div class="titlelist-sort">
          <div class="dropdown">
            <p value="default:asc"></p>
          </div>
        </div>

        <script>
          var postType = "dynamic-titlelist";
          var postId = 14517;
        </script>
      </body>
    </html>
    """


def product_row(
    *,
    source_id,
    title,
    sale_date,
    publisher,
    division,
    product_format="Comic Book",
):
    publisher_slug = (
        publisher
        .casefold()
        .replace(" ", "-")
        .replace("!", "")
    )

    return f"""
    <div class="row product-list-item toast-anchor">
      <div class="list-view-grid">

        <div class="below-cover-wrapper">
          <button
            data-format-code="CB"
            data-isbn="{source_id}">
          </button>
        </div>

        <div class="titlelist-meta">

          <div
            class="titlelist-meta-title"
            data-component="prod-list-meta-title">
            <a>{title}</a>
          </div>

          <div
            class="titlelist-meta-isbn"
            data-component="prod-list-meta-isbn">
            {source_id}
          </div>

          <div
            class="titlelist-meta-division meta90"
            data-component="prod-list-meta-description">
            This is a description and must never become publisher.
          </div>

          <div
            class="titlelist-meta-onsale"
            data-component="prod-list-meta-onsale">
            On sale {sale_date}
          </div>

          <div
            class="titlelist-meta-onsale"
            data-component="prod-list-meta-onsale">
            FOC Aug 10, 2026
          </div>

          <div
            class="titlelist-meta-format"
            data-component="prod-list-meta-format">
            {product_format}
          </div>

          <div
            class="titlelist-meta-division meta91"
            data-component="prod-list-meta-division">
            {division}
          </div>

          <div
            class="titlelist-meta-category-hierarchy"
            data-component="prod-list-meta-category-hierarchy">

            <a
              data-component="book-detail-meta-category-link"
              href="https://prhcomics.com/themes/?catUri=all-categories-comics">
              Comics
            </a>

            <a
              data-component="book-detail-meta-category-link"
              href="https://prhcomics.com/themes/?catUri=all-publishers-{publisher_slug}">
              {publisher}
            </a>

            <a
              data-component="book-detail-meta-category-link"
              href="https://prhcomics.com/themes/?catUri=all-publishers-{publisher_slug}-comics">
              Comics
            </a>

          </div>
        </div>
      </div>
    </div>
    """


class FakeResponse:
    def __init__(
        self,
        *,
        status_code=200,
        text="",
        payload=None,
    ):
        self.status_code = status_code
        self.text = text
        self.content = text.encode(
            "utf-8"
        )
        self._payload = payload

    def raise_for_status(
        self,
    ):
        if self.status_code >= 400:
            raise requests.HTTPError(
                f"HTTP {self.status_code}"
            )

    def json(
        self,
    ):
        if self._payload is None:
            raise ValueError(
                "no JSON payload"
            )

        return self._payload


class FakeSession:
    def __init__(
        self,
        *,
        missing_nonce=False,
    ):
        self.headers = {}
        self.missing_nonce = (
            missing_nonce
        )
        self.calls = []

    def get(
        self,
        url,
        *,
        timeout,
    ):
        self.calls.append(
            (
                "GET",
                url,
                None,
            )
        )

        return FakeResponse(
            text=dynamic_page()
        )

    def post(
        self,
        url,
        *,
        data,
        headers,
        timeout,
    ):
        self.calls.append(
            (
                "POST",
                url,
                dict(data),
            )
        )

        if (
            data.get(
                "action"
            )
            == "get_nonce"
        ):
            payload = (
                {}
                if self.missing_nonce
                else {
                    "nonce": "1234567890"
                }
            )

            return FakeResponse(
                payload=payload
            )

        if (
            data.get(
                "action"
            )
            != "get_product_list"
        ):
            raise AssertionError(
                "unexpected POST action"
            )

        start = int(
            data.get(
                "start",
                0,
            )
        )

        if start == 0:
            content = (
                product_row(
                    source_id="75960621282800711",
                    title="DAREDEVIL #7",
                    sale_date="Sep 16, 2026",
                    publisher="Marvel",
                    division="Marvel Universe",
                )
                +
                product_row(
                    source_id="82771403315102211",
                    title=(
                        "Teenage Mutant Ninja Turtles "
                        "#22 Cover A (Pe)"
                    ),
                    sale_date="Sep 16, 2026",
                    publisher="IDW Publishing",
                    division="IDW Publishing",
                )
            )

            return FakeResponse(
                payload={
                    "success": True,
                    "data": {
                        "content": content,
                        "total": 4,
                        "more": True,
                        "next_start_limit": 2,
                        "next_load_count": 2,
                    },
                }
            )

        if start == 2:
            content = (
                product_row(
                    source_id="75960621282800712",
                    title=(
                        "DAREDEVIL #7 "
                        "VARIANT COVER"
                    ),
                    sale_date="Sep 16, 2026",
                    publisher="Marvel",
                    division="Marvel Universe",
                )
                +
                product_row(
                    source_id="75960620841802811",
                    title="WOLVERINE #28",
                    sale_date="Sep 23, 2026",
                    publisher="Marvel",
                    division="Marvel Universe",
                )
            )

            return FakeResponse(
                payload={
                    "success": True,
                    "data": {
                        "content": content,
                        "total": 4,
                        "more": False,
                    },
                }
            )

        raise AssertionError(
            f"unexpected pagination start={start}"
        )


class PRHWeeklySourceTests(
    unittest.TestCase,
):

    def test_week_bounds_use_comicarr_sunday_week(
        self,
    ):
        start, end = _week_bounds(
            37,
            2026,
        )

        self.assertEqual(
            start.isoformat(),
            "2026-09-13",
        )

        self.assertEqual(
            end.isoformat(),
            "2026-09-19",
        )

    def test_plain_hash_issue_parsing(
        self,
    ):
        series, issue = _parse_series_issue(
            "X-MEN: OUTBACK #4"
        )

        self.assertEqual(
            series,
            "X-MEN: OUTBACK",
        )

        self.assertEqual(
            issue,
            "4",
        )

    def test_issue_word_form_parsing(
        self,
    ):
        series, issue = _parse_series_issue(
            "Far Cry: Cull the Herd, Issue #8"
        )

        self.assertEqual(
            series,
            "Far Cry: Cull the Herd",
        )

        self.assertEqual(
            issue,
            "8",
        )

    def test_publisher_prefers_category_hierarchy(
        self,
    ):
        soup = BeautifulSoup(
            product_row(
                source_id="75960621282800711",
                title="DAREDEVIL #7",
                sale_date="Sep 16, 2026",
                publisher="Marvel",
                division="Marvel Universe",
            ),
            "html.parser",
        )

        row = soup.select_one(
            "div.row.product-list-item.toast-anchor"
        )

        self.assertEqual(
            _publisher_from_row(
                row
            ),
            "Marvel",
        )

    def test_publisher_falls_back_to_division(
        self,
    ):
        html = """
        <div class="row product-list-item toast-anchor">
          <div
            data-component="prod-list-meta-division">
            BOOM! Studios
          </div>
        </div>
        """

        soup = BeautifulSoup(
            html,
            "html.parser",
        )

        row = soup.select_one(
            "div.row.product-list-item.toast-anchor"
        )

        self.assertEqual(
            _publisher_from_row(
                row
            ),
            "BOOM! Studios",
        )

    def test_description_is_not_publisher(
        self,
    ):
        soup = BeautifulSoup(
            product_row(
                source_id="75960621282800711",
                title="DAREDEVIL #7",
                sale_date="Sep 16, 2026",
                publisher="Marvel",
                division="Marvel Universe",
            ),
            "html.parser",
        )

        row = soup.select_one(
            "div.row.product-list-item.toast-anchor"
        )

        release = _parse_product_row(
            row
        )

        self.assertEqual(
            release[
                "publisher"
            ],
            "Marvel",
        )

        self.assertNotIn(
            "description",
            release[
                "publisher"
            ].casefold(),
        )

    def test_non_comic_format_is_rejected(
        self,
    ):
        soup = BeautifulSoup(
            product_row(
                source_id="9780000000001",
                title="TEST SERIES #1",
                sale_date="Sep 16, 2026",
                publisher="Test Publisher",
                division="Test Division",
                product_format="Hardcover",
            ),
            "html.parser",
        )

        row = soup.select_one(
            "div.row.product-list-item.toast-anchor"
        )

        self.assertIsNone(
            _parse_product_row(
                row
            )
        )

    def test_fetch_filters_requested_week_and_deduplicates_variants(
        self,
    ):
        releases = (
            fetch_prh_weekly_releases(
                37,
                2026,
                session=FakeSession(),
            )
        )

        self.assertEqual(
            len(releases),
            2,
        )

        titles = {
            item[
                "raw_title"
            ]
            for item in releases
        }

        self.assertIn(
            "DAREDEVIL #7",
            titles,
        )

        self.assertIn(
            (
                "Teenage Mutant Ninja Turtles "
                "#22 Cover A (Pe)"
            ),
            titles,
        )

        self.assertTrue(
            all(
                item[
                    "shipdate"
                ]
                == "2026-09-16"
                for item in releases
            )
        )

        publishers = {
            item[
                "publisher"
            ]
            for item in releases
        }

        self.assertEqual(
            publishers,
            {
                "Marvel",
                "IDW Publishing",
            },
        )

    def test_fetch_week_38_gets_sep_23(
        self,
    ):
        releases = (
            fetch_prh_weekly_releases(
                38,
                2026,
                session=FakeSession(),
            )
        )

        self.assertEqual(
            len(releases),
            1,
        )

        self.assertEqual(
            releases[0][
                "series"
            ],
            "WOLVERINE",
        )

        self.assertEqual(
            releases[0][
                "issue"
            ],
            "28",
        )

        self.assertEqual(
            releases[0][
                "shipdate"
            ],
            "2026-09-23",
        )

    def test_missing_nonce_fails_closed(
        self,
    ):
        with self.assertRaises(
            PRHSourceError
        ):
            fetch_prh_weekly_releases(
                37,
                2026,
                session=FakeSession(
                    missing_nonce=True
                ),
            )

    def test_source_contract_has_no_db_or_legacy_scraper(
        self,
    ):
        import comicarr.weekly_sources_prh as module

        source = pathlib.Path(
            module.__file__
        ).read_text(
            encoding="utf-8"
        )

        forbidden = (
            "sqlite3",
            "db.upsert",
            "catalog-landing-page",
            "api.penguinrandomhouse.com",
            '"/comics/"',
            "'/comics/'",
        )

        for token in forbidden:
            self.assertNotIn(
                token,
                source,
            )

        self.assertEqual(
            module.PRH_DYNAMIC_TITLELIST_URL,
            (
                "https://prhcomics.com/"
                "dynamic-titlelist/"
                "new-comics-in-stores-now/"
            ),
        )

        self.assertIn(
            'action": "get_product_list"',
            source,
        )

        self.assertIn(
            '"layout": "list"',
            source,
        )


if __name__ == "__main__":
    unittest.main(
        verbosity=2
    )