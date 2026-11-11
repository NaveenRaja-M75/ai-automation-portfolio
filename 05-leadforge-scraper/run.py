#!/usr/bin/env python3
"""
LeadForge :: the CLI.

    python run.py fetch   --sources high-signal --window 14d
    python run.py list    --tier A --limit 25 --body 200
    python run.py enrich  --tier A --limit 20
    python run.py rescore
    python run.py export  --tier A,B --out ./out
    python run.py doctor
    python run.py stats
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from lf.core import Http, Store, find_emails, iso, parse_window, utcnow  # noqa: E402
from lf.enrich import (Robots, draft_outreach, enrich_domain, guess_emails,  # noqa: E402
                       provider_lookup, resolve_company_domain)
from lf.icp import (ENRICH_TAGS, Scorer, apply_enrich_adjust,  # noqa: E402
                    cluster_extra_for_domain, load_config, regrade_all, tier_for)
from lf.report import print_table, rows_to_csv, write_all  # noqa: E402
from lf.sources import (SOURCE_REGISTRY, resolve_sources,  # noqa: E402
                        is_search_engine_url, source_mode)

HERE = Path(__file__).resolve().parent


def _utf8():
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


def _c(txt: str, code: str) -> str:
    if not sys.stdout.isatty() or os.environ.get("NO_COLOR"):
        return txt
    return f"\x1b[{code}m{txt}\x1b[0m"


BOLD, DIM, GREEN, YELLOW, RED, CYAN = "1", "2", "32", "33", "31", "36"


def build_ctx(args):
    cfg = load_config(args.config)
    data_dir = Path(cfg.profile.get("data_dir", str(HERE / "data")))
    data_dir.mkdir(parents=True, exist_ok=True)
    store = Store(str(data_dir / "leadforge.db"))
    http = Http(
        cache_dir=str(data_dir / "cache"),
        delay_ms=int(cfg.limits.get("request_delay_ms", 900)),
        timeout=int(cfg.limits.get("timeout_s", 25)),
        verbose=args.verbose,
    )
    scorer = Scorer(cfg)
    return cfg, store, http, scorer, data_dir


def _active_sources(cfg, spec: str) -> tuple[list[str], list[str]]:
    """Resolve a source spec into (to_run, switched_off).

    When you ask for everything we honour `enabled` in config.toml, so the slow
    or environment-dependent sources you turned off stay off. When you name a
    source explicitly we run it anyway, so
    `python run.py fetch --source websearch` always works as a test.
    """
    names = resolve_sources(spec)
    explicit = (spec or "").strip() not in ("", "all", "*")
    if explicit:
        return names, []
    run, off = [], []
    for n in names:
        if (cfg.sources.get(n) or {}).get("enabled", True):
            run.append(n)
        else:
            off.append(n)
    return run, off


# ==========================================================================
# fetch
# ==========================================================================


def cmd_fetch(args):
    cfg, store, http, scorer, _ = build_ctx(args)
    sources, disabled = _active_sources(cfg, args.sources)
    window_h = parse_window(args.window)
    since_ts = int(time.time() - window_h * 3600) if window_h else None
    per_source = args.limit

    print(_c(f"LeadForge fetch  |  {len(sources)} source(s)  |  "
             f"window={args.window}  |  profile={cfg.profile.get('name','?')}", BOLD))
    if disabled:
        print(_c(f"  off (enabled=false in config.toml): {', '.join(disabled)}", DIM))
    print()

    grand_new = grand_found = 0
    per_source_new: dict[str, int] = {}

    for name in sources:
        fn, bonus, group, desc = SOURCE_REGISTRY[name]
        t0 = time.time()
        found = new = skipped_old = skipped_junk = errors = 0
        err_lines: list[str] = []
        print(f"  {_c(name.ljust(16), CYAN)} {_c(desc, DIM)}")
        try:
            for rec in fn(http, cfg, since_ts=since_ts, limit=per_source):
                if "_error" in rec:
                    err_lines.append(rec["_error"])
                    errors += 1
                    continue
                found += 1
                pts = rec.get("posted_ts")
                if since_ts and pts and pts < since_ts:
                    skipped_old += 1
                    continue
                if is_search_engine_url(rec.get("url", "")):
                    skipped_junk += 1
                    continue
                v = scorer.score(rec.get("title", ""), rec.get("body", ""),
                                 meta=rec, platform_bonus=bonus,
                                 mode=source_mode(name))
                if v.excluded:
                    continue
                inline = find_emails(rec.get("body", "") or "")
                external = ""
                if v.budget_max:
                    external += f" budget~{v.currency or ''}{int(v.budget_max)}"
                lead = {
                    **rec,
                    "score": v.score,
                    "tier": v.tier,
                    "tags": json.dumps(v.tags),
                    "offers": json.dumps(v.offers),
                    "emails": json.dumps(inline),
                    "contact_email": inline[0] if inline else None,
                    "budget_raw": v.budget_raw,
                    "budget_max": v.budget_max,
                    "currency": v.currency,
                    "status": "new",
                    "notes": external.strip() or None,
                    "raw": json.dumps(rec.get("raw", {}), default=str)[:20000],
                }
                lead.pop("_error", None)
                _, created = store.upsert(lead)
                if created:
                    new += 1
                    per_source_new[name] = per_source_new.get(name, 0) + 1
        except KeyboardInterrupt:
            print("\n  interrupted")
            break
        except Exception as e:  # noqa: BLE001
            err_lines.append(f"{type(e).__name__}: {e}")
            errors += 1

        ms = int((time.time() - t0) * 1000)
        store.log_run(name, found, new, "; ".join(err_lines[:3]) or None)
        status = _c(f"+{new}", GREEN) if new else _c("0", DIM)
        extra = f"  {_c(str(errors) + ' err', YELLOW)}" if errors else ""
        old = f"  ({skipped_old} too old)" if skipped_old else ""
        junk = f"  ({skipped_junk} junk)" if skipped_junk else ""
        print(f"    -> {found} seen, {status} new{old}{junk}{extra}  [{ms}ms]")
        for e in err_lines[:2]:
            print(f"       {_c('!', RED)} {e[:150]}")
        grand_found += found
        grand_new += new

    print()
    summary = regrade_all(store, scorer)
    if summary["boosted_domains"]:
        print(f"  cluster boost on {summary['boosted_domains']} domain(s) "
              f"(several roles = real demand)")
    print(_c(f"  TOTAL: {grand_found} seen, {grand_new} new. "
             f"DB now holds {store.total()} leads.", BOLD))

    if args.enrich:
        print()
        cmd_enrich(argparse.Namespace(
            config=args.config, tier=args.enrich_tier, limit=args.enrich_limit,
            min_score=args.enrich_min, verbose=args.verbose,
            no_crawl=False, no_apis=False, no_resolve=False, only_missing=True))
    store.close()


# ==========================================================================
# list
# ==========================================================================


def cmd_list(args):
    cfg, store, http, scorer, _ = build_ctx(args)
    rows = store.query(
        tier=args.tier, min_score=args.min_score, sources=args.source,
        status=args.status, since_hours=parse_window(args.window) if args.window else None,
        offer=args.offer, limit=args.limit,
    )
    if args.free_only:
        rows = [r for r in rows if (r["contact_email"] or "") == ""]
    print(_c(f"{len(rows)} leads  (tier={args.tier or 'any'}  "
             f"min_score={args.min_score}  db={store.total()})", BOLD))
    print()
    print_table(rows, limit=args.limit, show_body=args.body)
    if args.json:
        print()
        print(rows_to_csv(rows, "raw") if args.csv else json.dumps(
            [dict(r) for r in rows[:20]], indent=2, default=str))
    store.close()


# ==========================================================================
# enrich
# ==========================================================================


def cmd_enrich(args):
    cfg, store, http, scorer, _ = build_ctx(args)
    rows = store.unenriched(
        tier=args.tier, min_score=args.min_score, limit=args.limit,
        only_missing_email=args.only_missing, source=getattr(args, "source", None),
    )
    if not rows:
        print("nothing to enrich (no rows with a domain and no contact email)")
        store.close()
        return

    robots = Robots(http)
    print(_c(f"enriching {len(rows)} lead(s) ...", BOLD))
    ok = resolved = 0
    for r in rows:
        domain = r["domain"]
        if not domain and r["company"] and not args.no_resolve:
            domain = resolve_company_domain(http, r["company"], r["location"], cfg)
            if domain:
                store.update(r["id"], domain=domain)
                resolved += 1
        if not domain or "." not in domain:
            continue
        try:
            en = enrich_domain(http, domain, cfg,
                               max_pages=int(cfg.limits.get("crawl_pages", 5)),
                               robots=robots)
        except Exception as e:  # noqa: BLE001
            print(f"  {_c('x', RED)} {domain}: {type(e).__name__}: {e}")
            continue

        if not args.no_apis:
            api_emails, notes = provider_lookup(http, domain, cfg)
            for e in api_emails:
                if e not in en.emails:
                    en.emails.append(e)
            en.source_notes += notes

        emails = en.emails
        if not emails and en.people:
            emails = [p["email"] for p in en.people if p.get("email")]

        # ---- grade this lead exactly the way `regrade_all` would ----------
        # Recomputed from the text every time, never adjusted in place: that is
        # what stops scores drifting upward when `enrich` runs repeatedly.
        bonus = (SOURCE_REGISTRY.get(r["source"]) or (None, 1, "", ""))[1]
        v = scorer.score(r["title"] or "", r["body"] or "",
                         meta={**r, "domain": domain}, platform_bonus=bonus,
                         mode=source_mode(r["source"] or ""))
        try:
            old_tags = json.loads(r["tags"] or "[]")
        except (TypeError, ValueError):
            old_tags = []
        if not isinstance(old_tags, list):
            old_tags = []
        tags = v.tags + [t for t in old_tags if t in ENRICH_TAGS and t not in v.tags]
        if en.ad_stack.get("likely_advertiser") and "advertiser" not in tags:
            tags.append("advertiser")
        if en.mx_ok is False and "no-mx" not in tags:
            tags.append("no-mx")
        score = 0 if v.excluded else apply_enrich_adjust(v.score, tags)
        extra = cluster_extra_for_domain(store, domain)
        if extra:
            score = min(100, score + extra)
            if "multi-signal" not in tags:
                tags.append("multi-signal")
        if emails:
            ok += 1

        tier = tier_for(score)
        best = en.best_email
        store.update(
            r["id"],
            contact_email=best,
            contact_name=(en.people[0]["name"] if en.people else None),
            contact_role=(en.people[0].get("role") if en.people else None),
            emails=json.dumps(emails),
            mx_ok=(1 if en.mx_ok else 0 if en.mx_ok is False else None),
            score=score, tier=tier, tags=json.dumps(tags),
            notes=json.dumps({
                "ad_stack": en.ad_stack.get("stack", []),
                "paid_media": en.ad_stack.get("paid_media", []),
                "socials": en.socials,
                "phones": en.phones,
                "title": en.title,
                "pages_read": en.pages_read,
                "provider_notes": en.source_notes,
            }, ensure_ascii=False)[:4000],
        )
        flag = _c("email", GREEN) if emails else _c("no email", DIM)
        ad = _c(" ADVERTISER", YELLOW) if en.ad_stack.get("likely_advertiser") else ""
        soc = (" " + ",".join(en.socials.keys())) if en.socials else ""
        print(f"  {domain:<32} {flag}{ad}{_c(soc, DIM)}"
              f"{' -> ' + best if best else ''}")
    print(_c(f"\n  {ok}/{len(rows)} now have a contact email."
             + (f"  ({resolved} domains resolved from company name)" if resolved else ""),
             BOLD))
    store.close()


# ==========================================================================
# export
# ==========================================================================


def cmd_export(args):
    cfg, store, http, scorer, data_dir = build_ctx(args)
    rows = store.query(
        tier=args.tier, min_score=args.min_score, sources=args.source,
        status=args.status, limit=args.limit, offer=args.offer,
    )
    if args.only_with_email:
        rows = [r for r in rows if r["contact_email"]]
    out = Path(args.out) if args.out else (data_dir / "exports")
    if args.format == "stdout":
        print(rows_to_csv(rows, args.style))
        store.close()
        return
    paths = write_all(rows, str(out), args.stem)
    print(_c(f"exported {len(rows)} leads ->", BOLD))
    for k, v in paths.items():
        print(f"  {k:<9} {v}")

    if args.drafts:
        draft_path = out / f"{args.stem}_drafts.md"
        with open(draft_path, "w", encoding="utf-8") as fh:
            fh.write("# Outreach drafts\n\n")
            for r in rows[: args.drafts]:
                if not r["contact_email"]:
                    continue
                offer = scorer.best_offer(json.loads(r["offers"] or "[]"))
                d = draft_outreach(dict(r), offer, cfg)
                fh.write(f"## {r['contact_email']}  ({r['tier']}/{r['score']})\n")
                fh.write(f"**Subject:** {d['subject']}\n\n")
                fh.write(d["body"] + "\n\n---\n\n")
        print(f"  drafts    {draft_path}")
    store.close()


# ==========================================================================
# doctor
# ==========================================================================


def _classify_error(note: str) -> str:
    """Turn a source's own error text into an honest doctor status."""
    low = note.lower()
    if "add " in low or "token" in low or "client_id" in low:
        return "needs-key"
    # A source that refuses to store unverifiable results is working correctly,
    # not failing - the engine is just hostile-looking to this IP today.
    if "corroborated" in low or "degraded serp" in low:
        return "no-signal"
    return "fail"


def cmd_doctor(args):
    cfg = load_config(args.config)
    http = Http(delay_ms=400, timeout=15)
    names, disabled = _active_sources(cfg, getattr(args, "sources", "all"))
    print(_c("LeadForge doctor - checking every active source", BOLD))
    print()
    print(f"  {'SOURCE':<17} {'STATUS':<10} {'N':>4}  {'MS':>6}  NOTE")
    print("  " + "-" * 92)
    good = bad = keyed = quiet = 0
    for name in names:
        fn, bonus, group, desc = SOURCE_REGISTRY[name]
        t0 = time.time()
        n = 0
        note = ""
        status = "ok"
        try:
            for rec in fn(http, cfg, since_ts=None, limit=3):
                if "_error" in rec:
                    note = rec["_error"][:70]
                    status = _classify_error(note)
                    break
                n += 1
        except Exception as e:  # noqa: BLE001
            status, note = "fail", f"{type(e).__name__}: {e}"[:70]
        ms = int((time.time() - t0) * 1000)
        if status == "ok":
            good += 1
            col = _c("ok", GREEN)
        elif status == "needs-key":
            keyed += 1
            col = _c("needs-key", YELLOW)
        elif status == "no-signal":
            quiet += 1
            col = _c("no-signal", YELLOW)
        else:
            bad += 1
            col = _c("fail", RED)
        print(f"  {name:<17} {col:<19} {n:>4}  {ms:>6}  {_c(note, DIM)}")
    for name in disabled:
        print(f"  {name:<17} {_c('off', DIM):<19} {'-':>4}  {'-':>6}  "
              f"{_c('enabled=false in config.toml', DIM)}")
    print()
    print(_c(f"  {good} working, {keyed} optional/needs-key, {bad} failing, "
             f"{quiet} no-signal (working, but nothing verifiable from this IP), "
             f"{len(disabled)} off. Checked {len(names)} active of "
             f"{len(SOURCE_REGISTRY)} sources.", BOLD))


# ==========================================================================
# stats
# ==========================================================================


def cmd_stats(args):
    cfg, store, http, scorer, data_dir = build_ctx(args)
    print(_c("LeadForge stats", BOLD))
    print(f"  profile   {cfg.profile.get('name','?')}")
    print(f"  db        {store.path}")
    print(f"  total     {store.total()} leads")
    print()
    print(f"  {'SOURCE':<18} {'N':>5}   last seen")
    print("  " + "-" * 60)
    for r in store.counts():
        print(f"  {r['source']:<18} {r['n']:>5}   {r['last'] or '-'}")
    print()
    print(f"  {'TIER':<6} {'N':>5}")
    print("  " + "-" * 20)
    for t in ("A", "B", "C", "D"):
        n = store.conn.execute(
            "SELECT COUNT(*) c FROM leads WHERE tier = ?", (t,)).fetchone()["c"]
        print(f"  {t:<6} {n:>5}")
    with_email = store.conn.execute(
        "SELECT COUNT(*) c FROM leads WHERE contact_email IS NOT NULL "
        "AND contact_email != ''").fetchone()["c"]
    adv = store.conn.execute(
        "SELECT COUNT(*) c FROM leads WHERE tags LIKE '%advertiser%'").fetchone()["c"]
    print()
    print(f"  with email     {with_email}")
    print(f"  advertisers    {adv}  (site running paid-ad pixels)")
    print()
    print(f"  {'OFFER':<22} {'A+B leads':>10}")
    print("  " + "-" * 36)
    for o in cfg.offers:
        n = store.conn.execute(
            "SELECT COUNT(*) c FROM leads WHERE offers LIKE ? AND tier IN ('A','B')",
            (f'%"{o.id}"%',)).fetchone()["c"]
        print(f"  {o.name[:22]:<22} {n:>10}")
    store.close()


# ==========================================================================
# rescore
# ==========================================================================


def cmd_rescore(args):
    """Re-apply the CURRENT scorer to every stored lead.

    Why this exists: the database is the long-lived artifact, but the scorer
    keeps improving. Rows written last week carry last week's opinion, and a
    stale grade is worse than no grade - it puts junk in tier A/B and hides real
    buyers. `fetch` calls the same function at the end of every run, so this is
    the manual handle on it: run it after editing config.toml keywords.
    """
    cfg, store, http, scorer, _ = build_ctx(args)
    print(_c(f"LeadForge rescore - re-grading {store.total()} stored leads", BOLD))
    print()
    s = regrade_all(store, scorer)
    print(f"  {s['changed']} lead(s) re-graded, "
          f"{s['boosted_domains']} domain(s) cluster-boosted from a clean base")
    print()
    moves = s["moves"]
    up = [m for m in moves if m[2] in ("A", "B") and m[1] not in ("A", "B")]
    down = [m for m in moves if m[1] in ("A", "B") and m[2] not in ("A", "B")]
    if up:
        print(_c(f"  promoted into A/B: {len(up)}", GREEN))
        for lab, was, now, sc in up[:15]:
            print(f"    {lab:<46} {was} -> {_c(now, GREEN)}  ({sc})")
        print()
    if down:
        print(_c(f"  demoted out of A/B: {len(down)}", RED))
        for lab, was, now, sc in down[:15]:
            print(f"    {lab:<46} {was} -> {_c(now, RED)}  ({sc})")
    store.close()


# ==========================================================================
# sources / search helpers
# ==========================================================================


def cmd_sources(args):
    print(_c("LeadForge source catalogue", BOLD))
    print()
    print(f"  {'SOURCE':<17} {'+':>2} {'GROUP':<12} DESCRIPTION")
    print("  " + "-" * 100)
    for name, (fn, bonus, group, desc) in SOURCE_REGISTRY.items():
        print(f"  {name:<17} {bonus:>2} {group:<12} {desc}")
    print()
    print("  Groups: high-signal, job-boards, news, local, all")
    print("  Example: python run.py fetch --sources high-signal,job-boards")


def cmd_addkey(args):
    """Quick, safe way to drop a credential into config.toml."""
    cfg_path = Path(args.config)
    text = cfg_path.read_text(encoding="utf-8")
    key, value = args.key, args.value
    import re
    pat = re.compile(rf'^(\s*){re.escape(key)}\s*=.*$', re.M)
    if pat.search(text):
        text = pat.sub(lambda m: f'{m.group(1)}{key} = "{value}"', text, count=1)
    else:
        section = args.section
        sec_pat = re.compile(rf'^\[{re.escape(section)}\]\s*$', re.M)
        if sec_pat.search(text):
            text = sec_pat.sub(f'[{section}]\n{key} = "{value}"', text, count=1)
        else:
            text += f'\n[{section}]\n{key} = "{value}"\n'
    cfg_path.write_text(text, encoding="utf-8")
    print(f"set {key} in [{args.section}] -> {cfg_path}")


# ==========================================================================
# main
# ==========================================================================


def main(argv=None):
    _utf8()
    ap = argparse.ArgumentParser(
        prog="leadforge",
        description="Free, local, signal-based lead engine for an AI creative studio.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    ap.add_argument("--config", default=str(HERE / "config.toml"))
    ap.add_argument("-v", "--verbose", action="store_true")
    sub = ap.add_subparsers(dest="cmd", required=True)

    f = sub.add_parser("fetch", help="pull signals from free sources into the DB")
    f.add_argument("--sources", default="high-signal,job-boards",
                   help="comma list or group: all, high-signal, job-boards, news, local")
    f.add_argument("--window", default="30d", help="only keep signals newer than this (30d, 24h, 2w)")
    f.add_argument("--limit", type=int, default=450, help="max records per source")
    f.add_argument("--enrich", action="store_true", help="run enrichment right after")
    f.add_argument("--enrich-tier", default="A")
    f.add_argument("--enrich-limit", type=int, default=15)
    f.add_argument("--enrich-min", type=int, default=0)
    f.set_defaults(func=cmd_fetch)

    l = sub.add_parser("list", help="show stored leads")
    l.add_argument("--tier", default="A,B")
    l.add_argument("--min-score", type=int, default=0)
    l.add_argument("--source", default=None)
    l.add_argument("--status", default=None)
    l.add_argument("--offer", default=None)
    l.add_argument("--window", default=None)
    l.add_argument("--limit", type=int, default=30)
    l.add_argument("--body", type=int, default=0, help="print the first N chars of the post")
    l.add_argument("--free-only", action="store_true", help="only leads with no email yet")
    l.add_argument("--json", action="store_true")
    l.add_argument("--csv", action="store_true")
    l.set_defaults(func=cmd_list)

    e = sub.add_parser("enrich", help="find emails / ad pixels / socials for stored leads")
    e.add_argument("--tier", default="A")
    e.add_argument("--min-score", type=int, default=0)
    e.add_argument("--limit", type=int, default=25)
    e.add_argument("--source", default=None, help="only enrich leads from this source (comma-separated)")
    e.add_argument("--no-crawl", action="store_true")
    e.add_argument("--no-resolve", action="store_true",
                   help="skip looking up company name -> website")
    e.add_argument("--no-apis", action="store_true", help="skip Hunter/Apollo/Snov")
    e.add_argument("--all", dest="only_missing", action="store_false", default=True)
    e.set_defaults(func=cmd_enrich)

    x = sub.add_parser("export", help="write CSV/JSONL/Markdown + outreach drafts")
    x.add_argument("--tier", default="A,B")
    x.add_argument("--min-score", type=int, default=30)
    x.add_argument("--source", default=None)
    x.add_argument("--status", default=None)
    x.add_argument("--offer", default=None)
    x.add_argument("--limit", type=int, default=1000)
    x.add_argument("--out", default=None)
    x.add_argument("--stem", default="leads")
    x.add_argument("--style", default="instantly", help="instantly|crm|raw")
    x.add_argument("--format", default="files", choices=["files", "stdout"])
    x.add_argument("--only-with-email", action="store_true")
    x.add_argument("--drafts", type=int, default=0, help="also write N personalised drafts")
    x.set_defaults(func=cmd_export)

    d = sub.add_parser("doctor", help="health-check every active source")
    d.add_argument("--source", dest="sources", default="all",
                   help="limit the check, e.g. --source high-signal")
    d.set_defaults(func=cmd_doctor)

    s = sub.add_parser("stats", help="DB and pipeline statistics")
    s.set_defaults(func=cmd_stats)

    rs = sub.add_parser("rescore", help="re-grade every stored lead with the current scorer")
    rs.set_defaults(func=cmd_rescore)

    sc = sub.add_parser("sources", help="list available sources")
    sc.set_defaults(func=cmd_sources)

    k = sub.add_parser("addkey", help="save an API key into config.toml")
    k.add_argument("key")
    k.add_argument("value")
    k.add_argument("--section", default="apis")
    k.set_defaults(func=cmd_addkey)

    args = ap.parse_args(argv)
    try:
        args.func(args)
    except KeyboardInterrupt:
        print("\ninterrupted")
        return 130
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
