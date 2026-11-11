#!/usr/bin/env python3
"""
LinkedIn engine, vault: load tier A/B leads into vault/02_Analyzed.

    python engine/li_vault.py            import (tiers from LINKEDIN.md, default A,B)
    python engine/li_vault.py --dry-run  show what would be added

Lead types written:
    post     somebody asked for AI ads / AI UGC in a LinkedIn post
    hiring   a company has an open AI video / AI UGC / AI creative role
    agency   niche agency  (drafted later by draft_all.py, AGENCY_PITCH shape)
    brand    niche brand   (drafted later by draft_all.py)

Post and hiring leads are drafted HERE, because their only personal detail is
the signal itself: the role they posted or the words they used. draft_all.py
is told to leave these two types alone, so a redraft can never swap a hiring
pitch for a brand template.

Like foreign_to_vault, it never overwrites a note. A lead already in the vault
(by file name, email or website) only gets its empty fields filled.
"""
import re
import sys
import argparse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import li_lib as L
import li_build as LB
import li_dedupe as D
import vault as V
from draft_all import check, OPTOUT_INTL
from foreign_to_vault import existing_index

try:
    from foreign_build import send_time
except Exception:                      # timing is a nicety, never a blocker
    def send_time(country, city=""):
        return ""

VARIANT = "LI1"

# Three openers per lead type. VOICE.md: two messages that start the same way
# read as a sequence. The choice is a stable hash of the name, so a re-import
# never rewrites a draft you have already read.
HIRING_OPEN = ["Saw on LinkedIn that you're hiring {a_role}.",
               "Noticed your {role} opening on LinkedIn.",
               "Your {role} opening on LinkedIn came up this week."]
# Never "re:": a fake reply prefix is a misleading subject line (CAN-SPAM).
HIRING_SUBJ = ["your {role} role", "the {role} opening", "{role}, while you hire"]
# When a title is too messy to repeat ("Senior Motion / VFX / AI", "AI Generalist
# Melbourne 2026"), the draft names the work instead of mangling the title.
GENERIC_ROLE, GENERIC_A_ROLE = "AI video", "someone for AI video work"
CAPS = {"email": 90, "instagram": 50, "whatsapp": 60, "linkedin": 50}   # VOICE.md
POST_OPEN = ["Saw your LinkedIn post {need_line}.",
             "Came across your post {need_line}.",
             "Read your LinkedIn post {need_line} this week."]


def role_short(title, location=""):
    """LinkedIn title -> the role a person would say out loud, or "" if too messy.

    '(AI Visual) AI Producer' -> 'AI Producer'
    'Hiring | AI Filmmaker - Cinematic AI Video' -> 'AI Filmmaker'
    'AI Video Creative Producer (m/w/d)' -> 'AI Video Creative Producer'
    'AI Generalist Melbourne 2026' -> 'AI Generalist' (place words removed)
    'Senior Motion / VFX / AI' -> ''  (caller falls back to "AI video")
    Titles carry em dashes and pipes, which the voice lint rightly blocks.
    """
    t = re.sub(r"[(\[][^)\]]*[)\]]", " ", title or "")
    for w in re.findall(r"[A-Za-z]{3,}", location or ""):
        t = re.sub(r"\b" + re.escape(w) + r"\b", " ", t, flags=re.I)
    t = re.sub(r"\b(19|20)\d{2}\b", " ", t)
    t = re.sub(r"^\s*(hiring|we'?re hiring|urgent)\s*[:|\-–—]\s*", "", t, flags=re.I)
    t = re.sub(r"\b(full[- ]?time|part[- ]?time|remote|hybrid|on[- ]?site|contract|"
               r"freelance|temporary|internship|m/w/d|f/m/d)\b", " ", t, flags=re.I)
    parts = [p.strip(" .,:/") for p in re.split(r"\s[—–|\-]\s|\s*[—–|]\s*", t)
             if re.search(r"[A-Za-z]{2}", p)]
    best = re.sub(r"\s+", " ", parts[0] if parts else "creative").strip(" .,:/&")[:60]
    if best.isupper() and len(best) > 4:     # 'AI VIDEO EDITOR' -> 'AI Video Editor'
        best = " ".join(w if w in ("AI", "UGC", "VFX", "3D", "2D", "CGI") else w.title()
                        for w in best.split())
    if re.search(r"[/:&+\d]", best) or not 1 <= len(best.split()) <= 5:
        return ""
    return best


def words(text):
    """Word count the way VOICE.md means it: the message, not the furniture.
    Subject line, opt-out line and sign-off are not counted."""
    body = re.sub(r"^Subject:.*$", "", text, flags=re.M)
    body = body.replace(OPTOUT_INTL.strip(), "")
    body = re.sub(r"\n\s*Naveen\s*\n\s*Monarc Studio\s*$", "", body.strip())
    return len(body.split())


def greet_name(company):
    """'Anatta - Shopify Platinum Partner' -> 'Anatta', 'Edison Smart®' -> 'Edison Smart'.

    Only for the greeting. The full name stays in `brand`, where dedupe needs it.
    """
    n = re.sub(r"[(\[][^)\]]*[)\]]", " ", company or "")
    n = re.sub(r"[®™©]", "", n)
    n = re.split(r"\s[—–|\-:]\s|\s*[—–|]\s*", n)[0]
    for _ in range(2):    # "Acme Media Pvt. Ltd." needs two passes
        n = re.sub(r",?\s+(inc|llc|llp|ltd|limited|pvt|private limited|private|gmbh|corp|"
                   r"fze|fzco|fz-llc|pty|plc|co)\.?$", "", n.strip(), flags=re.I)
    n = re.sub(r"\s+", " ", n).strip(" .,&")
    if n.isupper() and len(n) > 4:   # 'RESONANCE MEDIA SOLUTIONS' -> 'Resonance Media Solutions'
        n = " ".join(w if len(w) <= 3 else w.title() for w in n.split())
    return n or (company or "").strip()


def a_role(role):
    """'an AI Producer', 'a UGC Creator', 'an Editor'."""
    return ("an " if re.match(r"(?i)[aeio]|ai\b", role) else "a ") + role


def pick(options, name):
    import zlib
    return options[zlib.crc32(str(name).encode("utf-8")) % len(options)]


# A brand has products. An agency has clients and needs its own name on the
# work, so it gets the white-label version (AGENCY_PITCH.md: say white-label
# early or they read you as a competitor after their clients).
HIRING_EMAIL = {
    "brand": """Subject: {subject}

Hi {name} team,

{opener}

I run Monarc Studio. We make AI ads, AI UGC and launch films at a flat price per video, and we can start this week while you find the right person.

Not instead of the hire. Just ads that keep shipping meanwhile.

Want me to make a short sample for one of your products first, so you can judge it?
{optout}
Naveen
Monarc Studio""",
    "agency": """Subject: {subject}

Hi {name} team,

{opener}

I run Monarc Studio. We make AI ads and AI UGC white-label, under your agency's name, at a flat price per video. We can take client work this week while you hire.

Want me to make a short sample for one of your clients first, so you can judge it?
{optout}
Naveen
Monarc Studio""",
}
HIRING_SHORT = {
    "brand": ("Hi {name} team! Saw you're hiring {a_role} on LinkedIn. We make AI ads "
              "and AI UGC at a flat price per video, and can start this week while you "
              "hire. Want a short sample for one of your products?"),
    "agency": ("Hi {name} team! Saw you're hiring {a_role} on LinkedIn. We make AI ads "
               "and AI UGC white-label, under your name, and can take client work this "
               "week. Want a short sample for one of your clients?"),
}

POST_EMAIL = """Subject: your post about {need_short}

Hi {first},

{opener}

I run Monarc Studio, a small AI ad studio. AI UGC, AI product ads and AI commercials are what we make every week, so it lines up with what you described.

Happy to make a short sample to your brief first. Want one?
{optout}
Naveen
Monarc Studio"""

POST_LINKEDIN = ("Hi {first}, saw your post {need_line}. I run Monarc Studio, we make AI "
                 "UGC, AI product ads and AI commercials every week. Happy to make a short "
                 "sample to your brief first. Want one?")


US_STATES = set("AL AK AZ AR CA CO CT DE FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN MS "
                "MO MT NE NV NH NJ NM NY NC ND OH OK OR PA RI SC SD TN TX UT VT VA WA WV "
                "WI WY DC".split())


def country_city(location, market):
    parts = [p.strip() for p in (location or "").split(",") if p.strip()]
    if market == "remote" or not parts:
        return ("Remote" if market == "remote" else ""), ""
    country = parts[-1]
    # LinkedIn writes US jobs as "Los Angeles, CA": a state code, no country.
    if re.fullmatch(r"[A-Z]{2}", country) and country in US_STATES:
        return "United States", parts[0] if len(parts) > 1 else ""
    if country in ("United States", "United Kingdom", "Australia", "India",
                   "United Arab Emirates", "Canada", "Singapore"):
        return country, parts[0] if len(parts) > 1 else ""
    return country, ""


def first_name(author):
    """'Dr. Priya Raman' -> 'Priya'. Anything that does not look like a first
    name (a company posting, an emoji handle) becomes 'there'."""
    w = [x.strip(".,") for x in (author or "").split()]
    while w and w[0].lower() in ("dr", "mr", "mrs", "ms", "miss", "prof", "er", "ca",
                                 "adv", "sir", "dame"):
        w = w[1:]
    if w and re.fullmatch(r"[A-Z][a-zA-Z'\-]{1,20}", w[0]) and len(w) <= 5:
        return w[0]
    return "there"


def tier_label(t):
    return {"A": "A - strong", "B": "B - workable"}.get(t, "C - weak, low priority")


def market_label(country):
    if not country or country == "Remote":
        return "International - Remote"
    return "India" if country == "India" else f"International - {country}"


# ------------------------------------------------------------------ notes
def hiring_note(r):
    country, city = country_city(r.get("location"), r.get("market"))
    name = r["company"]
    intl = country != "India"
    role = role_short(r["title"], r.get("location", ""))
    f = dict(name=greet_name(name), role=role or GENERIC_ROLE,
             a_role=a_role(role) if role else GENERIC_A_ROLE,
             optout=OPTOUT_INTL if intl else "")
    f["opener"] = pick(HIRING_OPEN, name).format(**f)
    f["subject"] = pick(HIRING_SUBJ, name[::-1]).format(**f)
    kind = "agency" if r.get("kind") == "agency" else "brand"
    drafts = {"email": HIRING_EMAIL[kind].format(**f),
              "instagram": HIRING_SHORT[kind].format(**f),
              "whatsapp": HIRING_SHORT[kind].format(**f)}
    fm = {
        "status": "analyzed", "brand": name, "greeting_name": greet_name(name),
        "lead_type": "hiring", "market": market_label(country), "country": country,
        "city": city, "email": r.get("email", ""), "email_check": r.get("email_check", ""),
        "instagram": r.get("instagram", ""), "phone": r.get("phone", ""),
        "website": r.get("website", ""), "linkedin": r.get("company_url", ""),
        "signal_url": r.get("link", ""), "signal": f"hiring: {r['title']}",
        "signal_date": r.get("posted", ""), "company_type": r.get("kind", ""),
        "company_size": r.get("size", ""), "industry": r.get("industry", ""),
        "tier": tier_label(r["tier"]), "quality": r["score"],
        "first_channel": "Email" if r.get("email") else "LinkedIn",
        "send_at_ist": send_time(country, city), "sourced": L.TODAY.isoformat(),
        "source": "linkedin jobs", "variant": VARIANT,
        "tags": ["lead", "linkedin", "hiring"],
    }
    body = f"""# {name}

## What they are
{r.get('kind') or 'company'} | {r.get('industry') or 'industry not stated'} | {r.get('size') or 'size not stated'} | {r.get('hq') or r.get('location')}

## What is verified
- confirmed: open LinkedIn role "{r['title']}", posted {r.get('posted')}, {r.get('location')}
- confirmed: job link {r.get('link')}
- confirmed: proof from the posting: "{r.get('evidence') or 'no AI sentence found, read the posting'}"
- open roles on file for this company: {r.get('open_roles', 1)}
- applicants: {r.get('applicants') or 'not shown'}
- why it scored {r['score']}: {r.get('why')}

Only the confirmed lines may be quoted in a message. Never guess their product.

## The angle
AI ad output while they hire: the role shows budget and need, Monarc fills the gap
this week at a flat price per video. Agencies get the white-label version.
"""
    return fm, body, drafts


def post_note(r):
    first = first_name(r.get("author"))
    need = r.get("need") or ""
    need_line = f"looking for {need}" if need else "about the AI video work you need"
    need_short = (need or "the AI video work")[:40]
    f = dict(first=first, need_line=need_line, need_short=need_short, optout=OPTOUT_INTL)
    f["opener"] = pick(POST_OPEN, r.get("author") or r.get("url", "")).format(**f)
    drafts = {"email": POST_EMAIL.format(**f), "linkedin": POST_LINKEDIN.format(**f)}
    name = r.get("author") or "LinkedIn post"
    excerpt = re.sub(r"\s+", " ", r.get("text", ""))[:400]
    fm = {
        "status": "analyzed", "brand": name, "greeting_name": first,
        "lead_type": "post", "market": "International - LinkedIn",
        "email": r.get("email", ""), "linkedin": r.get("author_url", ""),
        "signal_url": r.get("url", ""), "signal": f"post asking for {need or 'AI video'}",
        "signal_date": r.get("date", ""), "followers": r.get("followers", ""),
        "tier": tier_label(r["tier"]), "quality": r["score"],
        "first_channel": "Email" if r.get("email") else "LinkedIn reply",
        "sourced": L.TODAY.isoformat(), "source": "linkedin posts", "variant": VARIANT,
        "tags": ["lead", "linkedin", "post"],
    }
    body = f"""# {name}

## What they asked for
> {excerpt}

## What is verified
- confirmed: public LinkedIn post dated {r.get('date')}: {r.get('url')}
- confirmed: they asked for: {need or 'see the post text above'}
- email written in the post: {r.get('email') or 'none'}
- why it scored {r['score']}: {r.get('why')}

Reply on the post or by DM first. If an email is in the post, they asked to be
emailed, so email is fine. Read the full post before sending: the filter
catches most sellers posing as buyers, not all.

## The angle
They asked in public. Answer fast, match their words, offer samples close to the brief.
"""
    return fm, body, drafts


def render(body, drafts):
    out = body.rstrip() + "\n\n## Draft messages\n> Written by li_vault.py from the confirmed lines above.\n"
    if "linkedin" in drafts:
        out += "\n### LinkedIn\n" + drafts["linkedin"] + "\n"
    for chan, title in (("email", "Email"), ("instagram", "Instagram"), ("whatsapp", "WhatsApp")):
        if chan in drafts:
            out += f"\n### {title}\n{drafts[chan]}\n"
    return out + ("\n---\n## To advance this lead\nWrite the approval tag on its own "
                  "line below.\n")


def niche_fm(r):
    country = r.get("hq", "").split(",")[-1].strip() if r.get("hq") else ""
    fm = {
        "status": "analyzed", "brand": r["name"], "greeting_name": greet_name(r["name"]),
        "lead_type": "agency" if r["kind"] == "agency" else "brand",
        "market": market_label(country if r.get("market") != "india" else "India"),
        "country": country, "city": r.get("hq", "").split(",")[0].strip(),
        "email": r.get("email", ""), "instagram": r.get("instagram", ""),
        "phone": r.get("phone", ""), "website": r.get("website", ""),
        "linkedin": r.get("url", ""), "industry": r.get("industry", ""),
        "company_size": r.get("size", ""), "services": r.get("industry", ""),
        "tier": tier_label(r["tier"]), "quality": r["score"],
        "first_channel": "Email" if r.get("email") else "Instagram" if r.get("instagram") else "LinkedIn",
        "sourced": L.TODAY.isoformat(), "source": "linkedin niche",
        "tags": ["lead", "linkedin", r["kind"]],
    }
    body = f"""# {r['name']}

## What they are
{r.get('about') or r.get('industry')}

## What is verified
- confirmed: LinkedIn company page {r.get('url')}
- confirmed: industry {r.get('industry')}, size {r.get('size')}, HQ {r.get('hq')}
- last LinkedIn post: {r.get('last_post') or 'not seen'}
- why it scored {r['score']}: {r.get('why')}

## The angle
{'White-label AI ad production: their name on the work, Monarc behind it.' if r['kind'] == 'agency' else 'AI product ads and AI UGC for their real products. Lead with a sample made for one product.'}

## Draft messages
> Written by draft_all.py from the facts above.

### Email

### Instagram

### WhatsApp

---
## To advance this lead
Write the approval tag on its own line at the bottom.
"""
    return fm, body


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--tiers", default="")
    a = ap.parse_args()
    L.lock()
    cfg, s = L.config(), L.load_state()
    tiers = ({t.strip().upper() for t in a.tiers.split(",")} if a.tiers
             else set(cfg["settings"]["vault_tiers"]))
    posts, hiring, agencies, brands, _ = LB.build_rows(cfg, s)
    L.save_state(s)
    idx = existing_index()
    added, filled, blocked, unreachable = {}, 0, 0, 0

    def place(kind, fm, body, keys):
        nonlocal filled
        hit = next((idx[k] for k in keys if k in idx), None)
        if hit:
            path, _ = hit
            old, old_body = V.read_note(path)
            gaps = {k: v for k, v in fm.items() if v not in ("", None) and
                    k not in ("status", "tags", "source", "sourced", "variant") and
                    old.get(k) in (None, "", {}, [])}
            if gaps and not a.dry_run:
                old.update(gaps)
                path.write_text("---\n" + V._fm_dump(old) + "\n---\n\n" +
                                old_body.strip() + "\n", encoding="utf-8")
            filled += bool(gaps)
            return
        added[kind] = added.get(kind, 0) + 1
        if not a.dry_run:
            fm = {k: v for k, v in fm.items() if v not in ("", None)}
            p = V.write_note("analyzed", fm["brand"], fm, body)
            for k in keys:
                idx.setdefault(k, (p, "analyzed"))
            # remembered outside the vault too, so deleting the note later
            # cannot bring this lead back as new
            D.remember_imported(D.identities(fm["brand"], fm.get("email"), fm.get("website"),
                                             [fm.get("linkedin"), fm.get("signal_url")]))

    def keys_for(fm):
        ks = ["slug:" + V.slug(fm["brand"])]
        e = str(fm.get("email") or "").lower()
        if "@" in e and not e.split("@")[1].startswith(LB.FREE):
            ks.append("email:" + e)
        if fm.get("website"):
            ks.append("dom:" + fm["website"])
        return ks

    for kind, rows, maker in (("post", posts, post_note), ("hiring", hiring, hiring_note)):
        for r in rows:
            if r["tier"] not in tiers:
                continue
            # A hiring lead with no email, Instagram or phone has nowhere to
            # send a draft. It stays in the workbook (LinkedIn link included)
            # instead of burying the reachable ones in Obsidian.
            if kind == "hiring" and not (r.get("email") or r.get("instagram") or r.get("phone")):
                unreachable += 1
                continue
            fm, body, drafts = maker(r)
            bad = check({k: v for k, v in drafts.items()}, (fm["greeting_name"], fm["brand"]))
            bad += [f"{ch}: {words(t)} words, over the {CAPS[ch]} cap"
                    for ch, t in drafts.items() if words(t) > CAPS[ch]]
            if bad:
                print(f"  BLOCKED by lint {fm['brand']}: {bad[:2]}")
                blocked += 1
                continue
            place(kind, fm, render(body, drafts), keys_for(fm))
    for kind, rows in (("agency", agencies), ("brand", brands)):
        for r in rows:
            if r["tier"] in tiers and (r.get("email") or r.get("instagram")):
                fm, body = niche_fm(r)
                place(kind, fm, body, keys_for(fm))

    verb = "would be" if a.dry_run else "were"
    print(f"\n  {sum(added.values())} LinkedIn leads {verb} added to vault/02_Analyzed {added}")
    print(f"  {filled} existing notes {verb} given missing fields (nothing overwritten)")
    if blocked:
        print(f"  {blocked} held back because a draft failed the voice lint")
    if unreachable:
        print(f"  {unreachable} hiring leads with no email/Instagram/phone kept in the "
              f"workbook only (reach them through the LinkedIn link there)")
    if added.get("agency") or added.get("brand"):
        print("  Agency and brand notes get drafts from draft_all.py next.")


if __name__ == "__main__":
    main()
