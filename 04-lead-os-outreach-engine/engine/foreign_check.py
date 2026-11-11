#!/usr/bin/env python3
"""
Check Monarc_Foreign_Leads.xlsx and report contact coverage.

    python engine/foreign_check.py

Fails (exit 1) on: a Meta token anywhere, a business on two lead sheets,
a malformed email/phone/WhatsApp/Instagram cell, a ready message that fails
the spam linter or claims live ads the row cannot prove.
"""
import re
import sys
import zipfile
import collections
from pathlib import Path

import openpyxl

sys.path.insert(0, str(Path(__file__).resolve().parent))
from dispatch import lint

P = Path(__file__).resolve().parent.parent / "Monarc_Foreign_Leads.xlsx"
wb = openpyxl.load_workbook(P)
MAXR = {ws.title: ws.max_row for ws in wb.worksheets}
fail = []


def ok(cond, msg):
    if not cond:
        fail.append(msg)


blob = b"".join(zipfile.ZipFile(P).read(n) for n in zipfile.ZipFile(P).namelist())
ok(b"access_token" not in blob and not re.search(rb"EAA[A-Za-z0-9]{20,}", blob),
   "Meta token found inside the workbook")

LEAD = ("Agencies", "D2C Brands", "Other businesses")
seen = collections.defaultdict(list)
cover = {}
for sh in LEAD + ("Agencies - form only", "Brands - Messenger only", "Indian brands abroad", "Not worth contacting"):
    ws = wb[sh]
    H = {ws.cell(1, i).value: i for i in range(1, ws.max_column + 1)}
    name_col = H.get("Agency") or H.get("Brand") or H.get("Name")
    c = collections.Counter()
    for r in range(2, MAXR[sh] + 1):
        name = ws.cell(r, name_col).value
        if not name:
            continue
        c["total"] += 1
        seen[re.sub(r"[^a-z0-9]", "", str(name).lower())].append(f"{sh}!{r}")
        if sh not in LEAD:
            continue
        e, p = ws.cell(r, H["Email"]).value, ws.cell(r, H["Phone"]).value
        i, w = ws.cell(r, H["Instagram"]).value, ws.cell(r, H["WhatsApp"]).value
        c["email"] += bool(e)
        c["instagram"] += bool(i)
        c["phone"] += bool(p)
        c["whatsapp"] += bool(w)
        c["all three"] += bool(e and i and p)
        c["none"] += not (e or i or p)
        if e:
            ok(re.fullmatch(r"[\w.+-]+@[\w-]+(\.[\w-]+)+", e), f"{sh}!{r} bad email {e}")
        if p:
            ok(re.fullmatch(r"\+\d{1,3} \d{6,12}", p), f"{sh}!{r} bad phone {p}")
        if w:
            ok(re.fullmatch(r"https://wa\.me/\d{8,15}", ws.cell(r, H["WhatsApp"]).hyperlink.target),
               f"{sh}!{r} bad WhatsApp link")
        if i:
            ok(re.fullmatch(r"@[a-z0-9._]{3,30}", i), f"{sh}!{r} bad Instagram {i}")
        m = ws.cell(r, H["Ready first message"]).value or ""
        nm = str(ws.cell(r, H["Say hi to"]).value or "")
        ok(m and not lint(m.replace(nm, " ") if nm else m) and "{" not in m
           and not re.search(r"https?://", m),
           f"{sh}!{r} ready message missing or fails lint")
        if "running quite a few" in m:
            ev = str(ws.cell(r, H["Ads today"]).value or "")
            n = re.match(r"(\d+) live ads", ev)
            ok(bool(n) and int(n.group(1)) >= 4, f"{sh}!{r} claims live ads without proof")
    cover[sh] = c

dups = {k: v for k, v in seen.items() if len(v) > 1}
ok(not dups, f"{len(dups)} businesses appear on two sheets: {list(dups.items())[:3]}")

for sh, c in cover.items():
    if sh in LEAD:
        n = c["total"] or 1
        print(f"{sh:<24} {c['total']:>5} leads | email {c['email']} ({c['email']*100//n}%) | "
              f"instagram {c['instagram']} ({c['instagram']*100//n}%) | phone {c['phone']} "
              f"({c['phone']*100//n}%) | whatsapp {c['whatsapp']} | all three {c['all three']} "
              f"| no contact {c['none']}")
    else:
        print(f"{sh:<24} {c['total']:>5} leads")
print(f"\nFAILURES: {len(fail)}")
for f in fail[:25]:
    print("  -", f)
sys.exit(1 if fail else 0)
