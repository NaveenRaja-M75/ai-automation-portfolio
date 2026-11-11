#!/usr/bin/env python3
"""
LinkedIn engine, build: de-duplicate, judge, score, find contacts, write the workbook.

    python engine/li_build.py            -> Monarc_LinkedIn_Leads.xlsx

Reads only _state/li_state.json plus company websites. Never LinkedIn itself,
so a rebuild is safe to run as often as you like.

ONE LEAD, ONE ROW (li_dedupe.py)
    Every candidate is checked against the vault, the ledger, the do-not-contact
    list, Gmail send history and the dispatch log, on name, email, email domain,
    website and LinkedIn identity. Then rows are de-duplicated across the four
    sheets, hottest signal first: a company that posted, is hiring AND matched a
    niche search appears once, under its post.

WHAT GETS THROWN OUT, AND WHY
    - posts that are not somebody asking: freelancers advertising, agencies
      selling AI UGC, "open to work". Sellers use the same words as buyers.
    - closed jobs: the posting page no longer has a description.
    - staffing firms and job aggregators: they repost for clients you cannot see.
    - enterprises (10,001+ staff): in-house teams and procurement, not a buyer.
    - niche companies outside the size band in LINKEDIN.md.
Every skipped row goes to the Skipped sheet with its reason, so a wrong call
is visible and the filter can be fixed instead of silently losing leads.

HOUSEKEEPING
    - "New" column: NEW on the first build that shows a lead, so a daily run
      only asks you to read what changed.
    - posts and jobs older than 90 days are pruned from state, keeping only
      their id so they are never fetched again. Holding less personal data
      for less time is also what India's DPDP Act expects.
    - every build appends its counts to _state/li_runs.json, so yield per
      stream is measurable over weeks, not guessed.
"""
import re
import sys
import json
import datetime
from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment
from openpyxl.utils import get_column_letter
from openpyxl.cell.cell import ILLEGAL_CHARACTERS_RE

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))
import li_lib as L
import li_dedupe as D
import vault as V
from contact_enricher import emails_from, instagram_from, phones_from, mx_ok

OUT = L.ROOT / "Monarc_LinkedIn_Leads.xlsx"
RUNS = L.STATE / "li_runs.json"
FREE = D.FREE
CONTACT_PATHS = ["", "/contact", "/contact-us", "/pages/contact", "/about", "/about-us"]
KEEP_DAYS = 90


def dom(url):
    return D.key_dom(url)


# ------------------------------------------------------------------ contacts
def contacts(s, website, market):
    """Email / Instagram / phone read off the company's own site. Cached."""
    d = dom(website)
    if not d:
        return {}
    if d in s["contacts"]:
        return s["contacts"][d]
    got = {"emails": [], "instagram": "", "phone": ""}
    for p in CONTACT_PATHS:
        html = L.site_get(f"https://{d}{p}")
        if not html:
            continue
        for e in emails_from(html):
            ed = e.lower().split("@")[1]
            if ed.endswith(d) or d.endswith(ed) or ed.startswith(FREE):
                got["emails"].append(e.lower())
        got["instagram"] = got["instagram"] or instagram_from(html)
        if market == "india" and not got["phone"]:
            ph = phones_from(html)       # foreign sites invent +91 numbers here
            got["phone"] = ph[0] if ph else ""
        if got["emails"] and got["instagram"]:
            break
    got["emails"] = list(dict.fromkeys(got["emails"]))[:3]
    got["email"] = got["emails"][0] if got["emails"] else ""
    got["email_check"] = mx_ok(got["email"]) if got["email"] else ""
    s["contacts"][d] = got
    return got


# ------------------------------------------------------------------ judging
def judge_post(p, days, cfg=None):
    t = p.get("text", "")
    if p.get("dead") or not t:
        return "page gone or private"
    if L.age_days(p.get("date")) > days:
        return f"older than {days} days"
    if L.SEEKER.search(t):
        return "seller, job-seeker or recruiter, not a buyer"
    if cfg:
        who = f"{p.get('author', '')} {' '.join(p.get('emails') or [])}"
        if L.matches_word(who, cfg["skip_words"]):
            return "recruiter / staffing (author or email)"
    if not L.STRONG_ASK.search(t):
        return "no ask in the post"
    if not (L.ROLE_RX.search(t) or L.AI_GEN.search(t)):
        return "off topic"
    return ""


def evidence(desc):
    """One sentence from the posting that proves the AI need, verbatim."""
    sents = [x.strip() for x in re.split(r"(?<=[.!?\n])\s+", desc or "") if 20 < len(x) < 260]
    # Best proof names AI AND the work (video, ads, content, UGC). "We are a data
    # partner for AI companies" mentions AI but proves nothing about video.
    work = re.compile(r"video|ads?\b|advert|content|ugc|creative|film|commercial|reel|"
                      r"campaign|motion|visual", re.I)
    for x in sents:
        if L.AI_GEN.search(x) and work.search(x):
            return x
    for rx in (L.UGC, L.PRODUCT_LINE):
        for x in sents:
            if rx.search(x):
                return x
    return ""


def prune(s):
    """Drop posts and jobs older than KEEP_DAYS, keep a tombstone id."""
    gone = s.setdefault("gone", {})
    n = 0
    for bucket, date_key in (("posts", "date"), ("jobs", "posted")):
        for k in list(s[bucket]):
            r = s[bucket][k]
            when = r.get(date_key) or r.get("found")
            if when and L.age_days(when) > KEEP_DAYS:
                gone[k] = r.get(date_key) or ""
                del s[bucket][k]
                n += 1
    return n


def stamp_new(s, key):
    fs = s.setdefault("first_seen", {})
    fs.setdefault(key, L.TODAY.isoformat())
    return "NEW" if fs[key] == L.TODAY.isoformat() else fs[key]


def build_rows(cfg, s):
    days = cfg["settings"]["max_age_days"]
    lo, hi = cfg["settings"]["min_company_size"], cfg["settings"]["max_company_size"]
    known, batch = D.Known(), D.Batch()
    posts, hiring, agencies, brands, skipped = [], [], [], [], []
    skip = lambda stream, name, why, link: skipped.append([stream, name, why, link])

    # ---------------------------------------------------------- 1. posts
    # Newest first, so when one author posted twice the fresher ask wins.
    for p in sorted(s["posts"].values(), key=lambda x: x.get("date") or "", reverse=True):
        url = p.get("url", "")
        why_not = judge_post(p, days, cfg)
        if not why_not:
            email = (p.get("emails") or [""])[0]
            stop, tag = known.check(p.get("author"), email, "", [url, p.get("author_url")])
            why_not = stop or batch.claim(f"post by {p.get('author')}", p.get("author"),
                                          email, "", [url, p.get("author_url")])
        if why_not:
            skip("post", p.get("author", ""), why_not, url)
            continue
        sc, why = L.score_post(p)
        posts.append({**p, "score": sc, "tier": L.tier(sc), "why": ", ".join(why),
                      "need": L.need_phrase(p["text"]), "email": email, "also": tag,
                      "new": stamp_new(s, "post:" + D.key_li(url))})

    # ---------------------------------------------------------- 2. hiring
    cos = s["companies"]
    roles_by_co = {}
    for j in s["jobs"].values():
        roles_by_co.setdefault(j.get("company_url") or D.key_name(j["company"]), []).append(j)

    best = {}
    for j in s["jobs"].values():
        if "description" not in j or L.WRONG_ROLE.search(j["title"]):
            continue
        k = j.get("company_url") or D.key_name(j["company"])
        co = cos.get(j.get("company_url"), {})
        if not co.get("fetched") or co.get("dead"):
            # Company page not read yet (budget ran out). The job posting
            # carries LinkedIn's industry label, enough to tell agency from
            # brand from staffing until the page is read on the next run.
            co = {**co, "industry": j.get("industries", ""),
                  "kind": L.company_kind(j["company"], j.get("industries", ""), "", cfg)}
        sc, why = L.score_job(j, co, cfg)
        if k not in best or sc > best[k][0]:
            best[k] = (sc, why, j, co)
    # Every company with jobs on file but no row is reported, never dropped
    # silently: either no title fit, or it was not read (already pitched,
    # skip-listed, or waiting for the next run's budget).
    for k, jobs_ in roles_by_co.items():
        if k in best:
            continue
        j0 = jobs_[0]
        if all(L.WRONG_ROLE.search(x["title"]) for x in jobs_):
            why0 = "not an AI/ads role: " + "; ".join(sorted({x["title"][:40] for x in jobs_}))[:120]
        elif known.check(j0["company"], "", "", j0.get("company_url"))[0]:
            why0 = known.check(j0["company"], "", "", j0.get("company_url"))[0]
        elif L.matches_word(j0["company"], cfg["skip_words"]):
            why0 = "staffing / recruiter / job board"
        elif L.matches_word(j0["company"], cfg["never_companies"], whole=True):
            why0 = "AI tool maker or platform, not a client"
        else:
            why0 = "description not read yet (next run)"
        skip("job", j0["company"], why0, j0.get("link", ""))

    max_size = cfg["settings"]["max_hiring_company_size"]
    for sc, why, j, co in sorted(best.values(), key=lambda x: -x[0]):
        sz = L.size_range(co.get("size"))
        reason = ""
        if not j.get("description"):
            reason = "job closed or removed"
        elif co.get("kind") == "staffing" or L.matches_word(j["company"], cfg["skip_words"]):
            reason = "staffing / recruiter / job board"
        elif co.get("kind") == "vendor":
            reason = "AI tool maker or platform, not a client"
        elif sz and sz[0] > max_size:
            reason = f"too big ({co.get('size')}), hires in-house"
        if not reason:
            reason, tag = known.check(j["company"], "", co.get("website"),
                                      j.get("company_url"))
        if not reason:
            reason = batch.claim(f"hiring at {j['company']}", j["company"], "",
                                 co.get("website"), j.get("company_url"))
        if reason:
            skip("job", j["company"], reason, j["link"])
            continue
        t = L.tier(sc)
        # Size unknown means the company page is not read yet. It could be
        # Netflix. Held at tier C (out of the vault) until the page proves
        # it is a fit, which the next run does.
        if not sz:
            t = "C"
            why = why + ["size unverified, held at C until the company page is read"]
        # A website visit costs up to six page loads. Spent on A and B only:
        # tier C never goes to the vault, and a cached result is reused for free.
        c = (contacts(s, co.get("website"), j.get("market")) if t in ("A", "B")
             else s["contacts"].get(dom(co.get("website")), {}))
        hiring.append({**j, "score": sc, "tier": t, "why": ", ".join(why),
                       "kind": co.get("kind", ""), "size": co.get("size", ""),
                       "industry": co.get("industry", ""), "hq": co.get("hq", ""),
                       "website": dom(co.get("website")), "evidence": evidence(j.get("description")),
                       "open_roles": len(roles_by_co.get(j.get("company_url") or
                                                         D.key_name(j["company"]), [])),
                       "also": tag, "new": stamp_new(s, "job:" + (j.get("company_url") or
                                                                 D.key_name(j["company"]))), **c})

    # ---------------------------------------------------------- 3. niche
    for co in sorted(cos.values(), key=lambda c: c.get("url", "")):
        if co.get("from") != "niche" or not co.get("fetched"):
            continue
        name = co.get("name") or co["url"].rsplit("/", 1)[-1]
        sz = L.size_range(co.get("size"))
        reason = ""
        if co.get("kind") == "vendor":
            reason = "AI tool maker or platform, not a client"
        elif co.get("kind") in ("staffing", "other"):
            reason = f"not an agency or brand ({co.get('industry') or 'no industry'})"
        elif sz and not (lo <= sz[1] and sz[0] <= hi):
            reason = f"size {co.get('size')} outside {lo}-{hi}"
        elif not co.get("website"):
            reason = "no website on LinkedIn"
        tag = ""
        if not reason:
            reason, tag = known.check(name, "", co.get("website"), co["url"])
        if not reason:
            reason = batch.claim(f"niche {name}", name, "", co.get("website"), co["url"])
        if reason:
            skip("niche", name, reason, co["url"])
            continue
        co["hiring"] = bool(roles_by_co.get(co["url"]))
        sc, why = L.score_company(co)
        mkt = L.market_of(co.get("hq", ""))
        row = {**co, "name": name, "score": sc, "tier": L.tier(sc), "why": ", ".join(why),
               "market": mkt, "website": dom(co.get("website")), "also": tag,
               "new": stamp_new(s, "co:" + co["url"]),
               **contacts(s, co.get("website"), mkt)}
        (agencies if co["kind"] == "agency" else brands).append(row)

    key = lambda r: -r["score"]
    return (sorted(posts, key=key), sorted(hiring, key=key), sorted(agencies, key=key),
            sorted(brands, key=key), skipped)


# ------------------------------------------------------------------ workbook
HEAD = PatternFill("solid", fgColor="1F2937")
TIERS = {"A": "DCFCE7", "B": "FEF9C3", "C": "F3F4F6"}
NEWFILL = PatternFill("solid", fgColor="DBEAFE")


def safe(v):
    """Make scraped text safe for a cell.

    Post text is written by strangers. A control character crashes the whole
    save (openpyxl IllegalCharacterError), and a value starting with = + - @
    is executed by Excel as a formula. Both are stripped or neutralised here.
    """
    if isinstance(v, (int, float)):
        return v
    v = ILLEGAL_CHARACTERS_RE.sub("", str(v if v is not None else ""))
    if v[:1] in ("=", "+", "-", "@") and not v.startswith("http"):
        v = "'" + v
    return v[:32000]


def sheet(wb, title, cols, rows, links=()):
    ws = wb.create_sheet(title)
    ws.append([c[0] for c in cols])
    for i, (name, _, width) in enumerate(cols, 1):
        cell = ws.cell(row=1, column=i)
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = HEAD
        ws.column_dimensions[get_column_letter(i)].width = width
    for r in rows:
        ws.append([safe(r.get(k, "") if isinstance(r.get(k, ""), (str, int, float))
                        else ", ".join(map(str, r.get(k) or []))) for _, k, _ in cols])
        n = ws.max_row
        t = r.get("tier")
        if t in TIERS:
            ws.cell(row=n, column=1).fill = PatternFill("solid", fgColor=TIERS[t])
        for i, (_, k, _) in enumerate(cols, 1):
            v = str(r.get(k, "") or "")
            cell = ws.cell(row=n, column=i)
            cell.alignment = Alignment(vertical="top",
                                       wrap_text=k in ("text", "evidence", "why", "about"))
            if k == "new" and v == "NEW":
                cell.fill = NEWFILL
                cell.font = Font(bold=True)
            if k in links and v.startswith("http"):
                cell.hyperlink = v
                cell.font = Font(color="2563EB", underline="single")
    ws.freeze_panes = "C2"
    ws.auto_filter.ref = ws.dimensions
    return ws


def write(posts, hiring, agencies, brands, skipped):
    wb = Workbook()
    ws = wb.active
    ws.title = "Start here"
    tc = lambda rows: "/".join(str(sum(1 for r in rows if r["tier"] == t)) for t in "ABC")
    nc = lambda rows: sum(1 for r in rows if r.get("new") == "NEW")
    lines = [
        ("Monarc LinkedIn Leads", True),
        (f"Built {datetime.datetime.now():%Y-%m-%d %H:%M} from public LinkedIn pages. No login used.", False),
        ("", False),
        ("Sheet          Leads  New   A/B/C       What to do", True),
        (f"Hot posts      {len(posts):>5} {nc(posts):>4}   {tc(posts):<11} Reply within 48h. They asked in public, speed wins", False),
        (f"Hiring         {len(hiring):>5} {nc(hiring):>4}   {tc(hiring):<11} Pitch 'AI ads while you hire', quote the role", False),
        (f"Agencies       {len(agencies):>5} {nc(agencies):>4}   {tc(agencies):<11} White-label offer, as in AGENCY_PITCH.md", False),
        (f"Niche brands   {len(brands):>5} {nc(brands):>4}   {tc(brands):<11} Coldest. Only with a sample made for their product", False),
        (f"Skipped        {len(skipped):>5}                    Every rejected row and why. Scan it once a week", False),
        ("", False),
        ("No duplicates: each business appears once across all sheets, and nothing already in the", False),
        ("vault, the ledger, Gmail send history or DO_NOT_CONTACT is shown. Those go to Skipped.", False),
        ("New = first time this lead appeared. Blue NEW cells are today's.", False),
        ("Tier A = score 70+, B = 50-69, C = below 50. The 'Why' column explains each score.", False),
        ("Load A and B into the vault with drafts:  python run.py linkedin vault", False),
        ("Messaged someone straight from this sheet? Type yes in its Contacted cell. The next build", False),
        ("records them and never shows them again.", False),
    ]
    for text, bold in lines:
        ws.append([text])
        ws.cell(row=ws.max_row, column=1).font = Font(
            bold=bold, size=14 if text.startswith("Monarc") else 11, name="Consolas")
    ws.column_dimensions["A"].width = 110

    sheet(wb, "Hot posts", [
        ("Tier", "tier", 6), ("Contacted", "contacted", 10), ("New", "new", 11), ("Score", "score", 7), ("Posted", "date", 11),
        ("Who", "author", 22), ("They asked for", "need", 30), ("Why", "why", 30),
        ("Email in post", "email", 28), ("Post", "url", 18), ("Profile", "author_url", 18),
        ("Followers", "followers", 10), ("Also in", "also", 18), ("Post text", "text", 80)],
        posts, links=("url", "author_url"))
    sheet(wb, "Hiring", [
        ("Tier", "tier", 6), ("Contacted", "contacted", 10), ("New", "new", 11), ("Score", "score", 7), ("Company", "company", 26),
        ("Type", "kind", 9), ("Role", "title", 30), ("Posted", "posted", 11),
        ("Market", "market", 9), ("Location", "location", 22), ("Size", "size", 16),
        ("Industry", "industry", 22), ("Website", "website", 22), ("Email", "email", 28),
        ("Email check", "email_check", 11), ("Instagram", "instagram", 18), ("Phone", "phone", 15),
        ("Open roles", "open_roles", 9), ("Applicants", "applicants", 18), ("Salary", "salary", 16),
        ("Why", "why", 34), ("Proof from the posting", "evidence", 60), ("Job", "link", 16),
        ("Company LinkedIn", "company_url", 18), ("Also in", "also", 18)],
        hiring, links=("link", "company_url"))
    cols = [("Tier", "tier", 6), ("Contacted", "contacted", 10), ("New", "new", 11), ("Score", "score", 7), ("Name", "name", 26),
            ("Industry", "industry", 22), ("Size", "size", 16), ("HQ", "hq", 20),
            ("Market", "market", 9), ("Website", "website", 22), ("Email", "email", 28),
            ("Email check", "email_check", 11), ("Instagram", "instagram", 18),
            ("Phone", "phone", 15), ("Followers", "followers", 10),
            ("Last LinkedIn post", "last_post", 12), ("Why", "why", 28),
            ("LinkedIn", "url", 18), ("Also in", "also", 18), ("About", "about", 60)]
    sheet(wb, "Agencies", cols, agencies, links=("url",))
    sheet(wb, "Niche brands", cols, brands, links=("url",))
    sheet(wb, "Skipped", [("Stream", "s", 8), ("Name", "n", 30), ("Reason", "r", 44),
                          ("Link", "u", 60)],
          [{"s": a, "n": b, "r": c, "u": d} for a, b, c, d in skipped], links=("u",))
    try:
        wb.save(OUT)
    except PermissionError:
        alt = OUT.with_name(OUT.stem + f"_{datetime.datetime.now():%H%M}.xlsx")
        wb.save(alt)
        print(f"  {OUT.name} is open in Excel, saved as {alt.name} instead")
        return alt
    return OUT


CONTACT_SHEETS = {   # sheet -> (name col, email col, website col, linkedin cols)
    "Hot posts": ("Who", "Email in post", None, ("Post", "Profile")),
    "Hiring": ("Company", "Email", "Website", ("Company LinkedIn",)),
    "Agencies": ("Name", "Email", "Website", ("LinkedIn",)),
    "Niche brands": ("Name", "Email", "Website", ("LinkedIn",)),
}


def harvest_contacted():
    """Leads you marked in the last workbook's Contacted column go in the ledger.

    Without this, a lead reached by hand straight from the workbook was never
    recorded anywhere, and the next build offered it again as new. Read before
    the workbook is overwritten. Hyperlinked cells hold the URL as their value.
    """
    if not OUT.exists():
        return 0
    try:
        from openpyxl import load_workbook
        wb = load_workbook(OUT, read_only=True)
    except Exception as e:
        print(f"  could not read the old workbook's Contacted column ({e.__class__.__name__}); "
              f"close it in Excel and rebuild to record them")
        return 0
    names, n = [], 0
    for title, (nc, ec, wc, lcs) in CONTACT_SHEETS.items():
        if title not in wb.sheetnames:
            continue
        rows = list(wb[title].iter_rows(values_only=True))
        if not rows or "Contacted" not in rows[0]:
            continue
        h = list(rows[0])
        col = lambda r, c: (str(r[h.index(c)] or "") if c and c in h else "")
        for r in rows[1:]:
            if not col(r, "Contacted").strip():
                continue
            name = col(r, nc)
            D.remember_imported(D.identities(name, col(r, ec), col(r, wc),
                                             [col(r, c) for c in lcs]))
            names.append(name)
            n += 1
    wb.close()
    if names:
        V.remember(names)
    return n


def log_run(rows):
    try:
        runs = json.loads(RUNS.read_text(encoding="utf-8"))
    except Exception:
        runs = []
    posts, hiring, agencies, brands, skipped = rows
    entry = {"at": datetime.datetime.now().isoformat(timespec="minutes")}
    for name, r in (("posts", posts), ("hiring", hiring), ("agencies", agencies),
                    ("brands", brands)):
        entry[name] = {"total": len(r), "new": sum(1 for x in r if x.get("new") == "NEW"),
                       "A": sum(1 for x in r if x["tier"] == "A")}
    entry["skipped"] = len(skipped)
    runs.append(entry)
    RUNS.write_text(json.dumps(runs[-200:], indent=1), encoding="utf-8")


def main():
    L.lock()
    cfg, s = L.config(), L.load_state()
    print("\n=== LinkedIn build: de-duplicating, scoring, finding contacts")
    marked = harvest_contacted()
    if marked:
        print(f"  {marked} leads marked Contacted in the last workbook were added to the "
              f"ledger and will not be shown again")
    pruned = prune(s)
    if pruned:
        print(f"  pruned {pruned} posts/jobs older than {KEEP_DAYS} days")
    rows = build_rows(cfg, s)
    L.save_state(s)
    path = write(*rows)
    log_run(rows)
    posts, hiring, agencies, brands, skipped = rows
    for name, r in (("Hot posts", posts), ("Hiring", hiring), ("Agencies", agencies),
                    ("Niche brands", brands)):
        a = sum(1 for x in r if x["tier"] == "A")
        b = sum(1 for x in r if x["tier"] == "B")
        new = sum(1 for x in r if x.get("new") == "NEW")
        reach = sum(1 for x in r if x.get("email") or x.get("instagram") or x.get("author_url"))
        print(f"  {name:<13} {len(r):>4} leads  new {new:<4} A {a:<3} B {b:<3} reachable {reach}")
    reasons = {}
    for _, _, why, _ in skipped:
        k = re.sub(r"\(.*\)|duplicate of .*", lambda m: "duplicate" if m.group(0).startswith("dup") else "", why)
        k = k.split(":")[0].strip(" ,")
        reasons[k] = reasons.get(k, 0) + 1
    print(f"  Skipped       {len(skipped):>4}  " +
          ", ".join(f"{k}: {v}" for k, v in sorted(reasons.items(), key=lambda x: -x[1])[:6]))
    print(f"\n  Workbook: {path}")


if __name__ == "__main__":
    main()
