"""
extract_personnel.py — Extract key personnel roles + qualifications from RFP bundles.

For each bundle in R2, calls GPT to extract labor categories / key personnel roles
with years of experience, education, and a brief description. Results are cached at
it_rfps/personnel/{noticeId}.json on R2 so re-runs skip already-processed bundles.

Run:
    python3 extract_personnel.py                  # process all unprocessed bundles
    python3 extract_personnel.py --reprocess      # ignore cache, reprocess everything
    python3 extract_personnel.py --limit 10       # process at most N bundles
"""

from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path
from typing import Optional

import boto3
import litellm
from botocore.config import Config
from dotenv import load_dotenv
from pydantic import BaseModel, Field

load_dotenv()

MODEL      = "gpt-5.4-mini"
R2_BUNDLE_PREFIX    = "it_rfps/bundles/"
R2_PERSONNEL_PREFIX = "it_rfps/personnel/"
MAX_TEXT_CHARS      = 25_000   # per bundle; keeps token costs low
NAICS_KEEP          = {"541511", "541512", "541519", "518210"}

SYSTEM_PROMPT = """You are extracting key personnel and labor category requirements
from a US government IT services solicitation (RFP, PWS, or SOW).

Extract every distinct job role or labor category mentioned that has qualifications
or requirements specified. Focus on roles the contractor must provide — not
government roles or evaluation criteria.

For each role return:
- title: the job title or labor category name exactly as written
- level: seniority level if stated (Junior / Mid / Senior / Lead / Principal / etc.), else null
- min_years_experience: minimum years of experience as an integer, else null
- education: required degree or education level as a short string, else null
- certifications: list of required certifications/clearances (e.g. ["CISSP", "TS/SCI"]), else []
- brief_description: one sentence (≤25 words) summarizing main responsibilities, else null
- is_key_personnel: true if explicitly called "Key Personnel", false otherwise

Return only roles with at least a title. If no roles are found, return an empty list."""


class PersonnelRole(BaseModel):
    title: str
    level: Optional[str] = None
    min_years_experience: Optional[int] = None
    education: Optional[str] = None
    certifications: list[str] = Field(default_factory=list)
    brief_description: Optional[str] = None
    is_key_personnel: bool = False


class PersonnelExtraction(BaseModel):
    roles: list[PersonnelRole]


def _s3_client():
    return boto3.client(
        "s3",
        endpoint_url=f"https://{os.environ['CF_R2_ACCOUNT_ID']}.r2.cloudflarestorage.com",
        aws_access_key_id=os.environ["CF_R2_ACCESS_KEY_ID"],
        aws_secret_access_key=os.environ["CF_R2_SECRET_ACCESS_KEY"],
        config=Config(signature_version="s3v4"),
        region_name="auto",
    )


def _bundle_text(bundle: dict) -> str:
    """Concatenate attachment text, prioritising PWS/SOW/Section-L docs."""
    atts = bundle.get("attachments") or []

    def priority(att):
        fn = (att.get("filename") or "").lower()
        if any(x in fn for x in ("pws", "sow", "section_l", "section-l", "sol")):
            return 0
        if any(x in fn for x in ("resume", "personnel", "staffing")):
            return 1
        return 2

    atts_sorted = sorted(atts, key=priority)
    chunks = []
    total = 0
    for att in atts_sorted:
        text = (att.get("text") or "").strip()
        if not text:
            continue
        fname = att.get("filename") or ""
        chunk = f"--- {fname} ---\n{text}"
        if total + len(chunk) > MAX_TEXT_CHARS:
            remaining = MAX_TEXT_CHARS - total
            if remaining > 500:
                chunks.append(chunk[:remaining])
            break
        chunks.append(chunk)
        total += len(chunk)
    return "\n\n".join(chunks)


def extract_roles(text: str) -> PersonnelExtraction | None:
    for attempt in range(3):
        try:
            response = litellm.completion(
                model=MODEL,
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user",   "content": text},
                ],
                response_format=PersonnelExtraction,
                temperature=0.0,
            )
            raw = response.choices[0].message.content
            return PersonnelExtraction(**json.loads(raw))
        except Exception as exc:
            if attempt < 2:
                time.sleep(2 ** attempt)
            else:
                print(f"    extraction failed: {exc}")
                return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--reprocess", action="store_true", help="Ignore cached results")
    ap.add_argument("--limit", type=int, default=None, help="Max bundles to process")
    args = ap.parse_args()

    s3     = _s3_client()
    bucket = os.environ["CF_R2_BUCKET"]

    # Load existing cached keys
    cached: set[str] = set()
    if not args.reprocess:
        p = s3.get_paginator("list_objects_v2")
        for page in p.paginate(Bucket=bucket, Prefix=R2_PERSONNEL_PREFIX):
            for o in page.get("Contents", []):
                nid = o["Key"].removeprefix(R2_PERSONNEL_PREFIX).removesuffix(".json")
                cached.add(nid)
        print(f"{len(cached)} bundles already cached")

    processed = skipped = errors = 0

    pager = s3.get_paginator("list_objects_v2")
    for page in pager.paginate(Bucket=bucket, Prefix=R2_BUNDLE_PREFIX):
        for obj in page.get("Contents", []):
            if args.limit and processed >= args.limit:
                break

            bundle = json.loads(s3.get_object(Bucket=bucket, Key=obj["Key"])["Body"].read())
            nid    = bundle.get("notice_id") or ""
            naics  = (bundle.get("metadata") or {}).get("naics_code") or ""

            if naics not in NAICS_KEEP:
                skipped += 1
                continue
            if nid in cached:
                skipped += 1
                continue

            text = _bundle_text(bundle)
            if not text.strip():
                skipped += 1
                continue

            title = (bundle.get("metadata") or {}).get("title") or nid
            print(f"  {nid[:12]}  {title[:55]}")

            result = extract_roles(text)
            if result is None:
                errors += 1
                continue

            roles_found = len(result.roles)
            print(f"    → {roles_found} role(s): {[r.title for r in result.roles[:4]]}")

            s3.put_object(
                Bucket=bucket,
                Key=f"{R2_PERSONNEL_PREFIX}{nid}.json",
                Body=result.model_dump_json(),
                ContentType="application/json",
            )
            cached.add(nid)
            processed += 1

        if args.limit and processed >= args.limit:
            break

    print(f"\nDone. processed={processed}  skipped={skipped}  errors={errors}")


if __name__ == "__main__":
    main()
