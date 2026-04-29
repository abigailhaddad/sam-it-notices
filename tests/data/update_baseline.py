"""
update_baseline.py — Snapshot current rfp_bundles.json into tests/data/baseline.json.

Run after a successful rebuild to raise the floor. The baseline is committed
to git; tests fail if any previously-seen notice_id disappears AND its
solicitation_number is also gone (sol# rotations are tolerated — see
test_no_drops.py).
"""
import json
from datetime import datetime, timezone
from pathlib import Path

BASELINE = Path(__file__).parent / "baseline.json"


def build_baseline():
    baseline = {"created_at": datetime.now(timezone.utc).isoformat()}

    rfp_path = Path("web/data/rfp_bundles.json")
    if rfp_path.exists():
        bundles = json.loads(rfp_path.read_text())
        nids = sorted(set(b["notice_id"] for b in bundles if b.get("notice_id")))
        # Map each notice_id to its solicitation_number when known. The
        # test treats baseline notice_ids whose sol# still appears in the
        # current build as "rotated" rather than "dropped".
        nid_to_sol = {}
        for b in bundles:
            nid = b.get("notice_id")
            sol = (b.get("solicitation_number") or "").strip()
            if nid and sol:
                nid_to_sol[nid] = sol
        baseline["rfp_bundles"] = {
            "count": len(nids),
            "notice_ids": nids,
            "notice_id_to_sol": nid_to_sol,
        }
        print(f"  rfp_bundles: {len(nids):,} notice IDs "
              f"({len(nid_to_sol):,} with sol#)")

    BASELINE.write_text(json.dumps(baseline, indent=2))
    print(f"\nWrote {BASELINE}")
    return baseline


if __name__ == "__main__":
    build_baseline()
