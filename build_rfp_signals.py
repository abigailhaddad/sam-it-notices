"""
build_rfp_signals.py — Aggregate RFP bundle labels for the dashboard.

Pulls every bundle from R2 under it_rfps/bundles/, tallies the regex labels
that rfp_text_pipeline.py attaches to each bundle, and writes two JSONs:

  - web/data/rfp_signals.json  — overall label share (for the bar panel)
  - web/data/rfp_bundles.json  — per-bundle metadata + snippet list
                                 (for the "browse RFPs" viewer)

Labels currently tracked (see rfp_text_pipeline.classify_bundle_text):
  - mentions_rtm     — "requirements traceability matrix" / "RTM"
  - shall_count      — number of "shall" occurrences (normalized to bool here)
  - has_agile_vocab  — sprint / agile / scrum / kanban / backlog / user story / ...
  - has_user_vocab   — end user / stakeholder / user research / UX / ...

Run:
    python3 build_rfp_signals.py                     # from R2
    python3 build_rfp_signals.py --local data/rfp_text/bundles  # local dir
"""

from __future__ import annotations

import argparse
import json
import os
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv
from pydantic import BaseModel

load_dotenv()

OUT_SIGNALS = Path("web/data/rfp_signals.json")
OUT_BUNDLES = Path("web/data/rfp_bundles.json")
R2_PREFIX   = "it_rfps/bundles/"


# Personnel rows must clear two bars to be surfaced on the dashboard:
#   1. Title isn't a generic counterparty term (Contractor, Vendor, etc.)
#   2. At least one concrete qualification — min_years, education, certs,
#      or an explicit seniority level. Without any of these, the "role" is
#      almost certainly a passing mention, not a labor category.

_GENERIC_TITLES = {
    "contractor", "the contractor", "vendor", "the vendor",
    "awardee", "the awardee", "offeror", "the offeror",
    "bidder", "the bidder", "company", "firm", "team",
    # Govt-side roles that can still slip past the prompt
    "contracting officer", "contracting officer's representative",
    "contracting officer’s representative",  # smart-quote variant
    "cor", "cotr", "aco", "pco", "office poc", "agency poc",
    "technical poc", "tpoc", "government project manager",
    "government program manager",
}


def _has_concrete_qual(role: dict) -> bool:
    return bool(
        role.get("min_years_experience") is not None
        or (role.get("education") or "").strip()
        or (role.get("certifications") or [])
        or (role.get("level") or "").strip()
    )


def _filter_personnel(roles: list[dict]) -> list[dict]:
    """Drop generic-counterparty titles and roles with zero concrete quals."""
    out = []
    for r in roles:
        title = (r.get("title") or "").strip().lower().rstrip("(s)")
        if title in _GENERIC_TITLES:
            continue
        if not _has_concrete_qual(r):
            continue
        out.append(r)
    return out


# ── LCAT extraction ──────────────────────────────────────────────────────────

class LcatEntry(BaseModel):
    name: str
    rate_usd: Optional[float] = None
    rate_per: Optional[str] = None   # "hour" | "year" | "month"
    clin: Optional[str] = None
    source: str


_RE_DOLLAR_RATE = re.compile(
    r'\$\s*([\d,]+(?:\.\d{1,2})?)\s*(?:/\s*|\bper\s+)?(hr|hour|yr|year|annum|month|day)\b',
    re.IGNORECASE,
)
_RE_CLIN = re.compile(r'\bCLIN\s*(\d{3,4}[A-Z]{0,2})\b', re.IGNORECASE)
# Header row signals that a table is a labor category table
_RE_LCAT_HEADER = re.compile(
    r'labor\s+categor|lcat\b|labor\s+title|position\s+title|job\s+title|personnel\s+categor',
    re.IGNORECASE,
)
# Cell values to skip as LCAT names
_RE_SKIP_CELL = re.compile(
    r'^\s*(?:\$|\d|n/?a\b|tbd\b|varies\b|total\b|ffp\b|t&m\b|cpff\b|base\b|option\b'
    r'|labor\s+hour|time\s+and\s+material|quantity\b|unit\s+price\b|unit\b|rate\b'
    r'|description\b|labor\s+cat|ceiling\b|period\b|hours?\b)',
    re.IGNORECASE,
)
# Words that disqualify a string as a job title
_RE_NOT_A_TITLE = re.compile(
    r'\b(option|exercised|amended|after|before|contract|shall|period|fiscal|year|'
    r'invoice|payment|deliverable|award|performance|government|contractor)\b',
    re.IGNORECASE,
)


def _parse_rate(dollar_str: str, unit: str) -> tuple[float, str]:
    amount = float(dollar_str.replace(',', ''))
    u = unit.lower()
    per = 'hour' if u in ('hr', 'hour') else 'year' if u in ('yr', 'year', 'annum') else u
    return amount, per


def _is_lcat_name(s: str) -> bool:
    s = s.strip()
    # Length: 3–60 chars, 2–6 words
    words = s.split()
    if not (3 <= len(s) <= 60) or not (2 <= len(words) <= 7):
        return False
    if _RE_SKIP_CELL.match(s):
        return False
    if _RE_NOT_A_TITLE.search(s):
        return False
    # Must contain at least one letter; reject mostly-numeric strings
    if not re.search(r'[A-Za-z]{2,}', s):
        return False
    # Hourly labor rates: $15–$500/hr is plausible; reject obvious outliers with units
    return True


def _extract_from_lcat_table(lines: list[str], src: str, seen: set[str]) -> list[LcatEntry]:
    """Extract from a contiguous block of pipe-delimited rows under a known LCAT header."""
    results: list[LcatEntry] = []
    for line in lines:
        if '|' not in line or not _RE_DOLLAR_RATE.search(line):
            continue
        cells = [c.strip() for c in line.split('|') if c.strip()]
        rate_m = next((m for c in cells for m in [_RE_DOLLAR_RATE.search(c)] if m), None)
        if not rate_m:
            continue
        amount, per = _parse_rate(rate_m.group(1), rate_m.group(2))
        # Sanity check: hourly $10–$500, annual $20k–$600k
        if per == 'hour' and not (10 <= amount <= 500):
            continue
        if per == 'year' and not (20_000 <= amount <= 600_000):
            continue
        clin_m = _RE_CLIN.search(line)
        name = next(
            (c for c in cells if _is_lcat_name(c) and not _RE_DOLLAR_RATE.search(c)),
            None,
        )
        if name and name.lower() not in seen:
            results.append(LcatEntry(name=name, rate_usd=amount, rate_per=per,
                                     clin=clin_m.group(1) if clin_m else None, source=src))
            seen.add(name.lower())
    return results


def extract_lcats(attachments: list[dict]) -> list[dict]:
    results: list[LcatEntry] = []
    seen: set[str] = set()

    for att in attachments:
        text = att.get('text') or ''
        src  = att.get('filename') or 'attachment'
        if not text:
            continue

        lines = text.splitlines()

        # Strategy 1: find LCAT header rows, then parse the following table rows
        for i, line in enumerate(lines):
            if '|' in line and _RE_LCAT_HEADER.search(line):
                # Extract up to 40 subsequent pipe rows as the table body
                table_lines = [l for l in lines[i+1:i+41] if '|' in l]
                results.extend(_extract_from_lcat_table(table_lines, src, seen))

        # Strategy 2: "Labor Category: <name>" immediately followed/preceded by a rate
        for m in re.finditer(
            r'(?:labor\s+categor(?:y|ies)|lcat)\s*[:\-]\s*([^\n\$\|]{4,55})',
            text, re.IGNORECASE,
        ):
            name = m.group(1).strip().rstrip(',;:')
            if not _is_lcat_name(name) or name.lower() in seen:
                continue
            # Look for a rate within 200 chars after the label
            window = text[m.end(): m.end() + 200]
            rate_m = _RE_DOLLAR_RATE.search(window)
            if not rate_m:
                continue
            amount, per = _parse_rate(rate_m.group(1), rate_m.group(2))
            if per == 'hour' and not (10 <= amount <= 500):
                continue
            clin_m = _RE_CLIN.search(text[max(0, m.start()-50): m.end()])
            results.append(LcatEntry(name=name, rate_usd=amount, rate_per=per,
                                     clin=clin_m.group(1) if clin_m else None, source=src))
            seen.add(name.lower())

    return [e.model_dump() for e in results]

# Regexes kept in sync with rfp_text_pipeline.classify_bundle_text. If the
# pipeline's patterns change we update here too — they're intentionally
# the same strings.
_RE = {
    "shall_count":     re.compile(r"\bshall\b", re.IGNORECASE),
    "has_user_vocab":  re.compile(
        r"\b(end[- ]?users?|stakeholders?|user\s+research|user\s+needs?|user\s+experience|ux)\b",
        re.IGNORECASE),
    "has_agile_vocab": re.compile(
        r"\b(sprint|agile|scrum|kanban|iteration|backlog|user\s+stor(y|ies)|mvp|working\s+software|ceremon(y|ies)|stand[- ]?up|retrospective)\b",
        re.IGNORECASE),
    "mentions_rtm":    re.compile(r"\brequirements?\s+traceability\s+matrix\b", re.IGNORECASE),
}

SNIPPET_RADIUS    = 120   # chars of context on each side of a match
MAX_SNIPPETS_PER_LABEL_PER_BUNDLE = 5

LABELS = [
    ("shall_count",     "Contains 'shall' clauses",        "FAR-style requirement language ('the contractor shall...')"),
    ("has_user_vocab",  "Mentions users / stakeholders",   "end users, stakeholders, user research, UX"),
    ("has_agile_vocab", "Uses agile vocabulary",           "sprints, scrum, kanban, backlog, user stories"),
    ("mentions_rtm",    "Mentions an RTM",                 "requirements traceability matrix"),
]


def iter_bundles_r2():
    import boto3
    s3 = boto3.client(
        "s3",
        endpoint_url=f"https://{os.environ['CF_R2_ACCOUNT_ID']}.r2.cloudflarestorage.com",
        aws_access_key_id=os.environ["CF_R2_ACCESS_KEY_ID"],
        aws_secret_access_key=os.environ["CF_R2_SECRET_ACCESS_KEY"],
        region_name="auto",
    )
    bucket = os.environ["CF_R2_BUCKET"]
    p = s3.get_paginator("list_objects_v2")
    for page in p.paginate(Bucket=bucket, Prefix=R2_PREFIX):
        for o in page.get("Contents", []):
            body = s3.get_object(Bucket=bucket, Key=o["Key"])["Body"].read()
            yield json.loads(body)


def iter_bundles_local(path: Path):
    for f in sorted(path.glob("*.json")):
        yield json.loads(f.read_text())


def _snippet(text: str, start: int, end: int) -> dict:
    """Return a small snippet-around-match record."""
    s = max(0, start - SNIPPET_RADIUS)
    e = min(len(text), end + SNIPPET_RADIUS)
    before = text[s:start]
    match  = text[start:end]
    after  = text[end:e]
    # collapse whitespace / newlines so the UI can render on one line
    def clean(x: str) -> str:
        return re.sub(r"\s+", " ", x).strip()
    return {
        "before": ("… " if s > 0 else "") + clean(before),
        "match":  clean(match),
        "after":  clean(after) + (" …" if e < len(text) else ""),
    }


def extract_snippets(attachments: list[dict], description: str) -> dict:
    """For each label, return up to N snippets across all attachment text + the
    opportunity description. Keeps the filename so the UI can show provenance."""
    out = {k: [] for k in _RE}
    # Scan each source separately so we know which attachment / source a
    # snippet came from.
    sources = [(a.get("filename") or f"attachment_{i}", a.get("text") or "")
               for i, a in enumerate(attachments)]
    if description:
        sources.append(("(notice description)", description))

    for src, text in sources:
        if not text:
            continue
        for label, pat in _RE.items():
            if len(out[label]) >= MAX_SNIPPETS_PER_LABEL_PER_BUNDLE:
                continue
            for m in pat.finditer(text):
                out[label].append({"source": src, **_snippet(text, m.start(), m.end())})
                if len(out[label]) >= MAX_SNIPPETS_PER_LABEL_PER_BUNDLE:
                    break
    # drop empty keys for leaner JSON
    return {k: v for k, v in out.items() if v}


def _load_personnel_cache_r2(s3, bucket: str) -> dict[str, list]:
    """Load all cached personnel extractions from R2. Returns {notice_id: [roles]}."""
    cache: dict[str, list] = {}
    try:
        p = s3.get_paginator("list_objects_v2")
        for page in p.paginate(Bucket=bucket, Prefix="it_rfps/personnel/"):
            for o in page.get("Contents", []):
                nid = o["Key"].removeprefix("it_rfps/personnel/").removesuffix(".json")
                try:
                    body = s3.get_object(Bucket=bucket, Key=o["Key"])["Body"].read()
                    data = json.loads(body)
                    cache[nid] = data.get("roles") or []
                except Exception:
                    pass
    except Exception:
        pass
    return cache


def aggregate(bundles, personnel_cache: dict | None = None):
    total = 0
    with_att = 0
    label_bool_hits = Counter()
    dept = Counter()
    ntype = Counter()
    posted = []
    examples = {k: [] for k, _, _ in LABELS}
    bundle_rows: list[dict] = []

    # by_dept: dept -> {total, label_key -> hit_count}
    dept_stats: dict[str, dict] = {}
    # by_month: "YYYY-MM" -> {total, label_key -> hit_count}
    month_stats: dict[str, dict] = {}

    # Mirror DEFAULT_NAICS_PREFIXES from rfp_text_pipeline.py — keep in sync.
    NAICS_KEEP = {
        "541511", "541512", "541513", "541519", "518210",
        "541330", "541611", "541618", "541690", "541715", "541990",
    }

    for b in bundles:
        m = b.get("metadata") or {}
        naics = (m.get("naics_code") or "").strip()
        if naics not in NAICS_KEEP:
            continue
        if (m.get("type") or "") == "Award Notice":
            continue
        total += 1
        atts = b.get("attachments") or []
        if atts:
            with_att += 1
        d = m.get("department") or "(none)"
        t = m.get("type") or "(none)"
        dept[d] += 1
        ntype[t] += 1
        posted_date = (m.get("posted_date") or "")[:10]
        if posted_date:
            posted.append(posted_date)
        month_key = posted_date[:7] if posted_date else None  # "YYYY-MM"

        # Recompute labels from stored text rather than trusting the
        # pipeline-time labels[] field — so regex tweaks here take effect on
        # the next rebuild without a pipeline re-run.
        # Sentinel-delimited per-attachment sections so the modal can render
        # "from foo.pdf" headers. Modal strips sentinels for display; search
        # column also strips them so filenames don't pollute matches.
        text_sections = []
        for a in atts:
            t = a.get("text")
            if not t:
                continue
            fname = a.get("filename") or "attachment"
            text_sections.append(f"␟{fname}␞\n\n{t}")
        if m.get("description"):
            text_sections.append(f"␟(notice description)␞\n\n{m.get('description')}")
        full_text = "\n\n".join(text_sections)
        labels = {
            "shall_count":     len(_RE["shall_count"].findall(full_text)),
            "has_user_vocab":  bool(_RE["has_user_vocab"].search(full_text)),
            "has_agile_vocab": bool(_RE["has_agile_vocab"].search(full_text)),
            "mentions_rtm":    bool(_RE["mentions_rtm"].search(full_text)),
        }
        snippets = extract_snippets(atts, m.get("description") or "")
        label_hits = {}
        for key, _, _ in LABELS:
            v = labels.get(key)
            hit = bool(v) if not isinstance(v, int) else v > 0
            if hit:
                label_bool_hits[key] += 1
                label_hits[key] = v
                if len(examples[key]) < 3:
                    examples[key].append({
                        "title":       m.get("title"),
                        "type":        m.get("type"),
                        "department":  d,
                        "posted_date": posted_date,
                        "ui_link":     m.get("ui_link"),
                    })

            # by_dept accumulation
            ds = dept_stats.setdefault(d, {"total": 0, **{k: 0 for k, _, _ in LABELS}})
            if key not in ds:
                ds[key] = 0
            if hit:
                ds[key] += 1

            # by_month accumulation
            if month_key:
                ms = month_stats.setdefault(month_key, {"total": 0, **{k: 0 for k, _, _ in LABELS}})
                if key not in ms:
                    ms[key] = 0
                if hit:
                    ms[key] += 1

        # increment totals outside the label loop
        dept_stats.setdefault(d, {"total": 0, **{k: 0 for k, _, _ in LABELS}})["total"] += 1
        if month_key:
            month_stats.setdefault(month_key, {"total": 0, **{k: 0 for k, _, _ in LABELS}})["total"] += 1

        lcats = extract_lcats(atts)
        nid   = b.get("notice_id") or ""
        personnel = (personnel_cache or {}).get(nid) or None
        if personnel:
            personnel = _filter_personnel(personnel)
            if not personnel:
                personnel = None

        # Slim attachment list for the dashboard: filename + R2 public URL.
        # SAM resource URLs require an api_key (would 401 from a browser),
        # so we only ship attachments that have an r2_url. Skip extracted
        # text + binary metadata to keep the JSON small.
        attachment_links = [
            {"filename": a.get("filename"), "url": a.get("r2_url")}
            for a in atts
            if a.get("r2_url")
        ]

        bundle_rows.append({
            "notice_id":          nid,
            "solicitation_number": m.get("solicitation_number"),
            "title":              m.get("title"),
            "type":               m.get("type"),
            "department":         d,
            "posted_date":        posted_date,
            "naics":              m.get("naics_code"),
            "set_aside":          m.get("set_aside_desc") or m.get("set_aside"),
            "ui_link":            m.get("ui_link"),
            "label_hits":         label_hits,
            "attachment_count":   len(atts),
            "attachments":        attachment_links,
            "snippets":           snippets,
            "lcats":              lcats or None,
            "personnel":          personnel,
            "search_text":        full_text[:15000],
        })

    bundle_rows.sort(key=lambda r: (r.get("posted_date") or "", r.get("title") or ""), reverse=True)

    # Deduplicate by solicitation_number — keep latest posting when SAM reposts the same sol
    seen_sol: set[str] = set()
    deduped: list[dict] = []
    for row in bundle_rows:
        sol = (row.get("solicitation_number") or "").strip()
        if sol and sol in seen_sol:
            continue
        if sol:
            seen_sol.add(sol)
        deduped.append(row)
    bundle_rows = deduped

    label_keys = [k for k, _, _ in LABELS]

    # Build by_dept: sorted by total desc, at least 5 bundles
    by_dept = []
    for d_name, stats in sorted(dept_stats.items(), key=lambda x: -x[1]["total"]):
        n = stats["total"]
        if n < 5:
            continue
        by_dept.append({
            "dept":  d_name,
            "total": n,
            "pcts":  {k: round(stats[k] / n * 100, 1) for k in label_keys},
        })

    # Build by_month: sorted chronologically
    by_month = []
    for mo in sorted(month_stats.keys()):
        stats = month_stats[mo]
        n = stats["total"]
        by_month.append({
            "month": mo,
            "total": n,
            "pcts":  {k: round(stats[k] / n * 100, 1) for k in label_keys},
        })

    signals = {
        "generated_at":   datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%MZ"),
        "total_bundles":  total,
        "with_attachments": with_att,
        "date_range":     {"from": min(posted) if posted else None,
                           "to":   max(posted) if posted else None},
        "labels": [
            {
                "key":         key,
                "label":       label,
                "description": desc,
                "count":       label_bool_hits[key],
                "percent":     round(label_bool_hits[key] / total * 100, 1) if total else 0,
                "examples":    examples[key],
            }
            for key, label, desc in LABELS
        ],
        "top_departments": [{"name": n, "count": c} for n, c in dept.most_common(10)],
        "top_notice_types": [{"name": n, "count": c} for n, c in ntype.most_common()],
        "by_dept":  by_dept,
        "by_month": by_month,
    }
    return signals, bundle_rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--local", type=Path, default=None,
                    help="Read bundles from a local directory instead of R2")
    args = ap.parse_args()

    personnel_cache = None
    if not args.local:
        import boto3
        from botocore.config import Config
        s3 = boto3.client(
            "s3",
            endpoint_url=f"https://{os.environ['CF_R2_ACCOUNT_ID']}.r2.cloudflarestorage.com",
            aws_access_key_id=os.environ["CF_R2_ACCESS_KEY_ID"],
            aws_secret_access_key=os.environ["CF_R2_SECRET_ACCESS_KEY"],
            config=Config(signature_version="s3v4"), region_name="auto",
        )
        bucket = os.environ["CF_R2_BUCKET"]
        personnel_cache = _load_personnel_cache_r2(s3, bucket)
        print(f"Loaded {len(personnel_cache)} personnel extractions from R2")

    bundles = iter_bundles_local(args.local) if args.local else iter_bundles_r2()
    signals, bundle_rows = aggregate(bundles, personnel_cache)

    OUT_SIGNALS.parent.mkdir(parents=True, exist_ok=True)
    OUT_SIGNALS.write_text(json.dumps(signals, indent=2))
    # The bundles file is large — dump compact (no indent) to keep it small.
    OUT_BUNDLES.write_text(json.dumps(bundle_rows, separators=(",", ":")))

    print(f"wrote {OUT_SIGNALS}  ({signals['total_bundles']} bundles, "
          f"{signals['date_range']['from']} → {signals['date_range']['to']})")
    for lb in signals["labels"]:
        print(f"  {lb['percent']:>5.1f}%  {lb['label']}  ({lb['count']})")
    size_kb = OUT_BUNDLES.stat().st_size / 1024
    print(f"wrote {OUT_BUNDLES}  ({len(bundle_rows)} rows, {size_kb:.0f} KB)")


if __name__ == "__main__":
    main()
