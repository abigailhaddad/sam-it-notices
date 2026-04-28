"""Data integrity tests for web/data/ JSON files — notice browser only."""
import json
from pathlib import Path

DATA = Path("web/data")
# Mirror DEFAULT_NAICS_PREFIXES from rfp_text_pipeline.py.
NAICS_VALID = {
    "541511", "541512", "541513", "541519", "518210",
    "541330", "541611", "541618", "541690", "541715", "541990",
}


def load(name):
    return json.loads((DATA / name).read_text())


def test_required_files_exist():
    for f in ["rfp_signals.json", "rfp_bundles.json"]:
        assert (DATA / f).exists(), f"Missing {f}"


def test_rfp_bundles_not_empty():
    bundles = load("rfp_bundles.json")
    assert len(bundles) > 100, f"Expected >100 bundles, got {len(bundles)}"


def test_rfp_bundles_naics_scope():
    bundles = load("rfp_bundles.json")
    bad = [b.get("naics") for b in bundles if b.get("naics") and b.get("naics") not in NAICS_VALID]
    assert not bad, f"Unexpected NAICS codes in rfp_bundles: {set(bad)}"


def test_rfp_bundles_required_fields():
    bundles = load("rfp_bundles.json")
    for b in bundles[:20]:
        assert b.get("notice_id"), f"Bundle missing notice_id: {b}"
        assert b.get("title") or b.get("name"), f"Bundle missing title: {b.get('notice_id')}"


def test_rfp_signals_structure():
    signals = load("rfp_signals.json")
    assert "by_dept" in signals, "rfp_signals.json missing 'by_dept'"
    assert len(signals["by_dept"]) > 0, "rfp_signals by_dept is empty"
    assert "date_range" in signals, "rfp_signals.json missing 'date_range'"


def test_rfp_bundles_no_award_notices():
    bundles = load("rfp_bundles.json")
    award_notices = [b for b in bundles if (b.get("type") or "") == "Award Notice"]
    assert not award_notices, f"Award Notices found in rfp_bundles ({len(award_notices)})"


def test_rfp_bundles_no_duplicate_solicitation_numbers():
    bundles = load("rfp_bundles.json")
    sol_nums = [b["solicitation_number"] for b in bundles if b.get("solicitation_number")]
    assert len(sol_nums) == len(set(sol_nums)), \
        f"Duplicate solicitation numbers found in rfp_bundles"
