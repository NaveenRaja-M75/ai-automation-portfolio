#!/usr/bin/env python3
"""
Self-audit. Run it after any change, and before you trust a batch.

It exists because every serious bug in this system was found by testing a
real run and inspecting the survivors, never by reading the code. A token
leaked into notes, a template auto-approved every lead, and a status field
silently disagreed with its folder. Each is now a check below.
"""
import sys, os, json, re
from pathlib import Path
sys.path.insert(0,'engine'); sys.path.insert(0,'engine/lib')
import vault as V

FAIL=[]; WARN=[]; OK=[]
def chk(cond, msg, hard=True):
    (OK if cond else (FAIL if hard else WARN)).append(msg)

# --- SECURITY -------------------------------------------------------------
gi = Path(".gitignore").read_text() if Path(".gitignore").exists() else ""
chk(".env" in gi, ".env is gitignored")
chk("_state" in gi, "_state/ (ledger + send log) is gitignored")
leaked=[]
for p in Path("vault").rglob("*.md"):
    t=p.read_text(encoding="utf-8",errors="ignore")
    if re.search(r"EAA[A-Za-z0-9]{20,}|AIzaSy[A-Za-z0-9_\-]{20,}", t): leaked.append(str(p))
chk(not leaked, f"no credential in any vault note ({len(list(Path('vault').rglob('*.md')))} scanned)")
chk(Path(".env").exists(), ".env present for the engine to read")

# --- MEMORY / STATE -------------------------------------------------------
seen=V.load_seen()
chk(len(seen)>0, f"ledger loaded: {len(seen)} names")
try:
    json.loads((V.STATE/"seen.json").read_text()); chk(True,"ledger is valid JSON")
except Exception as e: chk(False,f"ledger corrupt: {e}")
chk(isinstance(V.blocklist(),set), "do-not-contact list parses")

# --- STATE MACHINE INTEGRITY ---------------------------------------------
mismatch=[]
for stage in V.STAGES:
    for p in V.notes_in(stage):
        fm,_=V.read_note(p)
        if fm.get("status") and fm["status"]!=stage: mismatch.append(f"{p.name}: fm={fm['status']} folder={stage}")
chk(not mismatch, "every note's status matches its folder", hard=False)
if mismatch: WARN.extend(mismatch[:3])

# --- COLLISION RISK -------------------------------------------------------
slugs={}
for stage in V.STAGES:
    for p in V.notes_in(stage): slugs.setdefault(p.stem,[]).append(stage)
dupes={k:v for k,v in slugs.items() if len(v)>1}
chk(not dupes, "no note exists in two stages at once")

# --- ERROR HANDLING PROBES ------------------------------------------------
import source, dispatch
chk(hasattr(source,"content_farm"), "content-farm filter present")
chk(hasattr(source,"relevant"), "niche relevance gate present")
chk(hasattr(source,"safe_ads_link"), "token-free ad link builder present")
chk("SystemExit" in Path("engine/source.py").read_text(), "token expiry aborts cleanly instead of silently returning junk")
chk(dispatch.DAILY_CAP>0, f"daily send cap set ({dispatch.DAILY_CAP})")
chk("free" in dispatch.BANNED, "spam-word linter active")
chk("—" in dispatch.AI_TELLS, "em-dash blocked as AI tell")
# approval must be human
chk(V.APPROVE_TAG.search(V._tags_only("`#approved`")) is None, "backticked tag cannot auto-approve")
chk(V.APPROVE_TAG.search(V._tags_only("#approved")) is not None, "bare tag does approve")
# blocked beats approved
chk("blocked" in Path("engine/dispatch.py").read_text(), "dispatch checks do-not-contact before sending")

# --- CODE INTEGRITY -------------------------------------------------------
# A shell heredoc once turned backslash-b into a backspace character inside
# the brand filter. The regex still compiled, matched nothing, and pages
# named "...Vault" and "...Hub" sailed through for weeks. No error, no test
# failure: only a check for the character itself catches it.
bad_chars = [p.name for p in Path("engine").rglob("*.py")
             if "\x08" in p.read_text(encoding="utf-8", errors="ignore")]
chk(not bad_chars, "no backspace characters hiding inside any regex"
    + (f" (found in {', '.join(bad_chars)})" if bad_chars else ""))
chk(bool(source.NOT_BRAND.search("The Curiosity Vault")) and
    bool(source.NOT_BRAND.search("Wellness Boost Hub")),
    "brand filter still rejects vault/hub arbitrage pages")
import draft_all
chk(draft_all.brand_evidence_level({"brand": "x"}) == "none",
    "a brand with no verified ads is never told it is running ads")
import cadence
chk(cadence.routes({"phone": "+44 1273 675556", "phone_type": "landline"}) == [],
    "landlines never go on the WhatsApp page")
_probe = {"brand": "X", "city": "", "tags": ["lead", "agency"], "evidence": {"no_video": "confirmed"}}
_back, _ = V._fm_parse("---\n" + V._fm_dump(_probe) + "\n---\n\nb")
chk(_back == _probe, "a note saved and read back keeps its tags, blanks and nested fields")

# --- LINKEDIN ENGINE ------------------------------------------------------
_li_src = "".join(p.read_text(encoding="utf-8") for p in Path("engine").glob("li_*.py"))
chk(not re.search(r"li_at|JSESSIONID|cookies\s*=|session_key|/uas/login-submit", _li_src),
    "LinkedIn engine never sends a LinkedIn cookie or logs in")
import li_lib as _L, li_build as _LB, li_dedupe as _D
chk(_LB.judge_post({"text": "I'm a freelance UGC creator. DM me for AI UGC ads!",
                    "date": _L.TODAY.isoformat()}, 21) != "",
    "a seller advertising AI UGC is never treated as a buyer")
chk(_LB.judge_post({"text": "Looking for an AI UGC creator for our DTC skincare brand.",
                    "date": _L.TODAY.isoformat()}, 21) == "",
    "a real 'looking for AI UGC' post is kept")
_b = _D.Batch()
_b.claim("first", "Acme Media Pvt. Ltd.", "", "https://www.acme.com/about")
chk(bool(_b.claim("second", "ACME MEDIA", "")) and bool(_b.claim("third", "Other", "", "acme.com")),
    "LinkedIn dedupe matches a company by cleaned name and by website")
_vn = next((p for s in V.STAGES for p in V.notes_in(s)), None)
if _vn:
    _fm, _ = V.read_note(_vn)
    chk(bool(_D.Known().check(_fm.get("brand") or _vn.stem)[0]),
        "a lead already in the vault is never shown again by the LinkedIn engine")
_s = _L.score_job({"title": "Video Editor", "posted": _L.TODAY.isoformat()},
                  {"kind": "agency", "size": "11-50 employees"}, _L.config())[0]
_a = _L.score_job({"title": "AI UGC Creator", "posted": _L.TODAY.isoformat()},
                  {"kind": "agency", "size": "11-50 employees"}, _L.config())[0]
chk(_a - _s >= 25, "AI generation roles outrank plain editing roles by a clear margin")
chk(_LB.judge_post({"text": "Stop hiring video editors. Every brand needs AI UGC now.",
                    "date": _L.TODAY.isoformat()}, 21) != "",
    "a 'stop hiring editors' sales hook is never treated as a buyer")
chk(_LB.safe("=HYPERLINK(1)").startswith("'") and _LB.safe("a\x01b") == "ab",
    "scraped text cannot run as an Excel formula or crash the workbook save")
import dispatch as _DP
chk(_DP.pick_channel({"lead_type": "post", "signal_url": "https://x"}, {"linkedin": "hi"}) == "linkedin",
    "an approved LinkedIn post lead dispatches to LinkedIn instead of being skipped")
import cadence as _CD
chk("linkedin" in _CD.routes({"lead_type": "post", "signal_url": "https://x"}),
    "a LinkedIn post lead gets its follow-up nudge")
import li_selftest as _ST
_miss = _ST.run()
chk(not _miss, f"all {len(_ST.BUYERS) + len(_ST.NOT_BUYERS)} labelled LinkedIn posts judged "
    f"correctly" + (f" (first miss: {_miss[0][:60]})" if _miss else ""))
chk(_D.unsquash("adbhuttalesprivatelimited") == "adbhuttales" and _D.unsquash("disco") == "disco",
    "a ledger name stored with 'Pvt Ltd' still blocks the same company listed without it")
chk(_D.key_dom("https://youtube.com/@x") == "" and _D.key_dom("blissta.co") == "blissta.co",
    "YouTube, Instagram and Linktree links never count as a company's identity")
chk(_L.company_kind("Synthesia", "Software Development", "", _L.config()) == "vendor" and
    _L.company_kind("Metamorph Studio", "Media Production", "", _L.config()) != "vendor",
    "AI tool makers are never pitched, without catching look-alike names")
import li_vault as _LV
chk(_LV.role_short("Senior Motion / VFX / AI") == "" and
    _LV.role_short("AI Generalist Melbourne 2026", "Melbourne, Victoria, Australia") == "AI Generalist",
    "messy job titles are never pasted into a draft")
_hn = _LV.hiring_note({"company": "Acme Media Pvt. Ltd.", "title": "AI Video Producer", "kind": "agency",
                       "location": "London, United Kingdom", "market": "foreign", "score": 80,
                       "tier": "A", "posted": "", "link": "", "why": ""})
chk("clients" in _hn[2]["email"] and "re:" not in _hn[2]["email"].split("\n")[0] and
    "Hi Acme Media team" in _hn[2]["email"],
    "agency hiring drafts offer white-label for clients, clean greeting, no fake 're:' subject")
chk(all(_LV.words(t) <= _LV.CAPS[c] for c, t in _hn[2].items()),
    "LinkedIn drafts stay inside VOICE.md length caps")
_da = Path("engine/draft_all.py").read_text(encoding="utf-8")
chk('in ("post", "hiring")' in _da, "draft_all never overwrites a LinkedIn post or hiring draft")

# --- FOREIGN WORKBOOK -----------------------------------------------------
wbp = Path("Monarc_Foreign_Leads.xlsx")
if wbp.exists():
    import zipfile
    blob = b"".join(zipfile.ZipFile(wbp).read(n) for n in zipfile.ZipFile(wbp).namelist())
    chk(b"access_token" not in blob and not re.search(rb"EAA[A-Za-z0-9]{20,}", blob),
        "no Meta token anywhere inside the foreign leads workbook")

print("="*64); print("  MONARC LEAD OS — AUDIT"); print("="*64)
for m in OK:   print("  PASS  ", m)
for m in WARN: print("  WARN  ", m)
for m in FAIL: print("  FAIL  ", m)
print("="*64)
print(f"  {len(OK)} passed, {len(WARN)} warnings, {len(FAIL)} failures")
