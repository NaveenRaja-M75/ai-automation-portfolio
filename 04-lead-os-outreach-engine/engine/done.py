#!/usr/bin/env python3
"""
DONE — record that the messages on today's queue page actually went out.

    python run.py done whatsapp
    python run.py done instagram --skip "Digises, Klasse"

Reads the list the queue page was built from (_state/queue.json), so it can
only ever stamp leads that were really on your screen. A lead that was never
contacted before is filed into 04_Sent. A lead already there gets its touch
date added, which is what moves it along the sequence in cadence.py.

It refuses a queue built on an earlier day. Stamping yesterday's list today
would record messages against people you may not have messaged at all.
"""
import sys
import json
import argparse
import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import vault as V
import cadence as C
from outreach_queue import QUEUE_STATE


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("channel", choices=["whatsapp", "instagram"])
    ap.add_argument("--skip", default="",
                    help="comma separated names you did NOT send to")
    a = ap.parse_args()

    try:
        q = json.loads(QUEUE_STATE.read_text(encoding="utf-8"))
    except Exception:
        print("No queue found. Run `python run.py queue` first.")
        return
    today = datetime.date.today().isoformat()
    if q.get("built") != today:
        print(f"The queue page was built on {q.get('built')}, not today. "
              f"Rebuild it with `python run.py queue`, send, then run this.")
        return

    skip = [s.strip().lower() for s in a.skip.split(",") if s.strip()]
    field = C.FIELD[a.channel]
    stamped, skipped, missing = [], [], []
    for it in q.get("items", []):
        if it["channel"] != a.channel:
            continue
        brand = it["brand"]
        if any(s in brand.lower() for s in skip):
            skipped.append(brand)
            continue
        path = Path(it["path"])
        if not path.exists():
            # the note moved since the page was built; find it by filename
            hits = [p for st in V.STAGES for p in V.notes_in(st)
                    if p.name == path.name]
            if not hits:
                missing.append(brand)
                continue
            path = hits[0]
        fm, _ = V.read_note(path)
        if fm.get(field):
            continue                       # already stamped, never double count
        updates = {field: today}
        if fm.get("status") != "sent":
            updates.update({"channel": a.channel, "sent_on": today,
                            "replied": "", "reply_date": "", "outcome": "",
                            "variant": fm.get("variant", "E")})
            V.remember([brand])
        V.move_note(path, "sent", updates)
        stamped.append(brand)

    print(f"{len(stamped)} {a.channel} messages recorded")
    for b in stamped:
        print(f"  + {b}")
    for b in skipped:
        print(f"  - {b} (skipped, stays in the queue)")
    for b in missing:
        print(f"  ? {b} (note not found)")


if __name__ == "__main__":
    main()
