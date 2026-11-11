#!/usr/bin/env python3
"""Write drafts for every analyzed note that lacks them.

VARIANT E, the version that was actually approved
    Hook about THEM (what they run, where), then the problem as their client's
    video request turning into a cost decision, then who Monarc is with the
    words "AI" and "ad videos" in plain sight, then a sample offer.

    Variant D, which this file used to write, opened on the problem with no
    detail about the reader and was sent back as "nothing clear, what we
    provide is not mentioned". Every note still carrying D is rewritten with
    --redraft, which is how the 60 international agencies get fixed.

AGENCY vs BRAND
    An agency is never told it needs video. Its CLIENT asks for one, and the
    offer is that the agency can say yes and still know its cost. A brand is
    running many different ads (that is the velocity signal, and it is
    confirmed from the Ad Library), so the offer is new creatives without a
    new shoot each time.

INTERNATIONAL
    Adds one line telling them a reply stops the follow-ups. UK and Canadian
    rules on cold business email expect an easy way out, and it costs nothing
    to give one.

USAGE
    python draft_all.py                 notes with no drafts
    python draft_all.py --redraft       also rewrite notes still on variant D
    python draft_all.py --limit 10
"""
import re
import sys
import argparse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import vault as V
from dispatch import lint, PROMISES_ASSET, extract_drafts

VARIANT = "E"

# ------------------------------------------------------------------ agency
A_EMAIL = """Subject: knowing your video cost before you quote

Hi {name} team,

Saw you handle {work} for clients in {city}. When a client asks you for video, it usually ends up costing more than planned. More edits means more days, more people, more money, so you either quote high or cut corners.

I'm Naveen from Monarc Studio. We make the ad videos for your client using AI, under your agency's name, at one flat price per video, so you always know the cost before you even quote them.

Product commercials, cinematic films and AI UGC, whatever the client is after.

Happy to send a sample made for one of your actual clients if you're interested!
{optout}
Naveen
Monarc Studio"""

A_INSTAGRAM = """Hey {name}! Saw you handle {work} for clients in {city}. Video usually ends up costing more than planned, more edits means more days and more money, so you either quote high or cut corners.

I'm Naveen from Monarc Studio. We make the ad videos for your client using AI, under your agency's name, one flat price per video, so you know your cost before you quote.

Happy to send a sample if you're interested!"""

A_WHATSAPP = """Hey {name}! Saw you handle {work} for clients in {city}. When a client asks for video, it usually costs more than planned. More edits means more days, more people, more money, so you either quote high or cut corners.

I'm Naveen from Monarc Studio. We make the ad videos for your client using AI, under your agency's name, at one flat price per video, so you know your cost before you even quote them.

Happy to send a sample if you're interested!"""

# ------------------------------------------------------------------- brand
B_EMAIL = """Subject: new ad videos without a new shoot

Hi {name} team,

Saw you're running quite a few different ads for your {niche} right now. Keeping fresh creatives coming usually means another shoot, more edits and more budget every few weeks, and ads get tired fast.

I'm Naveen from Monarc Studio. We make ad videos for brands using AI, product commercials, cinematic films and UGC style, at one flat price per video, so a new creative doesn't need a new shoot.

Happy to make a short sample with one of your products if you're interested!
{optout}
Naveen
Monarc Studio"""

B_INSTAGRAM = """Hey {name}! Saw you're running quite a few different ads for your {niche} right now. Keeping creatives fresh usually means another shoot and more budget every few weeks.

I'm Naveen from Monarc Studio. We make ad videos for brands using AI, product commercials and UGC style, one flat price per video.

Happy to make a short sample with one of your products if you're interested!"""

B_WHATSAPP = """Hey {name}! Saw you're running quite a few different ads for your {niche} right now. Keeping fresh creatives coming usually means another shoot, more edits and more budget every few weeks.

I'm Naveen from Monarc Studio. We make ad videos for brands using AI, product commercials and UGC style, at one flat price per video, so a new creative doesn't need a new shoot.

Happy to make a short sample with one of your products if you're interested!"""

# ----------------------------------------------- brand, by what is verified
# The three B_* messages above open with "Saw you're running quite a few
# different ads right now". That is only true, and only allowed to be said,
# when the Ad Library confirmed it for THIS brand. The jewellery research
# list and every brand without a page id had it said to them anyway. So the
# opener is now chosen by evidence:
#     confirmed several live ads, none of them video  -> B0 (first video)
#     confirmed several live ads                      -> B  (more creatives)
#     nothing confirmed                               -> BN (neutral)
B0_EMAIL = """Subject: video versions of your ads

Hi {name} team,

Saw you're running quite a few ads right now, all of them still images. Video usually gets tested last because it means a shoot, a crew and weeks of waiting.

I'm Naveen from Monarc Studio. We turn products into ad videos using AI, product commercials and UGC style, at one flat price per video, so you can test video without booking a shoot.

Happy to make a short sample with one of your products if you're interested!
{optout}
Naveen
Monarc Studio"""

B0_INSTAGRAM = """Hey {name}! Saw you're running quite a few ads right now, all still images. Video usually gets tested last because it means a shoot and weeks of waiting.

I'm Naveen from Monarc Studio. We turn products into ad videos using AI, one flat price per video, no shoot needed.

Happy to make a short sample with one of your products if you're interested!"""

B0_WHATSAPP = """Hey {name}! Saw you're running quite a few ads right now, all of them still images. Video usually gets tested last because it means a shoot, a crew and weeks of waiting.

I'm Naveen from Monarc Studio. We turn products into ad videos using AI, product commercials and UGC style, at one flat price per video, so you can test video without booking a shoot.

Happy to make a short sample with one of your products if you're interested!"""

BN_EMAIL = """Subject: ad videos for {name}

Hi {name} team,

Came across {name} and really liked what you're making. Most {kind} we talk to want more ad videos than they can shoot, because every new creative means another shoot, more edits and more budget.

I'm Naveen from Monarc Studio. We make ad videos using AI, product commercials, cinematic films and UGC style, at one flat price per video, so a new creative doesn't need a new shoot.

Happy to make a short sample for you if you're interested!
{optout}
Naveen
Monarc Studio"""

BN_INSTAGRAM = """Hey {name}! Came across your page and really liked what you're making.

I'm Naveen from Monarc Studio. We make ad videos using AI, product commercials and UGC style, at one flat price per video, so a new creative doesn't need a new shoot.

Happy to make a short sample for you if you're interested!"""

BN_WHATSAPP = """Hey {name}! Came across {name} and really liked what you're making. Most {kind} we talk to want more ad videos than they can shoot, because every new creative means another shoot and more budget.

I'm Naveen from Monarc Studio. We make ad videos using AI, product commercials and UGC style, at one flat price per video.

Happy to make a short sample for you if you're interested!"""

OPTOUT_INTL = "\nIf this isn't relevant, just reply and I won't follow up.\n"


def _num(v):
    try:
        return int(float(str(v).strip()))
    except (TypeError, ValueError):
        return None


def brand_evidence_level(fm):
    """'none', 'ads' or 'ads_no_video', from the Meta check recorded on the
    lead. Both vault notes (active_ads) and workbook records (ads_now) work."""
    ads = _num(fm.get("ads_now") if fm.get("ads_now") is not None else fm.get("active_ads"))
    vids = _num(fm.get("videos_now") if fm.get("videos_now") is not None else fm.get("video_ads"))
    distinct = _num(fm.get("distinct_now") if fm.get("distinct_now") is not None
                    else fm.get("distinct_creatives"))
    if ads is None or ads < 4 or (distinct is not None and distinct < 3):
        return "none"
    return "ads_no_video" if vids == 0 else "ads"

# ------------------------------------------------------------------- names
# "co" is deliberately absent: "Binary & Co" and "Leads & Co" are what those
# agencies are called out loud, and trimming it greeted them as "Binary &".
TRIM = re.compile(
    r"\b(private limited|pvt\.? ?ltd\.?|limited|ltd\.?|llp|inc\.?|llc|"
    r"company)\b\.?", re.I)

# "Webwisez – Digital Marketing Classes" and "Thistle Media � Best ..." both
# carry the SEO tail after a separator. The replacement character is what an
# en dash becomes after one bad re-encoding in the source sheet, so it counts.
SEPARATOR = re.compile(r"\s+[|–—/]\s*|\s+-\s+|\s*[(:,]\s*")

# Words that only ever appear in a listing's marketing, never in a greeting.
PADDING = re.compile(
    r"\s*\b(best|top|leading|no\.? ?1|award winning)\b.*$", re.I)

GENERIC_TAIL = re.compile(
    r"\s+(digital marketing|marketing|advertising|social media)?\s*"
    r"(agency|agencies|company|services?|solutions?)$", re.I)


def short_name(full):
    """A name a human would actually type in a greeting.

    Everything after the first separator is listing SEO and is cut. A trailing
    generic word ("Digital Marketing Agency") is cut only when a real name is
    left in front of it, so "Digital Agency Delhi" does not collapse to
    "Digital Delhi" the way the old suffix trim made it.
    """
    # The replacement character becomes a real separator first, so the SEO
    # tail after it is cut like any other dash.
    s = str(full).replace("�", " - ").strip()
    s = SEPARATOR.split(s)[0]
    s = PADDING.sub("", s)
    s = TRIM.sub("", s).strip(" -–—:,.")
    s = re.sub(r"\s{2,}", " ", s)
    trimmed = GENERIC_TAIL.sub("", s).strip()
    if len(trimmed) >= 3:
        s = trimmed
    words = s.split()
    if len(words) > 3:
        s = " ".join(words[:3])
    return s or str(full)[:24]


def greeting_name(fm):
    """A hand-set `greeting_name` in the note always wins over the guess."""
    g = fm.get("greeting_name")
    g = g.strip() if isinstance(g, str) else ""
    return g or short_name(fm.get("brand", ""))


# ------------------------------------------------------------------ detail
def work_phrase(services):
    """Turn a listing's services into what a person would say they do."""
    s = str(services or "").lower()
    paid = re.search(r"ppc|google ads|facebook ads|meta ads|instagram ads|"
                     r"media buying|display ads|youtube ads|performance", s)
    social = "social media" in s
    ecom = "ecommerce" in s or "amazon" in s
    brand = "branding" in s or "graphic design" in s or "creative" in s
    if paid and social:
        return "paid and social campaigns"
    if ecom and social:
        return "ecommerce and social campaigns"
    if paid:
        return "paid ad campaigns"
    if social:
        return "social campaigns"
    if brand:
        return "branding and creative work"
    return "digital marketing"


def niche_phrase(fm):
    n = fm.get("niche")
    n = n.strip().lower() if isinstance(n, str) else ""
    return n or "products"


def is_international(fm):
    m = str(fm.get("market") or "").lower()
    c = fm.get("country")
    c = c.lower() if isinstance(c, str) else ""
    sc = fm.get("site_country")
    sc = sc.lower() if isinstance(sc, str) else ""
    return (m.startswith("international") or (c not in ("", "india", "in"))
            or (sc not in ("", "india")))


def text(v):
    return v.strip() if isinstance(v, str) else ""


def drafts_for(fm):
    name = greeting_name(fm)
    optout = OPTOUT_INTL if is_international(fm) else ""
    if fm.get("lead_type") == "agency":
        city = text(fm.get("city")) or text(fm.get("country")) or "your city"
        f = dict(name=name, city=city, work=work_phrase(fm.get("services")),
                 optout=optout)
        return {"email": A_EMAIL.format(**f), "instagram": A_INSTAGRAM.format(**f),
                "whatsapp": A_WHATSAPP.format(**f)}
    level = brand_evidence_level(fm)
    kind = "brands" if text(fm.get("business_type")) in ("", "D2C brand") else "businesses"
    f = dict(name=name, niche=niche_phrase(fm), optout=optout, kind=kind)
    if level == "ads_no_video":
        return {"email": B0_EMAIL.format(**f), "instagram": B0_INSTAGRAM.format(**f),
                "whatsapp": B0_WHATSAPP.format(**f)}
    if level == "ads":
        return {"email": B_EMAIL.format(**f), "instagram": B_INSTAGRAM.format(**f),
                "whatsapp": B_WHATSAPP.format(**f)}
    return {"email": BN_EMAIL.format(**f), "instagram": BN_INSTAGRAM.format(**f),
            "whatsapp": BN_WHATSAPP.format(**f)}


def check(d, names=()):
    """Lint every draft. The lead's own name is taken out first: "Social
    Elevate Agency" is what that agency is called, and blocking it for the
    AI tell "elevate" would mean never being allowed to greet them."""
    bad = []
    for chan, t in d.items():
        scan = t
        for n in names:
            if n:
                scan = re.sub(re.escape(n), " ", scan, flags=re.I)
        bad += [f"{chan}: {x}" for x in lint(scan)]
        if PROMISES_ASSET.search(t):
            bad.append(f"{chan}: promises an existing asset")
        if re.search(r"https?://|www\.", t):
            bad.append(f"{chan}: contains a link")
        if re.search(r"\{\w+\}", t):
            bad.append(f"{chan}: unfilled placeholder")
    return bad


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--redraft", action="store_true",
                    help="also rewrite notes still on an older variant")
    a = ap.parse_args()

    todo = []
    for p in V.notes_in("analyzed"):
        fm, body = V.read_note(p)
        # LinkedIn post and hiring leads are drafted by li_vault.py from their
        # own signal. A brand template here would replace "saw you're hiring
        # an AI video producer" with a generic pitch, so they are left alone.
        if fm.get("lead_type") in ("post", "hiring"):
            continue
        has = bool(extract_drafts(body))
        stale = text(fm.get("variant")) not in ("", VARIANT)
        if not has or (a.redraft and stale):
            todo.append((p, fm, body))
    if a.limit:
        todo = todo[:a.limit]
    if not todo:
        print("Every analyzed note already has current drafts.")
        return

    print(f"Writing variant {VARIANT} drafts for {len(todo)} leads\n")
    done = blocked = 0
    for p, fm, body in todo:
        d = drafts_for(fm)
        bad = check(d, (greeting_name(fm), text(fm.get("brand"))))
        if bad:
            print(f"  BLOCKED {fm.get('brand')}: {bad[:2]}")
            blocked += 1
            continue
        # Cutting at the Email heading also drops the old footer, and with it
        # any approval tag. An approval given to old words must not carry
        # over onto new ones.
        body = re.split(r"\n###\s*Email\b", body)[0].rstrip()
        fm["variant"] = VARIANT
        body += ("\n\n### Email\n" + d["email"] +
                 "\n\n### Instagram\n" + d["instagram"] +
                 "\n\n### WhatsApp\n" + d["whatsapp"] +
                 "\n\n---\n## To advance this lead\nWrite the approval tag on "
                 "its own line below.\n")
        p.write_text("---\n" + V._fm_dump(fm) + "\n---\n\n" + body.strip() + "\n",
                     encoding="utf-8")
        done += 1
        where = (text(fm.get("city")) or text(fm.get("country")))[:14]
        detail = (work_phrase(fm.get("services")) if fm.get("lead_type") == "agency"
                  else niche_phrase(fm))
        print(f"  {greeting_name(fm)[:28]:<30} {where:<15} {detail}")

    print(f"\n{done} drafted, {blocked} blocked")


if __name__ == "__main__":
    main()
