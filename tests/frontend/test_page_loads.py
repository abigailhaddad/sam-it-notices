"""Tests that the page loads correctly with data and no JS errors."""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from rfp_bundle_shards import BUNDLES_DIR, MANIFEST_NAME  # noqa: E402


def _manifest():
    return json.loads((BUNDLES_DIR / MANIFEST_NAME).read_text())


@pytest.mark.frontend
def test_no_js_errors(page, server):
    """Page should load without JavaScript errors."""
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(f"{server}/index.html")
    page.wait_for_selector("#rfpTable tbody tr", timeout=30_000)
    # Favicon 404 is expected — filter it out
    real_errors = [e for e in errors if "favicon" not in e]
    assert real_errors == [], f"JS errors: {real_errors}"


@pytest.mark.frontend
def test_header_visible(page, server):
    """Dark green header with title and subtitle should be present."""
    page.goto(f"{server}/index.html")
    header = page.locator("header.site-header")
    assert header.is_visible()
    assert "Federal Government Buys IT Services" in header.inner_text()
    subtitle = page.locator(".site-subtitle")
    assert "541511" in subtitle.inner_text() and "541512" in subtitle.inner_text()


@pytest.mark.frontend
def test_cards_show_data(page_loaded):
    """Reactive summary cards should display numbers after data loads."""
    total = page_loaded.locator("#rcTotal").inner_text()
    assert total.replace(",", "").isdigit(), f"rcTotal not a number: {total}"

    shall = page_loaded.locator("#rcShall").inner_text()
    assert "%" in shall, f"rcShall missing %: {shall}"


@pytest.mark.frontend
def test_table_has_rows(page_loaded):
    """DataTable should render at least 25 rows (default page size)."""
    rows = page_loaded.locator("#rfpTable tbody tr")
    assert rows.count() >= 25


@pytest.mark.frontend
def test_table_columns(page_loaded):
    """All expected columns should be present in the table header."""
    headers = page_loaded.locator("#rfpTable thead th").all_inner_texts()
    expected = ["Title", "Date", "Type", "Department", "NAICS",
                "Set-aside", "Labels", "Data", "SAM.gov", "Docs"]
    for col in expected:
        assert col in headers, f"Missing column: {col}"


@pytest.mark.frontend
def test_filter_bar_visible(page_loaded):
    """'No filters applied' bar and '+ Add filter' button should be visible."""
    assert page_loaded.locator("#filterBarEmpty").is_visible()
    assert page_loaded.locator("#addFilterBtn").is_visible()
    assert "No filters applied" in page_loaded.locator("#filterBarEmpty").inner_text()


@pytest.mark.frontend
def test_all_bundle_shards_are_loaded(page_loaded):
    """The table must hold every row across every shard.

    The bundle payload is split across data/rfp_bundles/shard-*.json to stay
    under the Cloudflare Pages 25 MiB per-file limit; a fetch or concat bug
    would quietly serve a truncated corpus instead of erroring.
    """
    manifest = _manifest()
    total = page_loaded.evaluate("() => $('#rfpTable').DataTable().rows().count()")
    assert total == manifest["count"], (
        f"Table has {total} rows, manifest advertises {manifest['count']}"
    )


@pytest.mark.frontend
def test_last_shard_is_full_text_searchable(page_loaded):
    """A row from the final shard must still be reachable by full-text search.

    Trailing shards are the ones a partial load would drop, and search_text
    is what the hidden _text column searches over.
    """
    manifest = _manifest()
    last_shard = json.loads((BUNDLES_DIR / manifest["shards"][-1]).read_text())
    row = next(b for b in reversed(last_shard) if b.get("solicitation_number"))

    assert page_loaded.evaluate(
        "(nid) => !!rfpDetailByNid[nid]", row["notice_id"]
    ), f"notice_id {row['notice_id']} from the last shard never reached the page"

    matched = page_loaded.evaluate(
        "(sol) => $('#rfpTable').DataTable().search(sol).draw()"
        "            .rows({search:'applied'}).count()",
        row["solicitation_number"],
    )
    assert matched >= 1, (
        f"Searching for {row['solicitation_number']!r} (last shard) matched no rows"
    )
