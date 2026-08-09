"""Read/write helpers for the sharded web/data/rfp_bundles/ payload.

Cloudflare Pages rejects any single file larger than 25 MiB (26,214,400
bytes). The old monolithic ``web/data/rfp_bundles.json`` was at 25,057,780
bytes — 95.6% of the limit — and the daily pipeline only appends, so the
deploy was weeks away from hard-failing.

The payload is therefore split into size-budgeted shards plus a manifest:

    web/data/rfp_bundles/manifest.json   {"count": N, "shards": [...]}
    web/data/rfp_bundles/shard-000.json  [ {...}, {...} ]
    web/data/rfp_bundles/shard-001.json  ...

The dashboard fetches the manifest, then every shard in parallel, and
concatenates them back into exactly the array it used to fetch in one
request. Every shard is loaded eagerly on purpose: full-text search runs
client-side over ``search_text`` in a hidden DataTables column, so the
search corpus has to be complete before the table is built.

Shards are packed to a budget rather than to a fixed count, so the file
count grows with the corpus and no individual file ever creeps toward the
platform limit.
"""

from __future__ import annotations

import json
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent
BUNDLES_DIR = REPO_ROOT / "web" / "data" / "rfp_bundles"
MANIFEST_NAME = "manifest.json"
SHARD_GLOB = "shard-*.json"

# The monolithic file this replaced. Deleted on write so a stale copy can't
# be picked up by a deploy.
LEGACY_BUNDLES_JSON = REPO_ROOT / "web" / "data" / "rfp_bundles.json"

# Hard Cloudflare Pages limit.
CF_PAGES_MAX_FILE_BYTES = 25 * 1024 * 1024  # 26,214,400

# Per-shard budget. Deliberately ~6x under the platform limit: it keeps the
# shard count small enough that parallel fetches stay cheap, while leaving
# no realistic path for one day's appends to push a file over.
SHARD_MAX_BYTES = 4 * 1024 * 1024  # 4,194,304


def _dumps(obj) -> str:
    """Compact JSON, matching what the monolithic file used to be written with."""
    return json.dumps(obj, separators=(",", ":"))


def write_bundle_shards(rows, out_dir: Path = BUNDLES_DIR,
                        max_bytes: int = SHARD_MAX_BYTES) -> dict:
    """Write ``rows`` as size-budgeted shards + manifest. Returns the manifest.

    Row order is preserved across the concatenated shards.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    # Drop shards from a previous, larger build so a shrinking corpus can't
    # leave orphans behind for the deploy to publish.
    for stale in out_dir.glob(SHARD_GLOB):
        stale.unlink()

    shards: list[list[str]] = []
    current: list[str] = []
    current_bytes = 2  # the enclosing "[" and "]"

    for row in rows:
        blob = _dumps(row)
        blob_bytes = len(blob.encode("utf-8"))
        added = blob_bytes + (1 if current else 0)  # +1 for the separating comma
        if current and current_bytes + added > max_bytes:
            shards.append(current)
            current, current_bytes = [], 2
            added = blob_bytes
        current.append(blob)
        current_bytes += added
    if current:
        shards.append(current)

    names = []
    for i, shard in enumerate(shards):
        name = f"shard-{i:03d}.json"
        (out_dir / name).write_text("[" + ",".join(shard) + "]")
        names.append(name)

    manifest = {
        "count": len(rows),
        "shard_max_bytes": max_bytes,
        "shards": names,
    }
    (out_dir / MANIFEST_NAME).write_text(json.dumps(manifest, indent=2))

    if LEGACY_BUNDLES_JSON.exists():
        LEGACY_BUNDLES_JSON.unlink()

    return manifest


def read_manifest(out_dir: Path = BUNDLES_DIR) -> dict:
    return json.loads((out_dir / MANIFEST_NAME).read_text())


def load_bundles(out_dir: Path = BUNDLES_DIR) -> list:
    """Read every shard back into one list, in manifest order."""
    manifest = read_manifest(out_dir)
    rows: list = []
    for name in manifest["shards"]:
        rows.extend(json.loads((out_dir / name).read_text()))
    return rows
