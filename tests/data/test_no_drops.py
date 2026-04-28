"""Verify no previously-seen RFP bundles have been dropped.

Loads baseline.json (committed) and checks every notice_id still exists.
Run tests/data/update_baseline.py after intentional reductions to reset.
"""
import json
import pytest
from pathlib import Path

BASELINE_PATH = Path(__file__).parent / "baseline.json"


def load_baseline():
    if not BASELINE_PATH.exists():
        pytest.skip("No baseline.json yet — run tests/data/update_baseline.py")
    return json.loads(BASELINE_PATH.read_text())


def test_no_rfp_bundles_dropped():
    baseline = load_baseline()
    if "rfp_bundles" not in baseline:
        pytest.skip("No rfp_bundles baseline")

    rfp_path = Path("web/data/rfp_bundles.json")
    if not rfp_path.exists():
        pytest.skip("rfp_bundles.json not available")

    current_ids = {b["notice_id"] for b in json.loads(rfp_path.read_text()) if b.get("notice_id")}
    baseline_ids = set(baseline["rfp_bundles"]["notice_ids"])
    dropped = baseline_ids - current_ids
    assert not dropped, (
        f"CRITICAL: {len(dropped)} bundles dropped from baseline.\n"
        f"Examples: {sorted(dropped)[:5]}\n"
        f"If intentional, re-run tests/data/update_baseline.py"
    )
