"""Verify no previously-seen RFP bundles have been dropped.

Loads baseline.json (committed) and checks every notice_id still exists.
Run tests/data/update_baseline.py after intentional reductions to reset.
"""
import json
import sys
import pytest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from rfp_bundle_shards import BUNDLES_DIR, MANIFEST_NAME, load_bundles  # noqa: E402

BASELINE_PATH = Path(__file__).parent / "baseline.json"


def load_baseline():
    if not BASELINE_PATH.exists():
        pytest.skip("No baseline.json yet — run tests/data/update_baseline.py")
    return json.loads(BASELINE_PATH.read_text())


def test_no_rfp_bundles_dropped():
    """A bundle dropping out is fine when it was rotated by a newer
    repost of the same solicitation_number — build_rfp_signals.py dedups
    by sol# and keeps the latest. Only fail when a bundle disappears
    AND its solicitation_number is gone too.
    """
    baseline = load_baseline()
    if "rfp_bundles" not in baseline:
        pytest.skip("No rfp_bundles baseline")

    if not (BUNDLES_DIR / MANIFEST_NAME).exists():
        pytest.skip("rfp_bundles shards not available")

    current = load_bundles()
    current_ids  = {b["notice_id"] for b in current if b.get("notice_id")}
    current_sols = {(b.get("solicitation_number") or "").strip()
                    for b in current if b.get("solicitation_number")}

    baseline_ids = set(baseline["rfp_bundles"]["notice_ids"])
    # Optional sol# map (added by update_baseline.py); older baselines may lack it
    nid_to_sol = baseline["rfp_bundles"].get("notice_id_to_sol", {})

    really_dropped = []
    for nid in baseline_ids - current_ids:
        sol = (nid_to_sol.get(nid) or "").strip()
        if sol and sol in current_sols:
            continue  # rotated to a newer noticeId for the same sol#
        really_dropped.append(nid)

    assert not really_dropped, (
        f"CRITICAL: {len(really_dropped)} bundles dropped from baseline "
        f"AND their solicitation_number is also gone.\n"
        f"Examples: {sorted(really_dropped)[:5]}\n"
        f"If intentional, re-run tests/data/update_baseline.py"
    )
