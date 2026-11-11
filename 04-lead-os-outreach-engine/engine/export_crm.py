#!/usr/bin/env python3
"""Export the vault into chunk files for the team CRM web page.

Each chunk is one JSON document ({"leads": [...]}) under ~200 KB, holding the
fixed facts and the drafted message for each lead. Teammates' work (status,
owner, notes, edits) lives in the CRM's own per-lead state documents, so
re-running this export after new leads arrive never overwrites their work.

Usage:
  python engine/export_crm.py --out <folder>
"""

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import dispatch  # noqa: E402  (extract_drafts: the same parser dispatch uses)

ROOT = Path(__file__).resolve().parent.parent
VAULT = ROOT / "vault"
STAGES = {"01_Sourced": "new", "02_Analyzed": "new", "03_Approved": "approved",
          "04_Sent": "sent", "99_Rejected": "rejected"}
LANES = {"agency": "agency", "hiring": "hiring", "post": "hiring", "local": "local", "brand": "brand"}
CHUNK_BYTES = 200_000


def frontmatter(text):
    m = re.match(r"^---\n(.*?)\n---\n", text, re.S)
    out = {}
    if not m:
        return out, text
    for line in m.group(1).splitlines():
        kv = re.match(r"^([A-Za-z_][A-Za-z0-9_]*):\s*(.*)$", line)
        if kv:
            out[kv.group(1)] = kv.group(2).strip().strip('"').strip("'")
    return out, text[m.end():]


def section(body, title, limit=500):
    m = re.search(rf"^##\s*{title}.*?\n(.*?)(?=\n##\s|\Z)", body, re.S | re.M | re.I)
    return re.sub(r"\n{3,}", "\n\n", m.group(1).strip())[:limit] if m else ""


def lead_from_note(path, status):
    text = path.read_text(encoding="utf-8", errors="ignore")
    fm, body = frontmatter(text)
    drafts = dispatch.extract_drafts(body)
    email = drafts.get("email", "")
    subject = ""
    sm = re.match(r"^Subject:\s*(.+)\n+", email)
    if sm:
        subject, email = sm.group(1).strip(), email[sm.end():].strip()
    lane = LANES.get(fm.get("lead_type", ""), fm.get("lead_type", "") or "other")
    if status == "new" and "#approved" in text:
        status = "approved"
    return {
        "id": path.stem[:180],
        "brand": fm.get("brand") or path.stem,
        "lane": lane,
        "niche": fm.get("niche") or fm.get("services") or "",
        "market": fm.get("market", ""),
        "country": fm.get("country", ""),
        "city": fm.get("city", ""),
        "email": fm.get("email", ""),
        "phone": fm.get("phone", ""),
        "instagram": fm.get("instagram", ""),
        "website": fm.get("website", ""),
        "tier": fm.get("tier", ""),
        "rating": fm.get("rating", ""),
        "reviews": fm.get("reviews", ""),
        "advertising": fm.get("advertising", ""),
        "send_window": fm.get("send_at_ist", ""),
        "subject": subject,
        "body": email,
        "dm": drafts.get("instagram", "")[:1200],
        "evidence": section(body, "What is verified") or section(body, "Confirmed Evidence"),
        "vault_status": status,
        "sent_on": fm.get("sent") or fm.get("sent_on") or fm.get("date_sent") or "",
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    leads = []
    for stage, status in STAGES.items():
        d = VAULT / stage
        if d.exists():
            leads += [lead_from_note(p, status) for p in sorted(d.glob("*.md"))]
    chunks, cur = [], []
    for lead in leads:
        cur.append(lead)
        if len(json.dumps({"leads": cur}, ensure_ascii=False).encode()) > CHUNK_BYTES:
            chunks.append(cur[:-1])
            cur = [lead]
    if cur:
        chunks.append(cur)
    for old in out.glob("chunk-*.json"):
        old.unlink()
    for i, c in enumerate(chunks, 1):
        (out / f"chunk-{i:02d}.json").write_text(json.dumps({"leads": c}, ensure_ascii=False), encoding="utf-8")
    # The desk's sheet import refuses anyone Lead OS already knows: the ledger
    # (contacted or imported) plus DO_NOT_CONTACT, as normalized names.
    import vault as V
    names = set(V.load_seen())
    dnc = VAULT / "00_Control" / "DO_NOT_CONTACT.md"
    if dnc.exists():
        for line in dnc.read_text(encoding="utf-8", errors="ignore").splitlines():
            line = line.strip().lstrip("-*").strip()
            if line and not line.startswith("#"):
                names.add(V.norm(line.split("|")[0]))
    (out / "ledger.json").write_text(json.dumps({"names": sorted(n for n in names if n)}), encoding="utf-8")
    by = {}
    for lead in leads:
        by[(lead["lane"], lead["vault_status"])] = by.get((lead["lane"], lead["vault_status"]), 0) + 1
    print(json.dumps({"leads": len(leads), "chunks": len(chunks),
                      "by_lane_status": {f"{k[0]}/{k[1]}": v for k, v in sorted(by.items())},
                      "no_email_draft": sum(1 for l in leads if not l["body"])}, indent=1))


if __name__ == "__main__":
    main()
