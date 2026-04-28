"""One-shot: re-download every attachment referenced in existing R2 bundles
and mirror it to R2 at it_rfps/attachments/{notice_id}/{filename}, then
write back the updated bundle JSON with `r2_url` populated.

Idempotent: skips attachments that already have an r2_url, and skips R2
keys that already exist (HEAD check). Safe to re-run after a SAM 429.

Run locally:
    python3 backfill_attachments.py            # full backfill
    python3 backfill_attachments.py --limit 50 # smoke test with 50 bundles
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from urllib.parse import quote

import requests

# Reuse helpers from the live pipeline
import rfp_text_pipeline as P
import r2_sync


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0,
                    help="Stop after this many bundles (0 = no limit)")
    ap.add_argument("--skip-existing", action="store_true", default=True,
                    help="Skip attachments whose r2_url is already populated")
    args = ap.parse_args()

    api_key = os.environ.get("SAM_API_KEY")
    if not api_key:
        sys.exit("SAM_API_KEY required")
    if not os.environ.get("CF_R2_ACCOUNT_ID"):
        sys.exit("R2 env vars required")

    s3 = r2_sync._client()
    bucket = r2_sync.BUCKET

    # List every bundle in R2
    paginator = s3.get_paginator("list_objects_v2")
    bundle_keys = []
    for page in paginator.paginate(Bucket=bucket, Prefix=P.R2_PREFIX + "bundles/"):
        for obj in page.get("Contents", []):
            if obj["Key"].endswith(".json"):
                bundle_keys.append(obj["Key"])

    print(f"Found {len(bundle_keys)} bundles in R2")
    if args.limit:
        bundle_keys = bundle_keys[:args.limit]
        print(f"Limiting to first {args.limit}")

    session = requests.Session()
    bundles_updated = atts_uploaded = atts_skipped_existing = atts_failed = 0

    for bi, key in enumerate(bundle_keys, 1):
        body = s3.get_object(Bucket=bucket, Key=key)["Body"].read()
        bundle = json.loads(body)
        nid = bundle.get("notice_id") or ""
        atts = bundle.get("attachments") or []
        if not atts:
            continue

        changed = False
        for att in atts:
            if args.skip_existing and att.get("r2_url"):
                atts_skipped_existing += 1
                continue
            url = att.get("url")
            filename = att.get("filename")
            if not url or not filename:
                continue

            # Skip if R2 key already exists (e.g. from a partial prior run)
            r2_key = P._attachment_r2_key(nid, filename)
            try:
                s3.head_object(Bucket=bucket, Key=r2_key)
                # Already there — just stamp the URL on the bundle
                att["r2_url"] = f"{P.R2_PUBLIC_BASE}/{quote(r2_key, safe='/')}"
                changed = True
                atts_skipped_existing += 1
                continue
            except Exception:
                pass

            fetch_url = url
            if "sam.gov" in url and "api_key=" not in url:
                fetch_url = f"{url}{'&' if '?' in url else '?'}api_key={api_key}"
            try:
                r = session.get(fetch_url, timeout=180, allow_redirects=True)
            except requests.RequestException as exc:
                print(f"  [{bi}/{len(bundle_keys)}] {nid}/{filename}: fetch error {exc}")
                atts_failed += 1
                continue
            if r.status_code == 429:
                sys.exit("SAM 429 — quota exhausted. Re-run later (idempotent).")
            if r.status_code != 200:
                print(f"  [{bi}/{len(bundle_keys)}] {nid}/{filename}: HTTP {r.status_code}")
                atts_failed += 1
                continue

            ct = (r.headers.get("content-type") or "").split(";")[0].strip() or None
            try:
                r2_url = P._upload_attachment_to_r2(nid, filename, r.content, ct)
            except Exception as exc:
                print(f"  [{bi}/{len(bundle_keys)}] {nid}/{filename}: upload error {exc}")
                atts_failed += 1
                continue
            att["r2_url"] = r2_url
            changed = True
            atts_uploaded += 1

        if changed:
            s3.put_object(Bucket=bucket, Key=key,
                          Body=json.dumps(bundle, indent=2, default=str).encode())
            bundles_updated += 1

        if bi % 25 == 0:
            print(f"  [{bi}/{len(bundle_keys)}] bundles, "
                  f"{atts_uploaded} uploaded, {atts_skipped_existing} skipped, "
                  f"{atts_failed} failed")

    print(f"\nDone. {bundles_updated} bundles updated; "
          f"{atts_uploaded} new attachments uploaded; "
          f"{atts_skipped_existing} already on R2; "
          f"{atts_failed} failed.")


if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv()
    main()
