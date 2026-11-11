#!/usr/bin/env python3
"""
MONARC LEAD OS — one entry point.

    python run.py status                    where every lead currently is
    python run.py source --market chennai   find brands paying for ads, no video
    python run.py enrich                    add email / phone / instagram
    python run.py pitch                     print the brief for Claude Code
    python run.py dispatch                  prepare approved leads for sending
    python run.py full --market chennai     source + enrich in one go
    python run.py queue                     today's WhatsApp + Instagram page
    python run.py done whatsapp             record that you sent them
    python run.py foreign                   rebuild Monarc_Foreign_Leads.xlsx
    python run.py foreign-vault             load reachable foreign leads into the vault
    python run.py linkedin all              LinkedIn posts + jobs + niche -> workbook
    python run.py linkedin vault            load LinkedIn tier A/B leads with drafts

The division of labour, which is the whole design:

    Python  does the deterministic work. Fetching, verifying, de-duplicating,
            parsing contacts. No intelligence needed, so no tokens spent.
    Claude  does the judgement. Looking at their creative, writing the concept,
            drafting the message in your voice.
    You     do the approving. Nothing reaches a human inbox without your tag.

That split is why this needs no Anthropic API key, no Fal.ai key, no Replicate
key, no Supabase and no vector database. Claude Code IS the reasoning layer,
and it is already paid for.
"""
import sys
import argparse
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "engine"))
import vault as V


def sh(script, *args):
    return subprocess.run([sys.executable, str(ROOT / "engine" / script), *args],
                          env={**__import__("os").environ,
                               "PYTHONIOENCODING": "utf-8"}).returncode


def status():
    cfg = V.campaign()
    print("=" * 58)
    print("  MONARC LEAD OS")
    print("=" * 58)
    print(f"  pack            : {cfg['pack']}  ->  "
          f"{', '.join(cfg['niches'][:5])}")
    print(f"  market          : {cfg['market']} "
          f"({cfg['markets'].get(cfg['market'], '?')})")
    print(f"  packs available : {', '.join(cfg['packs'])}")
    print(f"  do-not-contact  : {len(V.blocklist())} entries")
    print(f"  ledger          : {len(V.load_seen())} names never to repeat")
    try:
        sys.path.insert(0, str(ROOT / "engine"))
        from health import token_status
        st, msg, days = token_status()
        if st != "OK":
            print(f"  !! META TOKEN    : {st} - {msg}")
        elif days < 14:
            print(f"  !  meta token    : {msg}")
    except Exception:
        pass
    print("-" * 58)
    labels = {"sourced": "01 Sourced   (found, no contacts yet)",
              "analyzed": "02 Analyzed  (contacts added, awaiting your review)",
              "approved": "03 Approved  (tagged by you)",
              "sent": "04 Sent      (prepared and logged)",
              "rejected": "99 Rejected"}
    for stage, label in labels.items():
        print(f"  {len(V.notes_in(stage)):>4}  {label}")
    print("-" * 58)
    pending = V.approved_notes()
    if pending:
        print(f"  {len(pending)} lead(s) carry #approved and are ready:")
        for p in pending[:5]:
            fm, _ = V.read_note(p)
            print(f"      - {fm.get('brand', p.stem)}")
        print("  run: python run.py dispatch")
    else:
        n = len(V.notes_in("analyzed"))
        if n:
            print(f"  Next: open vault/02_Analyzed in Obsidian, review the {n} "
                  f"lead(s),\n  add #approved to the ones worth pitching.")
        else:
            print("  Next: python run.py source --market chennai")
    print("=" * 58)


PITCH_BRIEF = """
Paste this into Claude Code from inside the Monarc_Lead_OS folder:

----------------------------------------------------------------------
Read vault/00_Control/VOICE.md first, then:
  - for a note with lead_type: agency  -> read 00_Control/AGENCY_PITCH.md
  - for any other note                 -> read 00_Control/EVIDENCE_RULES.md

An agency pitch is an OFFER of white-label capacity, never a diagnosis of
their weakness. Never suggest their creative is lacking. Use the word
"white-label" early, or they read you as a competitor after their clients.

Then for every note in vault/02_Analyzed/ that has an empty "Creative gap
analysis" section:

1. Open the ads_link in the note and look at what their creative is actually
   doing. Describe it concretely: what is shown, how it is lit, whether a
   human appears, whether anything moves.

2. Fill in the three bullets: what they show now, what is missing, and the
   concept to pitch. The concept must name their real product, not a category.

3. Write three drafts under "## Draft messages", as:
       ### Email       (90 words max, with a subject line)
       ### Instagram   (50 words max, no subject, mobile-shaped)
       ### WhatsApp    (60 words max)

Rules that are not negotiable:
  - no em dashes, no semicolons, none of the AI tells in VOICE.md
  - never the word "free", or any banned word in VOICE.md
  - every message contains one detail true only of THIS brand
  - only state a claim graded `confirmed` in the note's evidence block.
    If no_video is not confirmed, do not mention video at all.
  - vary the opening line across notes. If two messages start the same way,
    rewrite one.

Do not mark anything #approved. That is mine to decide.
----------------------------------------------------------------------
"""


def main():
    ap = argparse.ArgumentParser(description="Monarc Lead OS")
    ap.add_argument("command", choices=["status", "source", "enrich", "pitch",
                                        "dispatch", "full", "audit", "repair", "health", "agencies", "followup", "draft",
                                        "queue", "whatsapp", "instagram", "done",
                                        "foreign", "foreign-vault", "linkedin"])
    ap.add_argument("target", nargs="?", default="",
                    help="done: which channel you just sent (whatsapp or instagram)")
    ap.add_argument("--market", default="",
                    help="overrides active_market in CAMPAIGN.md")
    ap.add_argument("--pages", type=int, default=2)
    ap.add_argument("--cap", type=int, default=120)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--batch", action="store_true",
                    help="dispatch: prepare all at once (clipboard holds only the last)")
    ap.add_argument("--region", default="tn",
                    choices=["tn", "india", "international", "all"],
                    help="agencies: which market to import")
    ap.add_argument("--learn", action="store_true",
                    help="followup: show only the reply statistics")
    ap.add_argument("--deep", action="store_true",
                    help="repair: also re-fetch contacts from each site")
    ap.add_argument("--now", action="store_true",
                    help="queue: ignore timing and include leads not yet due")
    ap.add_argument("--no-open", action="store_true",
                    help="queue: build the page without opening a browser")
    ap.add_argument("--skip", default="",
                    help='done: names you did NOT send to, e.g. --skip "Digises, Klasse"')
    a = ap.parse_args()
    cfg = V.campaign()
    if not a.market:
        a.market = cfg.get("market") or "india"

    if a.command == "status":
        status()
    elif a.command == "source":
        sh("source.py", "--market", a.market, "--pages", str(a.pages),
           "--cap", str(a.cap))
    elif a.command == "enrich":
        sh("enrich.py", *(["--limit", str(a.limit)] if a.limit else []))
    elif a.command == "draft":
        sh("draft_all.py", *(["--limit", str(a.limit)] if a.limit else []))
    elif a.command == "followup":
        sh("followup.py", *(["--learn"] if a.learn else []))
    elif a.command in ("queue", "whatsapp", "instagram"):
        chan = "all" if a.command == "queue" else a.command
        sh("outreach_queue.py", "--channel", chan,
           *(["--now"] if a.now else []), *(["--no-open"] if a.no_open else []))
    elif a.command == "foreign":
        # collect every foreign lead, re-check it live, dig deeper, build the
        # workbook. Each stage resumes from its own checkpoint in _state/.
        for step in ("foreign_collect.py", "foreign_verify.py", "foreign_deepen.py",
                     "foreign_build.py"):
            print(f"\n=== {step}")
            if sh(step) != 0:
                print(f"{step} stopped with an error. Fix it and run again: "
                      f"finished stages are not repeated.")
                break
    elif a.command == "foreign-vault":
        sh("foreign_to_vault.py", *(["--dry-run"] if a.dry_run else []),
           *(["--tiers", "A,B,C"] if a.batch else []))
        if not a.dry_run:
            sh("draft_all.py")
    elif a.command == "linkedin":
        # Public LinkedIn pages only, never a login. Aim it in
        # vault/00_Control/LINKEDIN.md.
        t = a.target or "all"
        if t in ("posts", "jobs", "niche"):
            if sh("li_collect.py", t, "companies") == 0:
                sh("li_build.py")
        elif t == "all":
            if sh("li_collect.py") == 0:
                sh("li_build.py")
        elif t == "build":
            sh("li_build.py")
        elif t == "vault":
            sh("li_vault.py", *(["--dry-run"] if a.dry_run else []))
            if not a.dry_run:
                sh("draft_all.py")
        elif t == "team":
            # clean lead sheet for team members, no drafts; only leads not
            # handed out before (--batch re-issues everything)
            sh("li_team.py", "export", *(["--all"] if a.batch else []))
        elif t == "team-sync":
            sh("li_team.py", "sync", *([a.skip] if a.skip else []))
        else:
            print("python run.py linkedin [all|posts|jobs|niche|build|vault|team|team-sync]")
    elif a.command == "done":
        if a.target not in ("whatsapp", "instagram"):
            print('Say which channel you sent: python run.py done whatsapp')
        else:
            sh("done.py", a.target, *(["--skip", a.skip] if a.skip else []))
    elif a.command == "agencies":
        sh("import_agencies.py", "--region", a.region,
           *(["--limit", str(a.limit)] if a.limit else []))
    elif a.command == "health":
        sh("health.py")
    elif a.command == "repair":
        sh("repair.py", *(["--deep"] if a.deep else []))
    elif a.command == "audit":
        sh("audit.py")
    elif a.command == "pitch":
        print(PITCH_BRIEF)
    elif a.command == "dispatch":
        sh("dispatch.py", *(["--dry-run"] if a.dry_run else []),
           *(["--batch"] if a.batch else []))
    elif a.command == "full":
        if sh("source.py", "--market", a.market, "--pages", str(a.pages),
              "--cap", str(a.cap)) == 0:
            sh("enrich.py")
        print("\nNow open vault/02_Analyzed in Obsidian, then run "
              "`python run.py pitch` and paste the brief into Claude Code.")


if __name__ == "__main__":
    main()
