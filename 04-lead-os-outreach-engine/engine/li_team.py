#!/usr/bin/env python3
"""
LinkedIn engine, team handout: a clean lead sheet for team members. No drafts.

    python run.py linkedin team            new batch: every A/B lead not handed out yet
    python run.py linkedin team --all      every A/B lead again (a full re-issue)
    python run.py linkedin team-sync FILE  read a returned sheet's Status column back

WHY BATCHES
    The sheet goes to several people. The two ways that goes wrong are the same
    company being handed to two people, and a lead handed out last week coming
    back this week as "new". So every exported lead is remembered in
    _state/li_team_exported.json and the next batch only carries leads nobody has
    had yet. Inside one sheet, the Assigned to column stops two people taking
    the same row.

WHY SYNC
    Your team works the sheet, not the vault. Without reading it back, the
    ledger never learns who was contacted, and the engine could offer that
    company again. team-sync puts every row marked Contacted / Replied /
    Interested / Not interested into the ledger, so it is never offered again
    by any part of the Lead OS.

Sources: LinkedIn hiring notes in the vault that have not been sent yet, plus
tier A/B hiring rows that never went to the vault (no email found).
"""
import re
import sys
import json
import argparse
import datetime
from pathlib import Path

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.datavalidation import DataValidation

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))
import li_lib as L
import li_build as LB
import li_dedupe as D
import vault as V
from li_vault import country_city, send_time, role_short

OUT_DIR = L.ROOT / "Team_Leads"
EXPORTED = L.STATE / "li_team_exported.json"
STATUSES = ["Not contacted", "Contacted", "Replied", "Interested", "Not interested",
            "Wrong contact"]
DONE_STATUSES = {"contacted", "replied", "interested", "not interested", "wrong contact"}

PLAIN = {   # score reasons -> words a team member understands
    "AI generation role": "Hiring for AI video/ad work: exactly what we make",
    "UGC role": "Hiring for UGC: AI UGC can replace it",
    "ads/film role": "Hiring for ads/film work",
    "open to outside help": "Open to freelance, remote or agency help",
    "AI tools in description": "The job asks for AI tools",
    "agency": "Agency: can resell our work to its clients (white-label)",
    "brand": "Brand: direct client for its own ads",
    "foreign": "Foreign budget (pays in USD/GBP/AED/AUD)",
    "remote": "Remote role, location doesn't matter",
    "editing role (low fit)": "Mostly editing: lower fit, pitch AI ads not editing",
}

FONT = "Arial"


def plain_why(why):
    parts = [w.strip() for w in str(why or "").split(",") if w.strip()]
    out = [PLAIN.get(p, "") for p in parts]
    return "; ".join(x for x in out if x)


def angle(kind):
    return ("White-label: we make AI ads under THEIR agency name for their clients"
            if kind == "agency" else
            "Direct: AI ads and AI UGC for their products while they hire")


def how_to_reach(r):
    if r.get("email"):
        return "Email"
    if r.get("instagram"):
        return "Instagram"
    if r.get("phone"):
        return "Phone / WhatsApp"
    return "LinkedIn only"


def _body_field(body, label):
    m = re.search(r"- (?:confirmed: )?" + re.escape(label) + r"[: ]+(.*)", body)
    return m.group(1).strip().strip('"') if m else ""


def from_vault(s):
    """LinkedIn hiring notes not sent yet (Analyzed or Approved)."""
    rows = []
    for stage in ("analyzed", "approved"):
        for p in V.notes_in(stage):
            fm, body = V.read_note(p)
            if fm.get("source") != "linkedin jobs":
                continue
            why = re.search(r"why it scored \d+: (.*)", body)
            rows.append({
                "tier": str(fm.get("tier", ""))[:1], "score": int(fm.get("quality") or 0),
                "company": fm.get("brand", ""), "kind": fm.get("company_type", ""),
                "role": str(fm.get("signal", "")).replace("hiring: ", ""),
                "posted": fm.get("signal_date", ""), "country": fm.get("country", ""),
                "city": fm.get("city", ""), "size": fm.get("company_size", ""),
                "industry": fm.get("industry", ""), "website": fm.get("website", ""),
                "email": fm.get("email", ""), "email_check": fm.get("email_check", ""),
                "instagram": fm.get("instagram", ""), "phone": fm.get("phone", ""),
                "company_url": fm.get("linkedin", ""), "job": fm.get("signal_url", ""),
                "why": why.group(1) if why else "",
                "proof": _body_field(body, "proof from the posting"),
                "best_time": fm.get("send_at_ist", ""), "in_vault": p.name,
            })
            # re-pick the proof from the stored job description, so every row
            # quotes the AI-video sentence, not an old company tagline
            jid = str(fm.get("signal_url", "")).rstrip("/").rsplit("/", 1)[-1]
            desc = (s["jobs"].get(jid) or {}).get("description", "")
            if desc and LB.evidence(desc):
                rows[-1]["proof"] = LB.evidence(desc)
    return rows


def from_workbook_only(cfg, s):
    """Tier A/B hiring leads that never went to the vault (no email found)."""
    _, hiring, _, _, _ = LB.build_rows(cfg, s)
    rows = []
    for r in hiring:
        if r["tier"] not in ("A", "B"):
            continue
        country, city = country_city(r.get("location"), r.get("market"))
        rows.append({
            "tier": r["tier"], "score": r["score"], "company": r["company"],
            "kind": r.get("kind", ""), "role": r["title"], "posted": r.get("posted", ""),
            "country": country, "city": city, "size": r.get("size", ""),
            "industry": r.get("industry", ""), "website": r.get("website", ""),
            "email": r.get("email", ""), "email_check": r.get("email_check", ""),
            "instagram": r.get("instagram", ""), "phone": r.get("phone", ""),
            "company_url": r.get("company_url", ""), "job": r.get("link", ""),
            "why": r.get("why", ""), "proof": r.get("evidence", ""),
            "best_time": send_time(country, city), "in_vault": "",
        })
    return rows


def lead_keys(r):
    return D.identities(r["company"], r.get("email"), r.get("website"),
                        [r.get("company_url")])


def load_exported():
    try:
        return json.loads(EXPORTED.read_text(encoding="utf-8"))
    except Exception:
        return {}


# ------------------------------------------------------------------ workbook
COLS = [   # header, key, width, wrap
    ("#", "n", 5, False), ("Priority", "tier", 8, False), ("Company", "company", 26, False),
    ("Type", "type", 10, False), ("Hiring for", "role", 30, True),
    ("Posted", "posted", 11, False), ("Country", "country", 14, False),
    ("City", "city", 14, False), ("Size", "size", 15, False),
    ("How to reach", "reach", 13, False), ("Email", "email", 30, False),
    ("Email check", "email_check", 11, False), ("Instagram", "instagram", 18, False),
    ("Phone", "phone", 16, False), ("Website", "website", 22, False),
    ("Company LinkedIn", "company_url", 16, False), ("Job post", "job", 14, False),
    ("Why they fit", "why_plain", 44, True), ("Pitch angle", "angle", 38, True),
    ("Proof from their job post", "proof", 50, True),
    ("Best time to contact (IST)", "best_time", 26, True),
    ("Assigned to", "assigned", 14, False), ("Status", "status", 15, False),
    ("Date contacted", "date", 13, False), ("Notes", "notes", 34, True),
]
TEAM_COLS = ("assigned", "status", "date", "notes")
HEAD = PatternFill("solid", fgColor="1F2937")
TEAM_HEAD = PatternFill("solid", fgColor="B45309")
FILL_IN = PatternFill("solid", fgColor="FFF7D6")
TIER_FILL = {"A": PatternFill("solid", fgColor="DCFCE7"), "B": PatternFill("solid", fgColor="FEF9C3")}
THIN = Border(bottom=Side(style="thin", color="E5E7EB"))


def write(rows, path, batch_label):
    wb = Workbook()
    hw = wb.active
    hw.title = "How to use"
    a = sum(1 for r in rows if r["tier"] == "A")
    em = sum(1 for r in rows if r["reach"] == "Email")
    lines = [
        ("Monarc Studio: lead sheet", "title"),
        (f"{batch_label}. {len(rows)} companies: {a} priority A, {len(rows) - a} priority B. "
         f"{em} have an email address.", ""),
        ("", ""),
        ("WHAT THESE LEADS ARE", "h"),
        ("Every company here has an OPEN JOB on LinkedIn for AI video, AI ads, AI UGC or AI "
         "content work. That means they need it and have budget for it right now.", ""),
        ("Agency = sell white-label (we make AI ads under THEIR name for their clients). "
         "Brand = sell directly (AI ads for their own products while they hire).", ""),
        ("", ""),
        ("HOW TO WORK THE SHEET", "h"),
        ("1. Put your name in 'Assigned to' BEFORE you contact a company. If a name is "
         "already there, skip that row. This stops two people pitching the same company.", ""),
        ("2. Start with Priority A, then B. Use 'How to reach' to pick the channel.", ""),
        ("3. Read 'Hiring for', 'Why they fit' and 'Proof from their job post' first, so "
         "the message is about THEM.", ""),
        ("4. After contacting: set 'Status', fill 'Date contacted', add anything useful "
         "in 'Notes'.", ""),
        ("5. 'LinkedIn only' rows: open 'Company LinkedIn', find the founder, marketing "
         "head or hiring manager, and message them there.", ""),
        ("6. Send the sheet back when done. Every row marked contacted is recorded, so "
         "nobody contacts that company again.", ""),
        ("", ""),
        ("WHICH CELLS TO EDIT", "h"),
        ("Only the four orange columns on the Leads sheet: Assigned to, Status, Date "
         "contacted, Notes. Everything else is research. Please don't change it.", ""),
        ("Example of a filled row:  Assigned to = Priya  |  Status = Contacted  |  "
         "Date contacted = 2026-10-08  |  Notes = Emailed hello@ address, asked for "
         "their brand guide", ""),
        ("", ""),
        ("RULES", "h"),
        ("Never claim anything that isn't in the row. Never invent prices, results or "
         "clients. Quote prices only from the Monarc rate card.", ""),
        ("Be polite and short. If someone says no or asks not to be contacted, set Status "
         "to 'Not interested' and write it in Notes.", ""),
        ("Data comes from public LinkedIn job posts and the companies' own websites. "
         "Use it only to offer Monarc's services.", ""),
    ]
    for text, style in lines:
        hw.append([text])
        c = hw.cell(row=hw.max_row, column=1)
        c.font = Font(name=FONT, size=16 if style == "title" else 11,
                      bold=style in ("title", "h"),
                      color="B45309" if style == "h" else "111827")
        c.alignment = Alignment(wrap_text=True, vertical="top")
    hw.column_dimensions["A"].width = 120

    ws = wb.create_sheet("Leads")
    ws.append([c[0] for c in COLS])
    for i, (name, key, width, _) in enumerate(COLS, 1):
        cell = ws.cell(row=1, column=i)
        cell.font = Font(name=FONT, bold=True, color="FFFFFF")
        cell.fill = TEAM_HEAD if key in TEAM_COLS else HEAD
        cell.alignment = Alignment(vertical="center", wrap_text=True)
        ws.column_dimensions[get_column_letter(i)].width = width
    ws.row_dimensions[1].height = 32
    for n, r in enumerate(rows, 1):
        r["n"] = n
        ws.append([LB.safe(r.get(k, "")) for _, k, _, _ in COLS])
        row = ws.max_row
        for i, (_, key, _, wrap) in enumerate(COLS, 1):
            cell = ws.cell(row=row, column=i)
            cell.font = Font(name=FONT, size=10)
            cell.alignment = Alignment(vertical="top", wrap_text=wrap)
            cell.border = THIN
            v = str(r.get(key) or "")
            if key in ("company_url", "job") and v.startswith("http"):
                cell.hyperlink = v
                cell.value = "Open"
                cell.font = Font(name=FONT, size=10, color="2563EB", underline="single")
            elif key == "website" and v:
                cell.hyperlink = "https://" + v
                cell.font = Font(name=FONT, size=10, color="2563EB", underline="single")
            if key in TEAM_COLS:
                cell.fill = FILL_IN
        ws.cell(row=row, column=2).fill = TIER_FILL.get(r["tier"], PatternFill())
        ws.cell(row=row, column=2).font = Font(name=FONT, size=10, bold=True)
    last = ws.max_row
    status_col = get_column_letter([k for _, k, _, _ in COLS].index("status") + 1)
    dv = DataValidation(type="list", formula1='"' + ",".join(STATUSES) + '"',
                        allow_blank=True, showDropDown=False)
    dv.error, dv.errorTitle = "Pick a status from the list", "Status"
    ws.add_data_validation(dv)
    dv.add(f"{status_col}2:{status_col}{max(last, 2)}")
    ws.freeze_panes = "D2"
    ws.auto_filter.ref = f"A1:{get_column_letter(len(COLS))}{last}"
    path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(path)


# ------------------------------------------------------------------ commands
def export(args):
    L.lock()
    cfg, s = L.config(), L.load_state()
    rows, seen = [], set()
    exported = load_exported()
    for r in from_vault(s) + from_workbook_only(cfg, s):
        keys = lead_keys(r)
        if any(k in seen for k in keys):
            continue                      # same company from both sources
        seen.update(keys)
        if not args.all and any(k in exported for k in keys):
            continue                      # already handed out in an earlier batch
        r["type"] = {"agency": "Agency", "brand": "Brand"}.get(r["kind"], "Company")
        r["why_plain"] = plain_why(r["why"])
        r["angle"] = angle(r["kind"])
        r["reach"] = how_to_reach(r)
        r["status"] = "Not contacted"
        rows.append(r)
    L.save_state(s)
    if not rows:
        print("  No new leads to hand out. Every A/B lead is already in an earlier batch.\n"
              "  Run `python run.py linkedin jobs` tomorrow for fresh ones, or use --all to re-issue.")
        return
    rows.sort(key=lambda r: (r["tier"] != "A", r["reach"] == "LinkedIn only", -r["score"]))
    today = datetime.date.today().isoformat()
    path = OUT_DIR / f"Monarc_Team_Leads_{today}.xlsx"
    k = 2
    while path.exists():
        path = OUT_DIR / f"Monarc_Team_Leads_{today}_{k}.xlsx"
        k += 1
    write(rows, path, f"Batch of {today}")
    for r in rows:
        for key in lead_keys(r):
            exported.setdefault(key, today)
        # The same lead is also a vault note. Stamped so you can see in Obsidian
        # that the team has it, and do not dispatch it yourself as well.
        if r.get("in_vault"):
            p = V.STAGES["analyzed"] / r["in_vault"]
            if not p.exists():
                p = V.STAGES["approved"] / r["in_vault"]
            if p.exists():
                fm, body = V.read_note(p)
                fm["given_to_team"] = today
                tags = fm.get("tags") if isinstance(fm.get("tags"), list) else []
                if "team" not in tags:
                    fm["tags"] = tags + ["team"]
                p.write_text("---\n" + V._fm_dump(fm) + "\n---\n\n" + body.strip() + "\n",
                             encoding="utf-8")
    EXPORTED.write_text(json.dumps(exported, indent=0), encoding="utf-8")
    reach = {}
    for r in rows:
        reach[r["reach"]] = reach.get(r["reach"], 0) + 1
    print(f"\n  {len(rows)} leads -> {path}")
    print(f"  priority A {sum(1 for r in rows if r['tier'] == 'A')}, "
          f"B {sum(1 for r in rows if r['tier'] == 'B')}   |   " +
          ", ".join(f"{k}: {v}" for k, v in sorted(reach.items(), key=lambda x: -x[1])))
    print("  These leads will not appear in the next batch. When the team sends the sheet back:\n"
          f"  python run.py linkedin team-sync \"{path.name}\"")


def sync(args):
    """Status column of a returned sheet -> ledger, so nobody is contacted twice."""
    if args.file:
        path = Path(args.file)
        if not path.is_absolute():
            path = OUT_DIR / path if (OUT_DIR / path).exists() else Path.cwd() / path
    else:
        sheets = sorted(OUT_DIR.glob("Monarc_Team_Leads_*.xlsx"), key=lambda p: p.stat().st_mtime)
        if not sheets:
            sys.exit("No team sheet found in Team_Leads/")
        path = sheets[-1]
        print(f"  reading the newest sheet: {path.name}")
    wb = load_workbook(path, read_only=True)
    rows = list(wb["Leads"].iter_rows(values_only=True))
    wb.close()
    h = list(rows[0])
    g = lambda r, c: str(r[h.index(c)] or "").strip() if c in h else ""
    done, by_status = [], {}
    for r in rows[1:]:
        st = g(r, "Status").lower()
        by_status[st or "blank"] = by_status.get(st or "blank", 0) + 1
        if st in DONE_STATUSES:
            name = g(r, "Company")
            D.remember_imported(D.identities(name, g(r, "Email"), g(r, "Website")))
            done.append(name)
            if st == "not interested" and "do not contact" in g(r, "Notes").lower():
                print(f"  ! {name}: notes say do not contact. Add it to "
                      f"vault/00_Control/DO_NOT_CONTACT.md")
    if done:
        V.remember(done)
    print(f"  {path.name}: " + ", ".join(f"{k}: {v}" for k, v in by_status.items()))
    print(f"  {len(done)} companies recorded in the ledger. No part of the Lead OS will "
          f"offer them again.")
    interested = [g(r, "Company") for r in rows[1:] if g(r, "Status").lower() in ("replied", "interested")]
    if interested:
        print("  Follow up personally: " + ", ".join(interested))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("action", choices=["export", "sync"])
    ap.add_argument("file", nargs="?", default="")
    ap.add_argument("--all", action="store_true")
    a = ap.parse_args()
    if a.action == "sync":
        sync(a)
    else:
        D.IGNORE_TEAM = a.all       # a full re-issue must see leads already handed out
        export(a)


if __name__ == "__main__":
    main()
