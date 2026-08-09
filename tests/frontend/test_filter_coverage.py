"""Every selectable filter option must yield >0 rows.

Regression test for the bug where `searchable: c.searchable || false` made
every column unsearchable, so every filter silently filtered everything out.

Strategy: enumerate every (field, value) pair that the filter UI offers for
each multiselect field, apply it via the URL (?field=value), and assert the
table draws at least one row.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from rfp_bundle_shards import load_bundles as _load_bundles  # noqa: E402


def _get_sa(b):
    sa = b.get("set_aside")
    if isinstance(sa, str):
        return sa
    if isinstance(sa, dict):
        return sa.get("description", "") or ""
    return b.get("sa", "") or ""


def _multiselect_pairs():
    """Build (url_key, value, label) tuples for every option the modal exposes.

    Mirrors the logic in `openFilterStep2` for `staticOptions` / `getVals`.
    """
    bundles = _load_bundles()
    pairs = []

    def uniq(values):
        return sorted({v for v in values if v and isinstance(v, str)})

    for v in uniq(b.get("type") for b in bundles):
        pairs.append(("type", v, f"type={v}"))
    for v in uniq(b.get("department") or b.get("dept") for b in bundles):
        pairs.append(("dept", v, f"dept={v}"))
    for v in uniq(b.get("naics") for b in bundles):
        pairs.append(("naics", v, f"naics={v}"))
    for v in uniq(_get_sa(b) for b in bundles):
        pairs.append(("sa", v, f"sa={v}"))

    # Label filter — static options mapped to internal chip keys
    for chip in ["shall", "user", "agile", "RTM"]:
        pairs.append(("label", chip, f"label={chip}"))

    # Data availability — only include options that actually appear in bundles
    has_personnel = any(b.get("personnel") for b in bundles)
    has_label_hits = any(b.get("label_hits") for b in bundles)
    has_full_text = any(
        (b.get("search_text") or "").strip()
        and len((b.get("search_text") or "").strip()) > 300
        and not (b.get("search_text") or "").strip().startswith("http")
        for b in bundles
    )
    has_lcats = any(b.get("lcats") for b in bundles)
    if has_personnel:
        pairs.append(("data", "Personnel roles", "data=Personnel roles"))
    if has_label_hits:
        pairs.append(("data", "Vocabulary signals", "data=Vocabulary signals"))
    if has_full_text:
        pairs.append(("data", "Full text", "data=Full text"))
    if has_lcats:
        pairs.append(("data", "LCATs", "data=LCATs"))

    return pairs


PAIRS = _multiselect_pairs()


@pytest.mark.frontend
@pytest.mark.parametrize("field,value,label", PAIRS, ids=[p[2] for p in PAIRS])
def test_filter_option_returns_rows(page, server, field, value, label):
    """Every filter option offered by the UI must match at least one row."""
    from urllib.parse import quote
    url = f"{server}/index.html?{field}={quote(value)}"
    page.goto(url)
    page.wait_for_selector("#rfpTable tbody tr", timeout=30_000)

    applied = page.evaluate(
        "() => $('#rfpTable').DataTable().rows({search:'applied'}).count()"
    )
    assert applied > 0, f"Filter {label!r} matched 0 rows"


@pytest.mark.frontend
def test_filter_pairs_nonempty():
    """Sanity check that we actually generated filter pairs to test."""
    assert len(PAIRS) > 10, f"Expected many filter pairs, got {len(PAIRS)}"
