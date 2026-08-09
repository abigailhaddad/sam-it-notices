"""Nothing under web/ may exceed Cloudflare Pages' per-file limit.

Cloudflare Pages hard-rejects an upload containing any single file larger
than 25 MiB. web/data/rfp_bundles.json hit 25,057,780 bytes (95.6% of the
limit) and the daily pipeline only appends, so the deploy was weeks from
failing outright. This is the check that catches it next time.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from rfp_bundle_shards import (  # noqa: E402
    BUNDLES_DIR,
    CF_PAGES_MAX_FILE_BYTES,
    SHARD_MAX_BYTES,
)

WEB = Path(__file__).resolve().parents[2] / "web"


def _web_files():
    return [p for p in WEB.rglob("*") if p.is_file()]


def test_no_web_file_exceeds_cloudflare_pages_limit():
    oversized = [
        (p.relative_to(WEB).as_posix(), p.stat().st_size)
        for p in _web_files()
        if p.stat().st_size > CF_PAGES_MAX_FILE_BYTES
    ]
    assert not oversized, (
        f"Cloudflare Pages rejects files over {CF_PAGES_MAX_FILE_BYTES:,} bytes; "
        f"oversized: {oversized}"
    )


def test_web_files_have_headroom_under_the_limit():
    """Fail while there is still time to act, not on the deploy that breaks.

    Anything past 80% of the limit is on a trajectory to break the deploy and
    needs splitting before it does.
    """
    warn_at = int(CF_PAGES_MAX_FILE_BYTES * 0.8)
    crowded = [
        (p.relative_to(WEB).as_posix(), p.stat().st_size)
        for p in _web_files()
        if p.stat().st_size > warn_at
    ]
    assert not crowded, (
        f"File(s) past 80% ({warn_at:,} bytes) of the Cloudflare Pages "
        f"25 MiB per-file limit: {crowded}"
    )


def test_bundle_shards_respect_their_budget():
    """The shard budget is what keeps the headroom above from eroding."""
    over = [
        (p.name, p.stat().st_size)
        for p in BUNDLES_DIR.glob("shard-*.json")
        if p.stat().st_size > SHARD_MAX_BYTES
    ]
    assert not over, f"Shard(s) over the {SHARD_MAX_BYTES:,}-byte budget: {over}"
