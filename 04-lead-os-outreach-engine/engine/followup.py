#!/usr/bin/env python3
"""
FOLLOW-UP and LEARNING. The stage that turns sending into knowing.

WHY THIS MATTERS MORE THAN ANOTHER LEAD SOURCE
    Everything upstream finds people to talk to. Nothing measured whether the
    talking worked. Send fifty messages without this and you have fifty
    guesses; send fifty with it and you know which opening line earns replies,
    which channel outperforms, and which market answers at all.

    Two thirds of replies to cold outreach come after a second message, and
    the most common reason a warm lead dies is nobody sent one. This command
    exists so that never happens by accident.

HOW YOU RECORD A REPLY
    Open the note in vault/04_Sent and set the fields:

        replied: yes
        reply_date: 2026-09-05
        outcome: interested | not now | no | wrong person

    That is all. The tables below read those fields.
"""
import sys
import argparse
import datetime
from pathlib import Path
from collections import Counter, defaultdict

sys.path.insert(0, str(Path(__file__).resolve().parent))
import vault as V

FOLLOWUP_AFTER = 4          # days. Sooner reads as pushy, later reads as gone.
SECOND_AFTER = 10
GIVE_UP_AFTER = 21


def days_since(d):
    try:
        return (datetime.date.today() - datetime.date.fromisoformat(str(d)[:10])).days
    except Exception:
        return None


def collect():
    rows = []
    for p in V.notes_in("sent"):
        fm, body = V.read_note(p)
        sent_on = fm.get("sent_on") or fm.get("updated")
        rows.append({
            "path": p,
            "brand": fm.get("brand", p.stem),
            "channel": fm.get("channel", "?"),
            "sent_on": sent_on,
            "age": days_since(sent_on),
            "replied": str(fm.get("replied", "")).lower() in ("yes", "true", "y"),
            "outcome": fm.get("outcome", ""),
            "type": fm.get("lead_type", "brand"),
            "variant": fm.get("variant", "A"),
            "market": fm.get("market") or fm.get("city") or "",
            "first_line": next((l.strip() for l in body.splitlines()
                                if l.strip().startswith("Hi ")), "")[:58],
        })
    return rows


# Message two is where most replies actually come from, so it cannot be left
# for the moment you are staring at a list with nothing written. Each nudge is
# short, adds one NEW thing, and never repeats the first message.
NUDGE = {
    "first": (
        "Hey {name}, following up on this. If it helps, I can put together a "
        "short sample using one of your current clients' products so you can "
        "see the finish before deciding anything."),
    "second": (
        "Hey {name}, last one from me. If video is handled in house that is "
        "completely fine, just let me know and I will stop. If it ever goes "
        "out, I am here."),
}


def nudge_text(row, which):
    name = str(row["brand"]).split()[0]
    return NUDGE[which].format(name=name)


def due_list(rows):
    """What each sent lead needs next. Reads cadence.py, the same rules the
    queue page uses, so this report and the page can never disagree about who
    is due. They used to be two separate calculations, and the page offered
    people this report had already moved past."""
    import cadence as C
    print("=" * 66)
    print("  NEXT TOUCH")
    print("=" * 66)
    due, soon = [], []
    for r in rows:
        fm, _ = V.read_note(r["path"])
        nt = C.next_touch(fm)
        if nt is None:
            continue
        (due if nt["overdue_days"] >= 0 else soon).append((r, nt))
    for r, nt in sorted(due, key=lambda x: -x[1]["overdue_days"]):
        how = ("python run.py queue" if nt["channel"] in ("whatsapp", "instagram")
               else "LinkedIn nudge by hand, then add li2_on: <date> to the note"
               if nt["channel"] == "linkedin"
               else "email nudge, ask Claude to draft it in Gmail")
        print(f"  [DUE ] {r['brand'][:34]:<36} step {nt['step']}  "
              f"{nt['channel']:<10} {how}")
    for r, nt in sorted(soon, key=lambda x: x[1]["due"]):
        print(f"  [    ] {r['brand'][:34]:<36} step {nt['step']}  "
              f"{nt['channel']:<10} due {nt['due']:%a %d %b}")
    if not due and not soon:
        print("  Nothing left to send. Everyone replied or finished the sequence.")
    print()


def learning(rows):
    sent = [r for r in rows if r["age"] is not None]
    if not sent:
        print("Nothing sent yet, so nothing to learn from.")
        return
    replied = [r for r in sent if r["replied"]]
    print("=" * 66)
    print("  WHAT IS WORKING")
    print("=" * 66)
    rate = len(replied) / len(sent) * 100
    print(f"  {len(replied)} replies from {len(sent)} sent  ({rate:.0f}%)")
    if len(sent) < 20:
        print(f"  Too few to trust yet. Numbers get meaningful around 20 sends,")
        print(f"  so treat everything below as a hint, not a finding.")
    print()

    for label, key in (("By message structure", "variant"),
                       ("By channel", "channel"), ("By market", "market"),
                       ("By lead type", "type")):
        tot, rep = Counter(), Counter()
        for r in sent:
            k = str(r[key] or "?")[:26]
            tot[k] += 1
            if r["replied"]:
                rep[k] += 1
        if len(tot) <= 1:
            continue
        print(f"  {label}")
        for k, n in tot.most_common(6):
            pct = rep[k] / n * 100 if n else 0
            bar = "#" * int(pct / 10)
            print(f"    {k:<28} {rep[k]:>2}/{n:<3} {pct:>3.0f}%  {bar}")
        print()

    if replied:
        print("  Openings that earned a reply")
        for r in replied[:6]:
            print(f"    {r['brand'][:22]:<24} {r['first_line']}")
        print()
    outcomes = Counter(r["outcome"] for r in sent if r["outcome"])
    if outcomes:
        print("  Outcomes")
        for k, n in outcomes.most_common():
            print(f"    {k:<28} {n}")
    print("=" * 66)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--learn", action="store_true", help="only the stats")
    a = ap.parse_args()
    rows = collect()
    if not rows:
        print("Nothing in vault/04_Sent yet. Send something first.")
        return
    if not a.learn:
        due_list(rows)
    learning(rows)


if __name__ == "__main__":
    main()
