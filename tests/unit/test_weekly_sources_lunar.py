from __future__ import annotations

import io
import pathlib
import unittest
import zipfile

import requests

from comicarr.weekly_sources_lunar import (
    LunarSourceError,
    _parse_series_issue,
    _record_to_release,
    _week_bounds,
    _xlsx_records,
    fetch_lunar_weekly_releases,
)


HEADERS = (
    "ProductCode",
    "Title",
    "RetailCost",
    "Publisher",
    "Description",
    "InitialOrderDue",
    "FinalOrderCutoff",
    "InstoreDate",
    "UPC",
    "ISBN",
    "EAN",
    "Writer",
    "Artist",
    "CoverArtist",
    "Mature",
    "Adult",
    "NumberOfPages",
    "Color",
    "CoverType",
    "TrimSize",
    "Rating",
)


def xml_escape(value):
    return (
        str(value)
        .replace(
            "&",
            "&amp;",
        )
        .replace(
            "<",
            "&lt;",
        )
        .replace(
            ">",
            "&gt;",
        )
        .replace(
            '"',
            "&quot;",
        )
        .replace(
            "'",
            "&apos;",
        )
    )


def column_name(number):
    result = ""

    while number:
        number, remainder = divmod(
            number - 1,
            26,
        )

        result = (
            chr(
                ord("A")
                + remainder
            )
            + result
        )

    return result


def make_xlsx(records):
    rows = [
        HEADERS,
    ]

    for record in records:
        rows.append(
            tuple(
                record.get(
                    header,
                    "",
                )
                for header
                in HEADERS
            )
        )

    sheet_rows = []

    for row_number, row in enumerate(
        rows,
        start=1,
    ):
        cells = []

        for column_number, value in enumerate(
            row,
            start=1,
        ):
            reference = (
                f"{column_name(column_number)}"
                f"{row_number}"
            )

            cells.append(
                (
                    f'<c r="{reference}" '
                    't="inlineStr">'
                    "<is><t>"
                    f"{xml_escape(value)}"
                    "</t></is>"
                    "</c>"
                )
            )

        sheet_rows.append(
            (
                f'<row r="{row_number}">'
                + "".join(
                    cells
                )
                + "</row>"
            )
        )

    worksheet = (
        '<?xml version="1.0" '
        'encoding="UTF-8" '
        'standalone="yes"?>'
        '<worksheet xmlns="'
        'http://schemas.openxmlformats.org/'
        'spreadsheetml/2006/main">'
        "<sheetData>"
        + "".join(
            sheet_rows
        )
        + "</sheetData>"
        "</worksheet>"
    )

    workbook = (
        '<?xml version="1.0" '
        'encoding="UTF-8"?>'
        '<workbook xmlns="'
        'http://schemas.openxmlformats.org/'
        'spreadsheetml/2006/main" '
        'xmlns:r="'
        'http://schemas.openxmlformats.org/'
        'officeDocument/2006/relationships">'
        "<sheets>"
        '<sheet name="Release" '
        'sheetId="1" '
        'r:id="rId1"/>'
        "</sheets>"
        "</workbook>"
    )

    relationships = (
        '<?xml version="1.0" '
        'encoding="UTF-8"?>'
        '<Relationships xmlns="'
        'http://schemas.openxmlformats.org/'
        'package/2006/relationships">'
        '<Relationship Id="rId1" '
        'Type="'
        'http://schemas.openxmlformats.org/'
        'officeDocument/2006/relationships/'
        'worksheet" '
        'Target="worksheets/sheet1.xml"/>'
        "</Relationships>"
    )

    output = io.BytesIO()

    with zipfile.ZipFile(
        output,
        "w",
        zipfile.ZIP_DEFLATED,
    ) as archive:
        archive.writestr(
            "xl/workbook.xml",
            workbook,
        )

        archive.writestr(
            "xl/_rels/"
            "workbook.xml.rels",
            relationships,
        )

        archive.writestr(
            "xl/worksheets/"
            "sheet1.xml",
            worksheet,
        )

    return output.getvalue()


def comic_record(
    *,
    code,
    title,
    publisher,
    instore,
    upc="",
    isbn="",
):
    return {
        "ProductCode": code,
        "Title": title,
        "RetailCost": "4.99",
        "Publisher": publisher,
        "Description": "Fixture",
        "InitialOrderDue": "07/23/2026",
        "FinalOrderCutoff": "08/24/2026",
        "InstoreDate": instore,
        "UPC": upc,
        "ISBN": isbn,
        "EAN": "",
        "Writer": "Writer",
        "Artist": "Artist",
        "CoverArtist": "Artist",
        "Mature": "0",
        "Adult": "0",
        "NumberOfPages": "32",
        "Color": "FC",
        "CoverType": "SOFTCOVER",
        "TrimSize": "",
        "Rating": "T",
    }


class FakeResponse:
    def __init__(
        self,
        *,
        status_code=200,
        payload=None,
        content=b"",
        text="",
    ):
        self.status_code = (
            status_code
        )
        self._payload = payload
        self.content = content
        self.text = text

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
                "no JSON"
            )

        return self._payload


class FakeSession:
    def __init__(
        self,
    ):
        self.headers = {}
        self.calls = []

        self.files = {
            "9/15/2026": (
                make_xlsx(
                    [
                        comic_record(
                            code="0726AA0001",
                            title="TEST SERIES #1 CVR A",
                            publisher="Publisher A",
                            instore="09/15/2026",
                            upc="11111111111111111",
                        ),
                        comic_record(
                            code="0726AA0002",
                            title="TEST SERIES #1 CVR B",
                            publisher="Publisher A",
                            instore="09/15/2026",
                            upc="11111111111111121",
                        ),
                        comic_record(
                            code="0726AA0003",
                            title="COLLECTION TP VOL 01",
                            publisher="Publisher A",
                            instore="09/15/2026",
                            isbn="9780000000001",
                        ),
                    ]
                )
            ),
            "9/16/2026": (
                make_xlsx(
                    [
                        comic_record(
                            code="0726BB0001",
                            title="SECOND SERIES #5 (OF 8)",
                            publisher="Publisher B",
                            instore="09/16/2026",
                            upc="22222222222200511",
                        ),
                    ]
                )
            ),
            "9/19/2026": (
                make_xlsx(
                    [
                        comic_record(
                            code="0726CC0001",
                            title=(
                                "BATMAN DAY BUNDLES OF 25 "
                                "- SAMPLE #1"
                            ),
                            publisher="Publisher C",
                            instore="09/19/2026",
                        ),
                        comic_record(
                            code="0726CC0002",
                            title="THIRD SERIES ISSUE #8",
                            publisher="Publisher C",
                            instore="09/19/2026",
                            upc="33333333333300811",
                        ),
                    ]
                )
            ),
        }

    def get(
        self,
        url,
        *,
        timeout,
        params=None,
        headers=None,
    ):
        self.calls.append(
            (
                "GET",
                url,
                params,
            )
        )

        if url.endswith(
            "/"
        ):
            return FakeResponse(
                text="<html></html>",
            )

        if (
            "/home/productdatafile"
            in url
        ):
            release = (
                params[
                    "release"
                ]
            )

            return FakeResponse(
                content=self.files[
                    release
                ],
            )

        raise AssertionError(
            f"unexpected GET {url}"
        )

    def post(
        self,
        url,
        *,
        json,
        headers,
        timeout,
    ):
        self.calls.append(
            (
                "POST",
                url,
                json,
            )
        )

        nonempty = {
            "9/15/2026",
            "9/16/2026",
            "9/19/2026",
        }

        if json not in nonempty:
            # Matches Lunar's live response for a valid date that has
            # no release products:
            #
            #     {"success": true}
            #
            return FakeResponse(
                payload={
                    "success": True,
                },
            )

        return FakeResponse(
            payload={
                "success": True,
                "current": json,
                "previous": "",
                "next": "",
                "products": [
                    {
                        "Title": (
                            "placeholder"
                        ),
                    }
                ],
            },
        )


class LunarWeeklySourceTests(
    unittest.TestCase,
):

    def test_week_bounds_are_sunday_based(
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

    def test_hash_issue_parsing(
        self,
    ):
        series, issue = (
            _parse_series_issue(
                (
                    "ABSOLUTE GREEN ARROW "
                    "#5 (OF 6) CVR A"
                )
            )
        )

        self.assertEqual(
            series,
            "ABSOLUTE GREEN ARROW",
        )

        self.assertEqual(
            issue,
            "5",
        )

    def test_issue_word_parsing(
        self,
    ):
        series, issue = (
            _parse_series_issue(
                "Far Cry ISSUE #8"
            )
        )

        self.assertEqual(
            series,
            "Far Cry",
        )

        self.assertEqual(
            issue,
            "8",
        )

    def test_collection_without_issue_is_rejected(
        self,
    ):
        self.assertEqual(
            _parse_series_issue(
                "BATMAN TP VOL 03"
            ),
            (
                None,
                None,
            ),
        )

    def test_merchandise_bundle_is_rejected(
        self,
    ):
        self.assertEqual(
            _parse_series_issue(
                (
                    "BATMAN DAY "
                    "BUNDLES OF 25 "
                    "- BATMAN #1"
                )
            ),
            (
                None,
                None,
            ),
        )

    def test_generic_bundle_quantity_is_rejected(
        self,
    ):
        self.assertEqual(
            _parse_series_issue(
                (
                    "COMIC SHOP NEWS #2039 "
                    "(BUNDLE OF 100) (NET)"
                )
            ),
            (
                None,
                None,
            ),
        )
    def test_xlsx_reader_maps_lunar_headers(
        self,
    ):
        blob = make_xlsx(
            [
                comic_record(
                    code="0726DC0047",
                    title=(
                        "ABSOLUTE "
                        "CASSANDRA CAIN "
                        "THE SHADOWS HAND #1"
                    ),
                    publisher="DC Comics",
                    instore="09/16/2026",
                    upc="76194140079200111",
                ),
            ]
        )

        records = (
            _xlsx_records(
                blob
            )
        )

        self.assertEqual(
            len(records),
            1,
        )

        self.assertEqual(
            records[0][
                "Publisher"
            ],
            "DC Comics",
        )

        self.assertEqual(
            records[0][
                "ProductCode"
            ],
            "0726DC0047",
        )

    def test_record_uses_product_code_as_source_id(
        self,
    ):
        record = comic_record(
            code="0726DC0072",
            title=(
                "ABSOLUTE "
                "GREEN ARROW #5"
            ),
            publisher="DC Comics",
            instore="09/16/2026",
            upc="76194139422000511",
        )

        release = (
            _record_to_release(
                record,
                expected_date=(
                    __import__(
                        "datetime"
                    )
                    .date(
                        2026,
                        9,
                        16,
                    )
                ),
            )
        )

        self.assertEqual(
            release[
                "source_id"
            ],
            "0726DC0072",
        )

        self.assertEqual(
            release[
                "publisher"
            ],
            "DC Comics",
        )

    def test_wrong_instore_date_is_rejected(
        self,
    ):
        from datetime import date

        record = comic_record(
            code="X",
            title="TEST #1",
            publisher="Publisher",
            instore="09/17/2026",
        )

        self.assertIsNone(
            _record_to_release(
                record,
                expected_date=date(
                    2026,
                    9,
                    16,
                ),
            )
        )

    def test_fetch_discovers_dates_and_deduplicates_variants(
        self,
    ):
        session = FakeSession()

        releases = (
            fetch_lunar_weekly_releases(
                37,
                2026,
                session=session,
            )
        )

        self.assertEqual(
            len(releases),
            3,
        )

        identities = {
            (
                item[
                    "series"
                ],
                item[
                    "issue"
                ],
                item[
                    "shipdate"
                ],
            )
            for item in releases
        }

        self.assertEqual(
            identities,
            {
                (
                    "TEST SERIES",
                    "1",
                    "2026-09-15",
                ),
                (
                    "SECOND SERIES",
                    "5",
                    "2026-09-16",
                ),
                (
                    "THIRD SERIES",
                    "8",
                    "2026-09-19",
                ),
            },
        )

    def test_fetch_queries_all_seven_days(
        self,
    ):
        session = FakeSession()

        fetch_lunar_weekly_releases(
            37,
            2026,
            session=session,
        )

        post_dates = [
            call[2]
            for call in session.calls
            if call[0]
            == "POST"
        ]

        self.assertEqual(
            post_dates,
            [
                "9/13/2026",
                "9/14/2026",
                "9/15/2026",
                "9/16/2026",
                "9/17/2026",
                "9/18/2026",
                "9/19/2026",
            ],
        )

    def test_malformed_xlsx_fails_closed(
        self,
    ):
        with self.assertRaises(
            LunarSourceError
        ):
            _xlsx_records(
                b"not-an-xlsx"
            )

    def test_source_contract_has_no_db_or_html_scraper(
        self,
    ):
        import comicarr.weekly_sources_lunar as module

        source = pathlib.Path(
            module.__file__
        ).read_text(
            encoding="utf-8"
        )

        forbidden = (
            "sqlite3",
            "db.upsert",
            "BeautifulSoup",
            "releaseimages",
            "productdetail",
        )

        for token in forbidden:
            self.assertNotIn(
                token,
                source,
            )

        self.assertIn(
            "/home/instoreproducts",
            source,
        )

        self.assertIn(
            "/home/productdatafile",
            source,
        )


if __name__ == "__main__":
    unittest.main(
        verbosity=2
    )