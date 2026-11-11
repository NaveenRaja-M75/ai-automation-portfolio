#!/usr/bin/env python3
"""
LinkedIn engine, collection: the three streams.

    python engine/li_collect.py posts    "looking for AI UGC / AI commercial" posts
    python engine/li_collect.py jobs     open AI video / AI UGC / AI creative roles
    python engine/li_collect.py niche    agencies and D2C brands that fit Monarc

Everything lands in _state/li_state.json. Each search is marked done for 12
hours, so a stopped run picks up where it left off and a repeat run the same
day costs nothing. li_build.py turns the state into the workbook.

Order of value, which is also the order `linkedin all` runs them in:
    posts  somebody asked for exactly this, in public, days ago
    jobs   a company admitted the need and approved budget for it
    niche  the right kind of company, with no signal yet
"""
import re
import sys
import time
import urllib.parse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import li_lib as L
import li_dedupe as D

JOB_SEARCH = ("https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search?"
              "{q}")
JOB_DETAIL = "https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/{id}"


def title_rank(title):
    """How close a job title is to what Monarc sells. Higher is better."""
    t = title or ""
    return (3 if L.AI_GEN.search(t) else 2 if L.UGC.search(t) else
            1 if L.PRODUCT_LINE.search(t) else 0) - (1 if L.EDIT_ONLY.search(t) and
                                                     not L.AI_GEN.search(t) else 0)


# ------------------------------------------------------------------ jobs
def jobs(cfg, s):
    st = cfg["settings"]
    days = st["max_age_days"]
    tpr = f"r{min(days, 30) * 86400}"
    kept = new = 0
    gone = s.get("gone", {})
    pages_ok = pages_empty = 0
    for role in cfg["job_roles"]:
        for market, loc in cfg["job_locations"]:
            key = f"jobs|{role}|{loc}|{days}"
            if L.done_recently(s, key):
                continue
            failed = False
            for page in range(st["pages_per_search"]):
                q = {"keywords": role, "location": loc, "f_TPR": tpr,
                     "start": page * 10}
                if market == "remote":
                    q["f_WT"] = 2
                html = L.li_get(JOB_SEARCH.format(q=urllib.parse.urlencode(q)), ttl_h=12)
                if html is None:
                    failed = True        # temporary error: leave this search un-done
                    break
                cards = L.parse_job_cards(html)
                # A 200 page full of markup but zero parsed cards means LinkedIn
                # changed its HTML, not that nobody is hiring. Counted and
                # reported below, so a broken parser is loud instead of silent.
                if html and len(html) > 3000:
                    pages_ok += bool(cards)
                    pages_empty += not cards
                for j in cards:
                    if j["id"] in s["jobs"] or j["id"] in gone:
                        continue
                    if not L.ROLE_RX.search(j["title"]) or L.WRONG_ROLE.search(j["title"]):
                        continue
                    if L.age_days(j["posted"]) > days:
                        continue
                    j["market"] = "remote" if market == "remote" else (
                        L.market_of(j["location"]) if L.market_of(j["location"]) != "other"
                        else market)
                    j["search"] = f"{role} / {loc}"
                    j["found"] = L.TODAY.isoformat()
                    s["jobs"][j["id"]] = j
                    new += 1
                if len(cards) < 10:
                    break
            if not failed:
                s["done"][key] = time.time()
            L.save_state(s)
            print(f"  {role[:28]:<29} {loc[:22]:<23} total jobs {len(s['jobs'])}"
                  f"{'   (network error, retried next run)' if failed else ''}")
    if pages_empty >= 5 and pages_ok == 0:
        print("\n  !! WARNING: LinkedIn returned pages but no job could be read from them.\n"
              "  !! The page layout has probably changed. parse_job_cards in li_lib.py\n"
              "  !! needs updating. Nothing is wrong with your searches.")

    # Details only for jobs worth reading: one per company, skipping names
    # already pitched. A description is one request each, so this is where
    # the request budget is spent, and spent only on candidates.
    # The job read is the company's BEST-FITTING title, not its newest: a
    # studio with an "AI Content Creator" and a "Mid-Weight Producer" opening
    # must be pitched on the AI one.
    by_co = {}
    for j in s["jobs"].values():
        if j.get("closed") or L.WRONG_ROLE.search(j["title"]):
            continue
        k = j.get("company_url") or L.norm(j["company"])
        rank = (title_rank(j["title"]), j["posted"])
        if k and (k not in by_co or rank > (title_rank(by_co[k]["title"]), by_co[k]["posted"])):
            by_co[k] = j
    known = D.Known()
    todo = [j for j in by_co.values()
            if ("description" not in j or (j["description"] == "" and not j.get("closed")))
            and not known.check(j["company"], "", "", j.get("company_url"))[0]
            and not L.matches_word(j["company"], cfg["skip_words"])
            and not L.matches_word(j["company"], cfg["never_companies"], whole=True)]
    print(f"\n  reading {len(todo)} job descriptions (one per company)")
    for i, j in enumerate(todo, 1):
        page = L.li_get(JOB_DETAIL.format(id=j["id"]), ttl_h=24 * 30)
        if page is None:
            continue                     # temporary error: try again next run
        j.update(L.parse_job_detail(page))
        j.setdefault("description", "")
        if not j["description"]:
            j["closed"] = True           # page gone, or live but with no posting left
        kept += 1
        if i % 10 == 0:
            L.save_state(s)
            print(f"    {i}/{len(todo)}")
    L.save_state(s)
    print(f"  jobs: {new} new postings, {kept} described, "
          f"{len(by_co)} companies in total")


# ------------------------------------------------------------------ posts
def posts(cfg, s):
    st = cfg["settings"]
    days = st["max_age_days"]
    found = 0
    for q in cfg["post_queries"]:
        key = f"posts|{q}"
        if L.done_recently(s, key):
            continue
        urls = L.search(f"site:linkedin.com/posts {q}", pages=st["search_pages"])
        fresh = []
        # One post has many URLs (in.linkedin.com, /feed/update/urn:li:activity:,
        # tracking params). The activity id is the post's real identity, so
        # two URLs with the same id are one post, opened once.
        have = {re.search(r"(\d{18,20})", k).group(1)
                for k in list(s["posts"]) + list(s.get("gone", {}))
                if re.search(r"(\d{18,20})", k)}
        for u in urls:
            if "linkedin.com/posts/" not in u and "/feed/update/" not in u:
                continue
            u = re.sub(r"^https?://[a-z]{2,3}\.linkedin", "https://www.linkedin", u)
            u = u.split("?")[0]
            aid = re.search(r"(\d{18,20})", u)
            if u in s["posts"] or (aid and aid.group(1) in have):
                continue
            if aid:
                have.add(aid.group(1))
            d = L.activity_date(u)
            if d and L.age_days(d) > days:
                continue          # the id already proves it is too old
            fresh.append(u)
        for u in fresh:
            page = L.li_get(u, ttl_h=24 * 30)
            if page is None:
                continue                 # temporary error: not marked, retried later
            p = L.parse_post(page)
            if not p:
                s["posts"][u] = {"url": u, "dead": True}
                continue
            p["url"] = u
            p["query"] = q
            p["found"] = L.TODAY.isoformat()
            p["date"] = p.get("date") or (L.activity_date(u) or "").__str__()
            p["emails"] = [e for e in dict.fromkeys(L.EMAIL_RX.findall(p["text"]))
                           if not e.lower().endswith((".png", ".jpg"))]
            s["posts"][u] = p
            found += 1
        s["done"][key] = time.time()
        L.save_state(s)
        print(f"  {q[:52]:<53} {len(fresh):>2} new posts")
    print(f"  posts: {found} opened, {len(s['posts'])} on file")


# ------------------------------------------------------------------ niche
def niche(cfg, s):
    st = cfg["settings"]
    added = 0
    for q in cfg["niche_queries"]:
        key = f"niche|{q}"
        if L.done_recently(s, key, hours=24 * 7):
            continue
        urls = L.search(f"site:linkedin.com/company {q}", pages=st["search_pages"],
                        month=False)
        n = 0
        for u in urls:
            cu = L.company_url(u)
            if not cu or cu in s["companies"]:
                continue
            s["companies"][cu] = {"url": cu, "from": "niche", "query": q}
            n += 1
        added += n
        s["done"][key] = time.time()
        L.save_state(s)
        print(f"  {q[:52]:<53} {n:>2} companies")
    print(f"  niche: {added} new company pages queued")


# ------------------------------------------------------------------ companies
def companies(cfg, s):
    """Open every company page once: website, size, industry, HQ, activity."""
    for j in s["jobs"].values():
        cu = j.get("company_url")
        if cu and cu not in s["companies"]:
            s["companies"][cu] = {"url": cu, "from": "jobs"}
    todo = [c for c in s["companies"].values() if "fetched" not in c]
    hiring = {j.get("company_url") for j in s["jobs"].values()}
    todo = [c for c in todo if c["from"] == "niche" or c["url"] in hiring]
    print(f"  opening {len(todo)} company pages")
    for i, c in enumerate(todo, 1):
        page = L.li_get(c["url"], ttl_h=24 * 14)
        if page is None:
            continue                     # temporary error: stays unread, retried later
        c.update(L.parse_company(page))
        c["fetched"] = L.TODAY.isoformat()
        if not page:
            c["dead"] = True             # page gone: never counted as "size unknown"
        c["kind"] = L.company_kind(c.get("name", ""), c.get("industry", ""),
                                   c.get("about", ""), cfg)
        if i % 10 == 0:
            L.save_state(s)
            print(f"    {i}/{len(todo)}")
    L.save_state(s)


STREAMS = {"jobs": jobs, "posts": posts, "niche": niche, "companies": companies}


def main():
    which = sys.argv[1:] or ["posts", "jobs", "niche", "companies"]
    L.lock()
    cfg, s = L.config(), L.load_state()
    t0 = time.time()
    # posts and niche discover through a search engine; jobs and companies read
    # LinkedIn directly. A block on one source skips only the streams that use
    # it, so a search-engine challenge never costs the company-page pass.
    uses = {"posts": "search", "niche": "search", "jobs": "linkedin",
            "companies": "linkedin"}
    down = set()
    for name in which:
        print(f"\n=== LinkedIn {name}")
        if uses[name] in down:
            print(f"  skipped: {uses[name]} is blocked for this run")
            continue
        try:
            STREAMS[name](cfg, s)
        except L.Blocked as e:
            L.save_state(s)
            down.add(e.source)
            print(f"\n  STOPPED {name}: {e}\n  Progress is saved. The next run "
                  f"continues from here.")
            if e.source == "search" and not L.env("SERPER_API_KEY"):
                print("  Tip: a SERPER_API_KEY in .env replaces DuckDuckGo with Google "
                      "results and ends these blocks (2,500 searches on signup).")
        except KeyboardInterrupt:
            L.save_state(s)
            print("\n  Interrupted. Progress is saved.")
            break
    print(f"\n  {L.STATS['requests']} requests, {L.STATS['cached']} served from cache, "
          f"{int(time.time() - t0)}s")


if __name__ == "__main__":
    main()
