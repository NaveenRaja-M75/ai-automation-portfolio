#!/usr/bin/env python3
"""
CADENCE — which channel each lead gets next, and when.

THE SEQUENCE
    Every lead is touched on a NEW channel each time, never the same one
    twice in a row:

        day 0    first available of  email -> whatsapp -> instagram
        day 4    the next one
        day 10   the next one, and that is the last
        day 21   closed, no reply means no

    A second message on the same channel reads as chasing. The same person
    seeing you once in their inbox, once on WhatsApp and once on Instagram
    reads as someone who is actually around.

    A lead with only one route gets that route twice (day 0 and day 4) and
    nothing more, because three messages in one inbox is spam.

WHY TOUCHES ARE STAMPED, NOT INFERRED
    The WhatsApp page used to decide who was due purely from `sent_on`. So
    after you tapped through eight chats on day 4, every one of them was still
    "due" on day 5, and the page would have offered all eight again. Each
    touch is now written to the note (`email_on`, `wa_on`, `ig_on`) by
    `python run.py done <channel>`, and the sequence reads those dates.
"""
import re
import datetime

GAPS = [0, 4, 10]       # days after the FIRST touch
CLOSE_AFTER = 21

FIELD = {"email": "email_on", "whatsapp": "wa_on", "instagram": "ig_on",
         "linkedin": "li_on"}


def mobile(raw):
    """(digits, kind) where kind is mobile, landline, unknown or none.

    Only the Indian plan is read with confidence: +91 then ten digits opening
    6 to 9 is a mobile, anything else on +91 is a landline and has no
    WhatsApp. Foreign numbers are 'unknown' and allowed through, so a rule
    written for India never silently drops an international lead.
    """
    d = re.sub(r"\D", "", str(raw or ""))
    if not d:
        return "", "none"
    if d.startswith("91"):
        rest = d[2:]
        return d, ("mobile" if len(rest) == 10 and rest[0] in "6789"
                   else "landline")
    return d, ("unknown" if len(d) >= 10 else "landline")


def routes(fm):
    out = []
    if "@" in str(fm.get("email") or ""):
        out.append("email")
    # A number already judged to be a landline, toll-free or a placeholder
    # has no WhatsApp, whatever its length says. Without this every foreign
    # landline counted as 'unknown' and went on the WhatsApp page.
    if str(fm.get("phone_type") or "") not in ("landline", "toll-free", "bad") and             mobile(fm.get("phone"))[1] in ("mobile", "unknown"):
        out.append("whatsapp")
    if str(fm.get("instagram") or "").strip().lstrip("@"):
        out.append("instagram")
    # A LinkedIn post lead is answered on LinkedIn, by hand. Only post leads:
    # a hiring company's page cannot be messaged, so it is never a route there.
    if fm.get("lead_type") == "post" and (fm.get("signal_url") or fm.get("linkedin")):
        out.append("linkedin")
    return out


def _date(v):
    try:
        return datetime.date.fromisoformat(str(v)[:10])
    except Exception:
        return None


def touches(fm):
    """Every touch already made, as [(channel, date)], oldest first.

    Notes sent before this module existed only carry `channel` + `sent_on`,
    so that pair is read as the first touch.
    """
    got = {}
    for ch, f in FIELD.items():
        d = _date(fm.get(f))
        if d:
            got[ch] = d
    legacy = str(fm.get("channel") or "")
    if legacy in FIELD and legacy not in got and _date(fm.get("sent_on")):
        got[legacy] = _date(fm.get("sent_on"))
    out = list(got.items())
    # An email-only lead gets its one nudge on the same channel, and a dict
    # keyed by channel cannot hold that second date. Without its own field the
    # nudge was invisible, so the lead stayed "due" forever.
    if _date(fm.get("email2_on")):
        out.append(("email", _date(fm.get("email2_on"))))
    if _date(fm.get("li2_on")):              # the same, for a LinkedIn-only lead
        out.append(("linkedin", _date(fm.get("li2_on"))))
    return sorted(out, key=lambda x: x[1])


def replied(fm):
    return str(fm.get("replied", "")).lower() in ("yes", "true", "y")


def next_touch(fm, today=None):
    """Return a dict describing the next step, or None when nothing is left.

    keys: channel, step (1, 2 or 3), due (date), overdue_days (int, can be
          negative when not yet due), prior (list of channels already used)
    """
    today = today or datetime.date.today()
    if replied(fm):
        return None
    done = touches(fm)
    avail = routes(fm)
    if not avail:
        return None
    used = [c for c, _ in done]
    step = len(done) + 1

    if done and (today - done[0][1]).days >= CLOSE_AFTER:
        return None

    remaining = [c for c in avail if c not in used]
    if not remaining:
        # single route: allow exactly one repeat as the nudge
        if len(avail) == 1 and len(done) == 1:
            remaining = avail
        else:
            return None
    if step > len(GAPS):
        return None

    first = done[0][1] if done else today
    due = first + datetime.timedelta(days=GAPS[step - 1])
    return {"channel": remaining[0], "step": step, "due": due,
            "overdue_days": (today - due).days, "prior": used}
