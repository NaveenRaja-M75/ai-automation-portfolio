"""LeadForge :: reporting and export."""
from __future__ import annotations

import csv
import io
import json
import os
import re
from datetime import datetime, timezone


def _json_field(value, default):
    if value is None:
        return default
    if isinstance(value, (list, dict)):
        return value
    try:
        return json.loads(value)
    except Exception:
        return default


def _split_name(full: str | None) -> tuple[str, str]:
    if not full:
        return "", ""
    parts = [p for p in re.split(r"\s+", full.strip()) if p]
    if not parts:
        return "", ""
    if len(parts) == 1:
        return parts[0], ""
    return parts[0], parts[-1]


def _infer_name(lead) -> tuple[str, str]:
    n, _l = _split_name(lead["contact_name"]) if lead["contact_name"] else ("", "")
    if n:
        return n, _l
    email = lead["contact_email"] or ""
    local = email.split("@")[0] if "@" in email else ""
    if not local or local in ("info", "hello", "contact", "support", "sales"):
        return "", ""
    parts = re.split(r"[._\-]+", local)
    if len(parts) >= 2 and all(p.isalpha() for p in parts[:2]):
        return parts[0].capitalize(), parts[1].capitalize()
    if local.isalpha():
        return local.capitalize(), ""
    return "", ""


# --------------------------------------------------------------------------
# Formats
# --------------------------------------------------------------------------

RAW_COLUMNS = [
    "tier", "score", "source", "company", "domain", "contact_email",
    "contact_name", "contact_role", "location", "title", "url", "posted_at",
    "budget_raw", "budget_max", "currency", "offers", "tags", "emails", "body",
]

CRm_COLUMNS = ["email", "first_name", "last_name", "company_name", "website",
               "job_title", "tier", "score", "signal", "source_url", "personalization"]


def rows_to_csv(rows, fmt: str = "raw") -> str:
    buf = io.StringIO()
    if fmt in ("instantly", "smartlead", "lemlist", "crm", "cold-email"):
        w = csv.DictWriter(buf, fieldnames=CRm_COLUMNS, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            first, last = _infer_name(r)
            tags = _json_field(r["tags"], [])
            offers = _json_field(r["offers"], [])
            w.writerow({
                "email": r["contact_email"] or "",
                "first_name": first,
                "last_name": last,
                "company_name": r["company"] or r["domain"] or "",
                "website": ("https://" + r["domain"]) if r["domain"] else "",
                "job_title": r["contact_role"] or "",
                "tier": r["tier"], "score": r["score"],
                "signal": ",".join(t for t in tags if not t.startswith("penalty"))[:200],
                "source_url": r["url"] or "",
                "personalization": (f"Re: {r['title']}" if r["title"] else "")[:180],
            })
    else:
        w = csv.DictWriter(buf, fieldnames=RAW_COLUMNS, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            d = {k: r[k] for k in RAW_COLUMNS if k in r.keys()}
            d["tags"] = ",".join(_json_field(r["tags"], []))
            d["offers"] = ",".join(_json_field(r["offers"], []))
            d["emails"] = ",".join(_json_field(r["emails"], []))
            w.writerow(d)
    return buf.getvalue()


def rows_to_jsonl(rows) -> str:
    out = []
    for r in rows:
        d = dict(r)
        for k in ("tags", "offers", "emails", "raw"):
            d[k] = _json_field(d.get(k), [] if k != "raw" else {})
        out.append(json.dumps(d, ensure_ascii=False, default=str))
    return "\n".join(out) + ("\n" if out else "")


def rows_to_markdown(rows, limit: int = 40) -> str:
    lines = [
        "# LeadForge report",
        f"_generated {datetime.now(timezone.utc).isoformat(timespec='seconds')}_",
        "",
        f"{len(rows)} leads",
        "",
        "| # | tier | score | company / source | contact | signal | link |",
        "|---|------|-------|------------------|---------|--------|------|",
    ]
    for i, r in enumerate(rows[:limit], 1):
        tags = [t for t in _json_field(r["tags"], []) if not t.startswith("penalty")]
        offers = _json_field(r["offers"], [])
        label = r["company"] or r["domain"] or (r["title"] or "")[:40]
        contact = r["contact_email"] or (r["domain"] or "-")
        link = r["url"] or ""
        lines.append(
            f"| {i} | {r['tier']} | {r['score']} | {_md(label)} | {_md(contact)} | "
            f"{_md(','.join(offers) or ','.join(tags[:3]))} | "
            f"{'[open](' + link + ')' if link else '-'} |"
        )
    if len(rows) > limit:
        lines.append(f"\n_...and {len(rows) - limit} more._")
    return "\n".join(lines) + "\n"


def _md(s: str) -> str:
    return str(s or "").replace("|", "\\|").replace("\n", " ")[:70]


def write_all(rows, out_dir: str, stem: str = "leads") -> dict:
    os.makedirs(out_dir, exist_ok=True)
    paths = {
        "raw_csv": os.path.join(out_dir, f"{stem}.csv"),
        "crm_csv": os.path.join(out_dir, f"{stem}_crm.csv"),
        "jsonl": os.path.join(out_dir, f"{stem}.jsonl"),
        "markdown": os.path.join(out_dir, f"{stem}.md"),
    }
    with open(paths["raw_csv"], "w", encoding="utf-8-sig", newline="") as fh:
        fh.write(rows_to_csv(rows, "raw"))
    with open(paths["crm_csv"], "w", encoding="utf-8-sig", newline="") as fh:
        fh.write(rows_to_csv(rows, "instantly"))
    with open(paths["jsonl"], "w", encoding="utf-8") as fh:
        fh.write(rows_to_jsonl(rows))
    with open(paths["markdown"], "w", encoding="utf-8") as fh:
        fh.write(rows_to_markdown(rows))
    return paths


# --------------------------------------------------------------------------
# Console table
# --------------------------------------------------------------------------


def print_table(rows, limit: int = 30, width: int = 100, show_body: int = 0):
    if not rows:
        print("  (no leads matched)")
        return
    head = f"{'#':>3} {'T':1} {'SC':>3}  {'SOURCE':<15} {'COMPANY/CONTACT':<28} {'SIGNAL':<30}"
    print(head)
    print("-" * min(len(head), width + 20))
    for i, r in enumerate(rows[:limit], 1):
        tags = [t for t in _json_field(r["tags"], []) if not t.startswith("penalty")]
        offers = _json_field(r["offers"], [])
        who = r["company"] or r["domain"] or (r["title"] or "")[:26]
        contact = r["contact_email"] or ""
        label = who if not contact else f"{who}"
        signal = ",".join(offers) or ",".join(tags[:2]) or "-"
        print(f"{i:>3} {r['tier']:1} {r['score']:>3}  {r['source']:<15} "
              f"{_trunc(label,28):<28} {_trunc(signal,30):<30}")
        sub = f"        {_trunc(r['title'] or '', 74)}"
        print(sub)
        if contact:
            print(f"        -> {contact}")
        if show_body and r["body"]:
            body = re.sub(r"\s+", " ", r["body"])[:show_body]
            print(f"        |  {body}")
    if len(rows) > limit:
        print(f"\n  ...and {len(rows) - limit} more (use --limit)")


def _trunc(s: str, n: int) -> str:
    s = re.sub(r"\s+", " ", str(s or "")).strip()
    return s if len(s) <= n else s[: n - 1] + "\u2026"
