"""Data integrity tests for web/data/ JSON files — notice browser only."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from rfp_bundle_shards import BUNDLES_DIR, MANIFEST_NAME, load_bundles  # noqa: E402

DATA = Path("web/data")
# Mirror DEFAULT_NAICS_PREFIXES from rfp_text_pipeline.py.
NAICS_VALID = {
    "541511", "541512", "541513", "541519", "518210",
    "541330", "541611", "541618", "541690", "541715", "541990",
}


def load(name):
    return json.loads((DATA / name).read_text())


def test_required_files_exist():
    assert (DATA / "rfp_signals.json").exists(), "Missing rfp_signals.json"
    manifest_path = BUNDLES_DIR / MANIFEST_NAME
    assert manifest_path.exists(), f"Missing {manifest_path}"
    manifest = json.loads(manifest_path.read_text())
    assert manifest["shards"], "Bundle manifest lists no shards"
    for name in manifest["shards"]:
        assert (BUNDLES_DIR / name).exists(), f"Manifest lists missing shard {name}"


def test_bundle_manifest_count_matches_shards():
    """The count the manifest advertises must equal the rows the shards hold —
    a mismatch means a shard went missing or was written stale.
    """
    manifest = json.loads((BUNDLES_DIR / MANIFEST_NAME).read_text())
    assert manifest["count"] == len(load_bundles())


def test_no_orphan_bundle_shards():
    """Every shard file on disk must be listed in the manifest. A shrinking
    corpus used to be able to leave a higher-numbered shard behind, which the
    deploy would publish but the page would never fetch.
    """
    manifest = json.loads((BUNDLES_DIR / MANIFEST_NAME).read_text())
    on_disk = sorted(p.name for p in BUNDLES_DIR.glob("shard-*.json"))
    assert on_disk == sorted(manifest["shards"])


def test_legacy_monolithic_bundles_file_absent():
    """web/data/rfp_bundles.json is what blew the Cloudflare Pages 25 MiB
    per-file limit. A stale copy would get deployed alongside the shards.
    """
    legacy = DATA / "rfp_bundles.json"
    assert not legacy.exists(), (
        f"{legacy} is back — build_rfp_signals.py should shard into "
        f"{BUNDLES_DIR} and delete the monolith"
    )


def test_rfp_bundles_not_empty():
    bundles = load_bundles()
    assert len(bundles) > 100, f"Expected >100 bundles, got {len(bundles)}"


def test_rfp_bundles_naics_scope():
    bundles = load_bundles()
    bad = [b.get("naics") for b in bundles if b.get("naics") and b.get("naics") not in NAICS_VALID]
    assert not bad, f"Unexpected NAICS codes in rfp_bundles: {set(bad)}"


def test_rfp_bundles_required_fields():
    bundles = load_bundles()
    for b in bundles[:20]:
        assert b.get("notice_id"), f"Bundle missing notice_id: {b}"
        assert b.get("title") or b.get("name"), f"Bundle missing title: {b.get('notice_id')}"


def test_rfp_signals_structure():
    signals = load("rfp_signals.json")
    assert "by_dept" in signals, "rfp_signals.json missing 'by_dept'"
    assert len(signals["by_dept"]) > 0, "rfp_signals by_dept is empty"
    assert "date_range" in signals, "rfp_signals.json missing 'date_range'"


def test_rfp_bundles_no_award_notices():
    bundles = load_bundles()
    award_notices = [b for b in bundles if (b.get("type") or "") == "Award Notice"]
    assert not award_notices, f"Award Notices found in rfp_bundles ({len(award_notices)})"


R2_PUBLIC_BASE = "https://pub-9f4e2a3f6cb94f8a9965f749fae53430.r2.dev"


def test_rfp_bundles_attachments_use_r2_urls():
    """Every attachment surfaced to the dashboard must link to our public R2
    bucket — SAM resource URLs require an api_key and would 401 from a
    browser. build_rfp_signals.py is supposed to drop attachments without
    an r2_url, so a leak here means that filter regressed.
    """
    bundles = load_bundles()
    bad = []
    for b in bundles:
        for a in (b.get("attachments") or []):
            url = a.get("url") or ""
            if not url.startswith(R2_PUBLIC_BASE + "/"):
                bad.append((b.get("notice_id"), a.get("filename"), url))
    assert not bad, (
        f"{len(bad)} attachment(s) point at non-R2 URL; first: {bad[0]}"
    )


def test_rfp_bundles_attachment_count_matches():
    """attachment_count should equal len(attachments) when attachments is
    populated. Catches drift between the count field and the link list.
    """
    bundles = load_bundles()
    mismatches = []
    for b in bundles:
        atts = b.get("attachments") or []
        if not atts:
            continue
        count = b.get("attachment_count")
        if count is not None and count < len(atts):
            mismatches.append((b.get("notice_id"), count, len(atts)))
    assert not mismatches, f"attachment_count < len(attachments): {mismatches[:3]}"


def test_rfp_bundles_have_some_attachments():
    """Sanity floor: at least 100 bundles should expose attachment links."""
    bundles = load_bundles()
    with_atts = [b for b in bundles if b.get("attachments")]
    assert len(with_atts) >= 100, (
        f"Expected ≥100 bundles with attachment links, got {len(with_atts)}"
    )


def test_rfp_bundles_no_duplicate_solicitation_numbers():
    bundles = load_bundles()
    sol_nums = [b["solicitation_number"] for b in bundles if b.get("solicitation_number")]
    assert len(sol_nums) == len(set(sol_nums)), \
        f"Duplicate solicitation numbers found in rfp_bundles"
