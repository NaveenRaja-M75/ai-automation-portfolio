#!/usr/bin/env python3
"""
Stage 4 — DISPATCH. Prepare an approved lead for sending. Never sends.

WHY NOTHING IS EVER SENT AUTOMATICALLY
    Two separate reasons, both non-negotiable.

    1. Platform safety. Automated Instagram and WhatsApp messaging is the
       fastest way to get an account banned. Monarc's own social accounts are
       business assets worth more than any lead list, so the machine prepares
       the message and a human presses send. That takes four seconds and
       removes the entire ban risk.

    2. Brand safety. Every message goes out under your name. A studio selling
       taste cannot afford a bot sending something slightly wrong at 3am.

    So this stage opens the right window, puts the right text on your
    clipboard, and gets out of the way.

WHAT IT ENFORCES BEFORE ANYTHING LEAVES
    - the note carries #approved, added by you and nobody else
    - a draft message actually exists for the channel being used
    - the message contains no banned word and no AI tell
    - a claim graded anything other than `confirmed` never appears
    - a daily cap, because volume is what turns outreach into spam
"""
import re
import sys
import json
import argparse
import datetime
import subprocess
import urllib.parse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import vault as V

DAILY_CAP = 20
LOG = V.STATE / "sent_log.json"

BANNED = ["free", "guarantee", "risk-free", "risk free", "100%", "click here",
          "act now", "limited time", "special offer", "discount", "cheap",
          "exclusive deal", "don't miss", "urgent", "no obligation",
          "dear sir", "dear madam", "to whom it may concern"]
AI_TELLS = ["—", ";", "hope this email finds you well", "i wanted to reach out",
            "elevate", "unlock", "leverage", "seamless", "cutting-edge",
            "game-changing", "in today's fast-paced", "delve", "robust",
            "looking forward to hearing from you soon"]


def load_log():
    try:
        return json.loads(LOG.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_log(d):
    V.STATE.mkdir(parents=True, exist_ok=True)
    LOG.write_text(json.dumps(d, indent=1), encoding="utf-8")


def sent_today(log):
    today = datetime.date.today().isoformat()
    return sum(1 for v in log.values() if v.get("date") == today)


def lint(text):
    """Return the reasons this message must not go out as written.

    Banned words match on WORD BOUNDARIES. A plain substring test flagged a
    perfectly good sentence about sending work to a "freelancer" because it
    contains "free", and would equally reject "freedom" or "freely". A linter
    that blocks correct writing gets switched off, and then it protects
    nothing at all.
    """
    problems = []
    low = text.lower()
    for w in BANNED:
        pattern = r"\b" + re.escape(w) + r"\b"
        if re.search(pattern, low):
            problems.append(f"banned word: '{w}'")
    for t in AI_TELLS:
        # punctuation tells (em dash, semicolon) have no word boundary
        if len(t) <= 2:
            if t in text:
                problems.append(f"AI tell: '{t}'")
        elif re.search(r"\b" + re.escape(t) + r"\b", low):
            problems.append(f"AI tell: '{t}'")
    # Imported templates carry slots like {{matched_sample_url}}; a draft with one
    # still unfilled must never reach a prospect.
    if re.search(r"\{\{[^}]*\}\}", text):
        problems.append("unfilled template placeholder: " + re.search(r"\{\{[^}]*\}\}", text).group(0))
    return problems




def extract_drafts(body):
    """Pull the per-channel drafts out of the note's Draft messages section.

    The capture must stop at the next heading OR a horizontal rule. Without
    the rule, an EMPTY "### WhatsApp" section ran straight on into the note's
    own footer, and dispatch happily prepared "## To advance this lead. Write
    the approval tag..." as the message to send a prospect. Internal
    instructions were one keystroke from going out to a real agency.

    Anything that still looks like note scaffolding is discarded rather than
    trusted, because a draft is text YOU wrote, never text the template left
    behind.
    """
    SCAFFOLD = re.compile(r"^(#|-{3,}|>|\(|To advance|Write the approval|"
                          r"Run `|Generated at|Written at)", re.I)
    out = {}
    for chan, pat in (("email", r"###\s*Email\b(.*?)(?=\n###|\n---|\Z)"),
                      ("instagram", r"###\s*Instagram\b(.*?)(?=\n###|\n---|\Z)"),
                      ("whatsapp", r"###\s*WhatsApp\b(.*?)(?=\n###|\n---|\Z)"),
                      ("linkedin", r"###\s*LinkedIn\b(.*?)(?=\n###|\n---|\Z)")):
        m = re.search(pat, body, re.S | re.I)
        if not m:
            continue
        txt = re.sub(r"^\s*>\s?", "", m.group(1).strip(), flags=re.M).strip()
        if len(txt) < 25:
            continue
        lines = [l for l in txt.splitlines() if l.strip()]
        if not lines or SCAFFOLD.match(lines[0].strip()):
            continue                     # template leftovers, not a message
        out[chan] = txt
    return out


def copy_clipboard(text):
    try:
        subprocess.run("clip", input=text.encode("utf-16le"), check=True)
        return True
    except Exception:
        return False


def pick_channel(fm, drafts):
    """Instagram first for a brand that lives on Instagram, which any Meta
    advertiser does. Phone next, email last, Messenger as the always-works
    fallback when a brand publishes nothing.

    A LinkedIn post lead is different: they asked in public, so email wins
    when they wrote one in the post (they asked to be emailed), and otherwise
    the reply goes on LinkedIn itself, typed by hand from your own account."""
    if fm.get("lead_type") == "post":
        if fm.get("email") and "email" in drafts:
            return "email"
        if (fm.get("signal_url") or fm.get("linkedin")) and "linkedin" in drafts:
            return "linkedin"
        return ""
    if fm.get("instagram") and "instagram" in drafts:
        return "instagram"
    if fm.get("phone") and "whatsapp" in drafts:
        return "whatsapp"
    if fm.get("email") and "email" in drafts:
        return "email"
    if fm.get("fb_messenger"):
        return "messenger"
    if fm.get("linkedin") and "linkedin" in drafts:
        return "linkedin"
    return ""


def link_for(channel, fm, text):
    if channel == "instagram":
        return "https://instagram.com/" + str(fm.get("instagram", "")).lstrip("@")
    if channel == "whatsapp":
        num = re.sub(r"\D", "", str(fm.get("phone", "")))
        return f"https://wa.me/{num}?text=" + urllib.parse.quote(text[:900])
    if channel == "email":
        return "mailto:" + str(fm.get("email", ""))
    if channel == "linkedin":
        # the post itself, so the reply lands under their ask; profile as fallback
        return str(fm.get("signal_url") or fm.get("linkedin", ""))
    return str(fm.get("fb_messenger", ""))


def reel_ready():
    """Every draft promises a reel. Sending before one exists is the single
    most avoidable way to lose an agency: they reply "send it" within the
    hour, and a two-day render tells them you were not ready. So this is a
    hard gate rather than a reminder."""
    txt = V.control("ASSETS.md")
    # Must be an actual URL. A looser \S+ match happily captured the inline
    # comment that follows the empty key ("reel_url:   # paste the link here"),
    # so the gate reported a reel that did not exist and unlocked itself.
    m = re.search(r"^\s*reel_url:\s*(https?://\S+)", txt, re.M)
    return (m.group(1) if m else "")


# Promising an asset that ALREADY EXISTS is what needs the file on hand.
# Offering to MAKE one is a different promise entirely, and a much better
# opener: "want a sample made for one of your clients" commits you to work
# only once they say yes, and it needs no pre-built reel at all.
#
# The distinction matters because gating the second kind would block the
# strongest message we have. So the gate looks only for an existing thing
# being sent or attached now.
PROMISES_EXISTING_ASSET = re.compile(
    r"\b(attached|i attached|attach(ing)?\b|"
    r"(send|sending|share|sharing|drop|dropping)\s+(you\s+)?"
    r"(our|the|a|my)\s+(reel|showreel|portfolio|deck|case study)|"
    r"(our|my)\s+(reel|showreel|portfolio|deck))\b", re.I)

# Kept under the old name so nothing else breaks.
PROMISES_ASSET = PROMISES_EXISTING_ASSET


def run(dry=False, batch=False):
    """The reel gate is per MESSAGE, not global.

    An earlier version blocked every send until a reel existed, which was too
    blunt. You can absolutely pitch without one: a message that names a real
    observation and asks a question needs no asset at all, and often reads
    better for it. What you must never do is promise a reel you cannot send
    within the hour.

    So the rule is narrow and exact: if THIS draft promises an asset, the
    asset must exist. If it does not promise one, send it today.
    """
    reel = reel_ready()
    notes = V.approved_notes()
    if not notes:
        print("Nothing approved. Open a note in vault/02_Analyzed/ and add "
              "#approved once you are happy with the draft.")
        return
    log = load_log()
    used = sent_today(log)
    room = max(0, DAILY_CAP - used)
    print(f"{len(notes)} approved | {used} already prepared today | "
          f"{room} left under the daily cap of {DAILY_CAP}\n")
    if room == 0:
        print("Daily cap reached. Volume is what turns outreach into spam.")
        return

    # ONE AT A TIME BY DEFAULT.
    #
    # The clipboard holds exactly one thing. Looping through ten approved
    # leads copied each message in turn and left only the last one there, so
    # nine of them could never be pasted. Worse, all ten were marked sent and
    # written to the ledger, which means nine agencies would have been
    # recorded as contacted while never receiving anything, and the ledger
    # would refuse to surface them again.
    #
    # So a run prepares one lead, and you run it again for the next. That also
    # paces the sending, which is what keeps an Instagram account healthy.
    if not batch and not dry:
        notes = notes[:1]
        room = 1

    done = 0
    for path in notes[:room]:
        fm, body = V.read_note(path)
        brand = fm.get("brand", path.stem)

        if V.blocked(brand):
            print(f"BLOCKED {brand}: on the do-not-contact list. Never send.")
            # A dry run must change nothing. This move ran unconditionally and
            # filed notes into 99_Rejected during a preview, which quietly
            # rewrote state the user was only inspecting. If --dry-run has any
            # side effect at all, it stops being safe to run.
            if not dry:
                V.move_note(path, "rejected", {"reason": "do-not-contact"})
            continue

        if fm.get("given_to_team"):
            print(f"SKIP {brand}: handed to the team on {fm.get('given_to_team')}. "
                  f"They contact it, not you, so it is never pitched twice.")
            continue

        ev = fm.get("evidence", {}) or {}
        if isinstance(ev, dict) and ev.get("no_video") not in ("confirmed", None):
            print(f"SKIP {brand}: no_video is '{ev.get('no_video')}', not confirmed. "
                  f"Do not open with a claim you have not verified.")
            continue

        drafts = extract_drafts(body)
        if not drafts:
            print(f"SKIP {brand}: no draft written yet. Ask Claude Code to "
                  f"write it from the note, then re-approve.")
            continue

        channel = pick_channel(fm, drafts)
        if not channel:
            print(f"SKIP {brand}: no usable contact route.")
            continue
        text = drafts.get(channel) or drafts.get("email", "")
        problems = lint(text)
        if problems:
            print(f"BLOCK {brand}: {'; '.join(problems[:3])}")
            continue

        promise = PROMISES_ASSET.search(text)
        if promise and not reel:
            print(f"HOLD  {brand}: this draft promises a "
                  f"'{promise.group(0).lower()}' and no reel_url is set.")
            print(f"       Either add the link to ASSETS.md, or rewrite this "
                  f"draft to ask a question instead of offering an asset.")
            continue

        link = link_for(channel, fm, text)
        print("=" * 60)
        print(f"{brand}   ->   {channel.upper()}")
        print("=" * 60)
        print(text)
        print("-" * 60)
        print("open:", link)
        if not dry:
            if copy_clipboard(text):
                print("message copied to clipboard, paste and send")
            log[V.norm(brand)] = {"date": datetime.date.today().isoformat(),
                                  "channel": channel, "brand": brand}
            V.move_note(path, "sent", {
                "channel": channel,
                "sent_on": datetime.date.today().isoformat(),
                # written empty on purpose: these are the fields you fill in
                # when a reply lands, and `run.py followup` reads them.
                "replied": "", "reply_date": "", "outcome": "",
                # which message structure was sent. Two are in play and
                # nobody knows which wins, so record it and let replies
                # settle the argument instead of opinion.
                "variant": fm.get("variant", "A")})
            V.remember([brand])
        done += 1
        print()

    if not dry:
        save_log(log)
    # The dispatched note has already moved out of the approved set, so
    # subtracting `done` again counted it twice and under-reported the queue.
    left = len(V.approved_notes())
    print(f"{done} lead prepared. Nothing was sent automatically, by design.")
    if not dry and left > 0:
        print(f"{left} still approved. Paste and send this one, then run "
              f"`python run.py dispatch` again for the next.")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--batch", action="store_true",
                    help="prepare every approved lead at once. The clipboard "
                         "will only hold the LAST one, so use this to review, "
                         "not to send.")
    a = ap.parse_args()
    run(a.dry_run, a.batch)
