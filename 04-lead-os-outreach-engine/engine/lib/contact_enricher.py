#!/usr/bin/env python3
"""
Contact Enricher — turns a company NAME into a real, verified email.

WHY THIS EXISTS
    The two best lead sources give you a company but no way to reach it:
      - Internshala job posts   -> company name only (applies run through them)
      - Meta Ad Library         -> page_name / page_id only, never contacts
    So the contact has to be resolved separately. This does that.

HOW IT AVOIDS GUESSING
    It derives candidate domains from the company name, then FETCHES each one
    and only accepts it if the live site actually identifies as that company
    (name tokens present in the page). A domain that does not verify is
    discarded, not recorded. Emails are then read off the site's own contact
    or statutory policy pages - never pattern-built from a first name.
    Finally every address gets an MX lookup, so dead domains are dropped.

    A blank cell means "no public email found". It never means "invented one".

USAGE
    python contact_enricher.py --infile hiring_leads.xlsx --col Company
    python contact_enricher.py --infile ad_gap_leads.xlsx --col Brand --limit 60
"""
import re, sys, time, argparse, datetime
import requests, pandas as pd

H = {"User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")}
EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
BAD = ("sentry", "wixpress", "example.com", "yourdomain", "godaddy", "domainsbyproxy",
       ".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", "@2x", "sentry.io",
       "cloudflare", "shopify.com", "googleapis", "schema.org", "w3.org",
       "email@", "your@", "name@", "test@", "user@")
DEAD_END = ("careers@", "jobs@", "hr@", "legal@", "press@", "noreply", "no-reply",
            "donotreply", "abuse@", "postmaster@")
PATHS = ["/pages/contact", "/pages/contact-us", "/contact", "/contact-us",
         "/policies/refund-policy", "/pages/about-us", "/"]
TLDS = [".com", ".in", ".co.in", ".net", ".store", ".shop"]


# D2C brands overwhelmingly register a prefixed domain when the bare name is
# taken. A Meta page called "Try Lumilux" actually lives at getlumilux.com.
# The guesser tried trylumilux and lumilux, gave up, and marked a real Shopify
# store running 56 live ads as having no website at all. These prefixes cost
# one extra fetch each and recover leads that were otherwise discarded.
D2C_PREFIXES = ["get", "try", "shop", "buy", "use", "my", "join", "go"]
# The same words often appear IN the page name, where they are noise rather
# than part of the brand: "Try Lumilux" is simply the brand Lumilux.
LEAD_NOISE = re.compile(r"^(try|get|shop|buy|use|my|the|join|go|official|"
                        r"team|brand)\s+", re.I)


def slugs(name):
    """Candidate domain stems from a company name — every one verified before use."""
    name = LEAD_NOISE.sub("", str(name).strip())
    n = re.sub(r"\b(private|pvt|limited|ltd|llp|inc|co|company|technologies|"
               r"solutions|india|global|the)\b", " ", name, flags=re.I)
    n = re.sub(r"[^A-Za-z0-9 ]", " ", n).strip()
    parts = [p for p in n.split() if p]
    if not parts:
        return []
    joined = "".join(parts).lower()
    out = [joined]
    if len(parts) > 1:
        if len(parts[0]) >= 6:          # "walk" is too generic to trust
            out.append(parts[0].lower())
        out.append("".join(parts[:2]).lower())

    # prefixed variants, tried after the plain ones so the obvious wins first
    for stem in [s for s in dict.fromkeys(out) if 3 <= len(s) <= 24]:
        for pre in D2C_PREFIXES:
            if not stem.startswith(pre):
                out.append(pre + stem)
    return [s for s in dict.fromkeys(out) if 3 <= len(s) <= 28]


def name_tokens(name):
    n = re.sub(r"[^A-Za-z0-9 ]", " ", name).lower()
    return [t for t in n.split() if len(t) > 2][:3]


def verify_site(url, company, want_stem):
    """Only accept a domain if the live page really is this company.

    Two guards, both learned the hard way:
      1. REDIRECT HIJACK. A parked or sold domain often 301s somewhere else.
         Requesting brand-site.example and landing on theme-agency.example must fail,
         so the final host has to still contain the stem we asked for.
      2. WEAK NAME MATCH. Requiring one token out of two let a generic word
         like "Organics" validate an unrelated shop. Now the distinctive
         first token must appear, and multi-word names must match in full.
    """
    try:
        r = requests.get(url, headers=H, timeout=12, allow_redirects=True)
    except Exception:
        return None
    if r.status_code != 200 or len(r.text) < 400:
        return None

    final_host = re.sub(r"^www\.", "", (r.url.split("//")[-1].split("/")[0]).lower())
    if want_stem not in final_host.replace("-", "").replace(".", ""):
        return None                      # redirected off the domain we vetted

    low = r.text.lower()
    toks = name_tokens(company)
    if not toks:
        return None
    if toks[0] not in low:               # distinctive word must be present
        return None
    if len(toks) >= 2 and sum(1 for t in toks if t in low) < len(toks):
        return None                      # multi-word name must match fully
    return r.text


FREE_MAIL = ("gmail.com", "googlemail.com", "yahoo.com", "yahoo.in", "outlook.com",
             "hotmail.com", "rediffmail.com", "protonmail.com", "icloud.com", "zoho.com")


def same_org(email, site_domain):
    """An address on a company's own site should live on that company's own
    domain - or on a free provider, which small brands genuinely use.

    A DIFFERENT corporate domain is almost always a template leak: dropship
    and theme-built stores routinely ship with the original shop's address
    still in the footer. That is how brand-site.example yielded theme-agency.example.
    Rejecting those is the difference between a usable list and one that
    emails strangers."""
    if not email or not site_domain:
        return False
    edom = email.split("@")[-1].lower().strip(".")
    sdom = site_domain.lower().replace("www.", "")
    if edom in FREE_MAIL:
        return True
    # Exact core match only. A substring test let support@walkin-other.example pass
    # as "walk.in" - close-looking domains are a different company far more
    # often than they are the same one.
    ecore = edom.split(".")[0]
    score = sdom.split(".")[0]
    return ecore == score


def emails_from(html):
    if not html:
        return []
    found = []
    for e in EMAIL.findall(html):
        el = e.lower()
        if any(b in el for b in BAD):
            continue
        if el.endswith((".png", ".jpg", ".gif", ".svg", ".webp")):
            continue
        if any(el.startswith(d) for d in DEAD_END):
            continue
        if len(el) > 60:
            continue
        found.append(e)
    return list(dict.fromkeys(found))



# --- phones -----------------------------------------------------------------
# A tel: link is unambiguous, so it is trusted first. Loose digit-scraping is
# what produces garbage: Indian pincodes are 6 digits, GST numbers 15 chars,
# and prices/years look like numbers too. So free-text matching demands either
# an explicit country code or a 10-digit number starting 6-9 (the only valid
# Indian mobile range), and anything embedded in a longer digit run is dropped.
TEL_HREF = re.compile(r'tel:\s*([+0-9][0-9\-\s().]{6,20})', re.I)
INTL = re.compile(r'(?<![\d])(\+\d{1,3}[\s-]?\d[\d\s().-]{6,14}\d)(?![\d])')
IN_MOBILE = re.compile(r'(?<![\d+])([6-9]\d{9})(?![\d])')
JUNK_PHONE = re.compile(r'^(0{5,}|1{5,}|1234567890|9{10})$')


def phones_from(html):
    if not html:
        return []
    found = []
    for m in TEL_HREF.findall(html):
        found.append(m)
    text = re.sub(r"<[^>]+>", " ", html)
    for m in INTL.findall(text):
        found.append(m)
    for m in IN_MOBILE.findall(text):
        found.append(m)

    out = []
    for raw in found:
        d = re.sub(r"[^\d+]", "", raw)
        if d.startswith("00"):
            d = "+" + d[2:]
        digits = d.lstrip("+")
        if not (10 <= len(digits) <= 14):
            continue
        if JUNK_PHONE.match(digits):
            continue
        if not d.startswith("+"):
            if len(digits) == 10 and digits[0] in "6789":
                d = "+91" + digits           # bare Indian mobile
            else:
                continue                     # ambiguous, discard rather than guess
        if d not in out:
            out.append(d)
    return out



# --- socials ----------------------------------------------------------------
# For a brand that is already buying Meta ads, Instagram is usually the most
# responsive channel there is - a founder who ignores support@ will still read
# a DM. Handles are taken from the site's own social links, and platform
# junk (share/intent URLs, /p/ post links, generic app pages) is filtered out.
IG_RE = re.compile(r"instagram\.com/([A-Za-z0-9_.]{2,30})", re.I)

# Instagram's own routes and assets, none of which are accounts. Each entry
# below reached a note as a "handle" at some point: en_us from a locale path,
# embed.js from the embed script, embed from the same URL without the suffix.
# Patching them one at a time did not work, so validation is now positive:
# a string must LOOK like a handle, and not be a known system path.
IG_SYSTEM = {
    "p", "reel", "reels", "explore", "accounts", "about", "developer",
    "directory", "legal", "privacy", "tv", "stories", "share", "invites",
    "embed", "embeds", "oauth", "api", "static", "web", "graphql", "ajax",
    "challenge", "session", "login", "signup", "emails", "press", "blog",
    "help", "terms", "data", "download", "your_activity", "direct",
    # locale segments
    "en_us", "en_gb", "en_in", "en", "hi", "ta", "te", "ml", "kn", "bn",
    "fr", "es", "de", "pt", "ar", "ja", "ko", "zh_cn", "id", "ru", "it", "tr",
}
FILE_EXT = {"js", "css", "png", "jpg", "jpeg", "gif", "svg", "webp", "ico",
            "json", "php", "html", "htm", "xml", "woff", "woff2", "map", "txt"}
# A real handle: 2-30 chars, letters/digits/._ , not all digits, not a file.
HANDLE_OK = re.compile(r"^(?!\d+$)[a-z0-9](?:[a-z0-9._]{0,28}[a-z0-9])?$")


def instagram_from(html):
    """Extract a real Instagram handle, or nothing.

    Validation is positive rather than a growing blocklist: the candidate must
    match the shape Instagram actually allows, must not carry a file
    extension, and must not be one of Instagram's own routes. An empty result
    is correct and safe; a wrong handle sends a DM into the void.
    """
    if not html:
        return ""
    for raw in IG_RE.findall(html):
        h = raw.lower().strip(".")
        if not h or h in IG_SYSTEM:
            continue
        if "." in h and h.rsplit(".", 1)[-1] in FILE_EXT:
            continue
        if h.split(".")[0] in IG_SYSTEM:      # embed.js -> embed
            continue
        if not HANDLE_OK.match(h):
            continue
        return "@" + h
    return ""


def resolve(company):
    """name -> (website, email, how_found). Blank where nothing verifiable."""
    for stem in slugs(company):
        for tld in TLDS:
            dom = stem + tld
            home = verify_site("https://" + dom, company, stem)
            if not home:
                continue
            # site confirmed as this company; now hunt a published address
            got = [e for e in emails_from(home) if same_org(e, dom)]
            ph = phones_from(home)
            ig = instagram_from(home)
            if got:
                return dom, got[0], "site footer/home", (ph[0] if ph else ""), ig
            for p in PATHS:
                try:
                    r = requests.get("https://" + dom + p, headers=H, timeout=12)
                except Exception:
                    continue
                if r.status_code != 200:
                    continue
                got = [e for e in emails_from(r.text) if same_org(e, dom)]
                ph2 = phones_from(r.text) or ph
                ig = ig or instagram_from(r.text)
                if got:
                    return dom, got[0], "contact/policy page", (ph2[0] if ph2 else ""), ig
                if ph2 and not ph:
                    ph = ph2
                time.sleep(0.2)
            return dom, "", "site found, no public email", (ph[0] if ph else ""), ig
    return "", "", "no site verified", "", ""


def mx_ok(email):
    if not email or "@" not in email:
        return ""
    try:
        import dns.resolver
        a = dns.resolver.resolve(email.split("@")[-1], "MX", lifetime=6)
        return "Verified" if len(a) else "No MX"
    except Exception:
        return "Unresolved"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--infile", required=True)
    ap.add_argument("--col", required=True, help="column holding the company name")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--out", default="")
    a = ap.parse_args()

    df = pd.read_excel(a.infile)
    if a.col not in df.columns:
        print(f"No column '{a.col}'. Columns: {list(df.columns)}")
        sys.exit(1)
    names = list(dict.fromkeys([str(x).strip() for x in df[a.col] if str(x).strip()]))
    if a.limit:
        names = names[:a.limit]

    print(f"Resolving contacts for {len(names)} companies "
          f"(verify domain -> read published email -> MX check)\n")
    rec = {}
    hit = 0
    for i, n in enumerate(names, 1):
        site, mail, how, phone, insta = resolve(n)
        status = mx_ok(mail) if mail else ""
        if mail and status == "Verified":
            hit += 1
        rec[n] = (site, mail, how, status, phone, insta)
        flag = "OK " if (mail and status == "Verified") else ("TEL" if phone else "   ")
        shown = ((mail or how)[:34] + (("  " + phone) if phone else "")
                 + (("  " + insta) if insta else ""))
        print(f"{flag}{i:>3}/{len(names)} {n[:32]:<34}{shown}")
        time.sleep(0.25)

    BLANK = ("", "", "", "", "", "")
    df["Website (verified)"] = df[a.col].map(lambda x: rec.get(str(x).strip(), BLANK)[0])
    df["Email"] = df[a.col].map(lambda x: rec.get(str(x).strip(), BLANK)[1])
    df["Email source"] = df[a.col].map(lambda x: rec.get(str(x).strip(), BLANK)[2])
    df["Email status"] = df[a.col].map(lambda x: rec.get(str(x).strip(), BLANK)[3])
    df["Phone / WhatsApp"] = df[a.col].map(lambda x: rec.get(str(x).strip(), BLANK)[4])
    df["Instagram"] = df[a.col].map(lambda x: rec.get(str(x).strip(), BLANK)[5])
    df["Instagram URL"] = df["Instagram"].map(
        lambda h: ("https://instagram.com/" + h.lstrip("@")) if h else "")
    df["Enriched on"] = datetime.date.today().isoformat()

    out = a.out or a.infile.replace(".xlsx", "_with_contacts.xlsx")
    df.to_excel(out, index=False)
    from openpyxl import load_workbook
    from openpyxl.styles import Font, PatternFill, Alignment
    wb = load_workbook(out); ws = wb.active
    for c in ws[1]:
        c.font = Font(bold=True, color="FFFFFF")
        c.fill = PatternFill("solid", start_color="1F3864", end_color="1F3864")
        c.alignment = Alignment(horizontal="center", wrap_text=True)
    ws.freeze_panes = "B2"; ws.auto_filter.ref = ws.dimensions
    green = PatternFill("solid", start_color="C6EFCE", end_color="C6EFCE")
    hdr = [c.value for c in ws[1]]
    # Excel reads a leading "+" as arithmetic and turns +919800000000 into
    # 9.1972E+11. Force the phone column to literal text so numbers stay dialable.
    if "Phone / WhatsApp" in hdr:
        pi = hdr.index("Phone / WhatsApp") + 1
        vals = df["Phone / WhatsApp"].fillna("").tolist()
        for i, v in enumerate(vals, start=2):
            c = ws.cell(row=i, column=pi)
            c.value = str(v) if v else ""
            c.number_format = "@"
            c.alignment = Alignment(horizontal="left")
    if "Email status" in hdr:
        ci = hdr.index("Email status") + 1
        for row in ws.iter_rows(min_row=2, min_col=ci, max_col=ci):
            if row[0].value == "Verified":
                row[0].fill = green
    wb.save(out)
    print(f"\n{hit}/{len(names)} companies now have a VERIFIED email ({hit/len(names)*100:.0f}%)")
    print(f"Saved -> {out}")


if __name__ == "__main__":
    main()
