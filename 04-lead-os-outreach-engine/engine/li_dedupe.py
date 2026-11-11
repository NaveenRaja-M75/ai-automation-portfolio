#!/usr/bin/env python3
"""
LinkedIn engine, de-duplication: one lead, one row, ever.

A lead can already exist in six places before LinkedIn finds it again:

    1. the vault, any stage              (a note with its name, email, website)
    2. _state/seen.json                  (the ledger: delivered or contacted)
    3. 00_Control/DO_NOT_CONTACT.md      (asked us not to)
    4. _state/foreign_contacted.json     (Gmail sent history, job-post replies)
    5. _state/sent_log.json              (dispatch log)
    6. _state/foreign_raw.json           (the foreign workbook's raw list)

1-5 mean "do not show this again": the row goes to Skipped with the reason.
6 is only a list, nobody was contacted, so the row stays and is tagged.

Matching is on every identity a business has, not just its name, because the
same company arrives as "Puffy", "Puffy Mattress", "puffy.com" and
"linkedin.com/company/puffy-mattress". Names are compared after stripping
legal suffixes (Inc, LLC, Pvt Ltd, Group...) so "Acme Media Pvt. Ltd." and
"Acme Media" collide as they should.
"""
import re
import json
from pathlib import Path

import li_lib as L
import vault as V

SUFFIX = re.compile(
    r"\b(inc|llc|l\.l\.c|ltd|limited|pvt|private|plc|gmbh|co|corp|corporation|"
    r"company|group|holdings|fze|fzco|fz llc|llp|pty|sa|srl|bv|ag|the)\b\.?", re.I)
FREE = ("gmail.", "yahoo.", "hotmail.", "outlook.", "icloud.", "live.", "proton")
IGNORE_TEAM = False    # li_team --all sets this to re-issue leads already handed out


# The ledger stores names already squashed ("acmemediapvtltd"), so word-based
# suffix stripping cannot reach them. These endings are peeled off the
# squashed form instead. Longest first, so "privatelimited" beats "limited".
SQUASHED_SUFFIX = ("privatelimited", "pvtltd", "pvtlimited", "limited", "private",
                   "pltd", "ltd", "llp", "llc", "inc", "corp", "corporation", "gmbh",
                   "group", "holdings", "company", "fzllc", "fzco", "fze", "pty", "plc")

# Hosts many unrelated businesses share. As an identity they made every
# company that lists a YouTube channel a "duplicate" of the first one that did.
SHARED_HOSTS = ("youtube.com", "youtu.be", "instagram.com", "facebook.com", "fb.com",
                "linktr.ee", "linkedin.com", "x.com", "twitter.com", "tiktok.com",
                "wa.me", "whatsapp.com", "bit.ly", "medium.com", "behance.net",
                "github.com", "notion.site", "beacons.ai", "sites.google.com",
                "google.com", "forms.gle", "calendly.com", "wixsite.com", "carrd.co",
                "linkin.bio", "taplink.cc", "about.me", "substack.com")


def key_name(name):
    n = SUFFIX.sub(" ", str(name or "").lower())
    return re.sub(r"[^a-z0-9]", "", n)


def unsquash(n):
    """'adbhuttalesprivatelimited' -> 'adbhuttales'."""
    changed = True
    while changed and len(n) > 4:
        changed = False
        for suf in SQUASHED_SUFFIX:
            if n.endswith(suf) and len(n) - len(suf) >= 4:
                n, changed = n[: -len(suf)], True
                break
    return n


def key_dom(url):
    d = re.sub(r"^https?://", "", str(url or "").lower())
    d = re.sub(r"^www\.", "", d).split("/")[0].split("?")[0].split(":")[0]
    if "." not in d or any(d == h or d.endswith("." + h) for h in SHARED_HOSTS):
        return ""
    return d


def key_email(e):
    e = str(e or "").lower().strip()
    return e if "@" in e else ""


def email_dom(e):
    e = key_email(e)
    d = e.split("@")[1] if e else ""
    return "" if d.startswith(FREE) else d


def key_li(url):
    """A LinkedIn identity: company slug, person slug or post activity id."""
    u = str(url or "")
    m = re.search(r"(?:activity|ugcPost|share)[-:](\d{18,20})", u)
    if m:
        return "act:" + m.group(1)
    m = re.search(r"linkedin\.com/(company|in|showcase)/([^/?#]+)", u)
    return f"{m.group(1)}:{m.group(2).lower()}" if m else ""


def identities(name="", email="", site="", li=""):
    """Every key a business can be recognised by. Name is tried both ways:
    word-stripped ('Acme Media Pvt. Ltd.' -> acmemedia) and squashed like the
    ledger stores it (acmemediapvtltd), so neither side can miss the other."""
    keys = []
    for n in {key_name(name), V.norm(name), unsquash(V.norm(name))}:
        if len(n) > 2:
            keys.append("n:" + n)
    if key_email(email):
        keys.append("e:" + key_email(email))
    if email_dom(email):
        keys.append("d:" + email_dom(email))
    if key_dom(site):
        keys.append("d:" + key_dom(site))
    keys += [key_li(x) for x in (li if isinstance(li, (list, tuple)) else [li])]
    return [k for k in keys if k and len(k) > 3]


def remember_imported(keys):
    """Called by li_vault for every note it writes."""
    f = L.STATE / "li_imported.json"
    try:
        have = set(json.loads(f.read_text(encoding="utf-8")))
    except Exception:
        have = set()
    have |= set(keys)
    f.write_text(json.dumps(sorted(have), indent=0), encoding="utf-8")


class Known:
    """Everything already on file, indexed by every identity."""

    def __init__(self):
        self.stop = {}      # identity -> reason (never show again)
        self.tag = {}       # identity -> note   (show, but say where else it is)
        self._vault()
        self._ledger()
        self._contacted()
        self._foreign_list()

    def _add(self, table, reason, name="", email="", site="", li=""):
        for k in (("n:" + key_name(name)) if key_name(name) else "",
                  ("e:" + key_email(email)) if key_email(email) else "",
                  ("d:" + email_dom(email)) if email_dom(email) else "",
                  ("d:" + key_dom(site)) if key_dom(site) else "",
                  key_li(li)):
            if k and len(k) > 3:
                table.setdefault(k, reason)

    def _vault(self):
        for stage in V.STAGES:
            for p in V.notes_in(stage):
                fm, _ = V.read_note(p)
                why = f"already in vault ({p.parent.name})"
                self._add(self.stop, why, fm.get("brand") or p.stem, fm.get("email"),
                          fm.get("website"), fm.get("linkedin"))
                if fm.get("signal_url"):
                    self._add(self.stop, why, li=fm.get("signal_url"))

    def _ledger(self):
        for n in V.load_seen():
            for k in (n, unsquash(n)):
                if len(k) > 2:
                    self.stop.setdefault("n:" + k, "already pitched (ledger)")
        for n in V.blocklist():
            self.stop["n:" + n] = "on DO_NOT_CONTACT"
            self.stop["n:" + unsquash(n)] = "on DO_NOT_CONTACT"
        # Everything li_vault ever imported. If a note is DELETED instead of
        # moved to 99_Rejected, the vault forgets it, but this file does not,
        # so a deleted lead never comes back as new.
        try:
            imp = json.loads((L.STATE / "li_imported.json").read_text(encoding="utf-8"))
        except Exception:
            imp = []
        for k in imp:
            self.stop.setdefault(k, "imported to the vault before")
        # Handed to the team in a lead sheet: theirs now, never offered again.
        if not IGNORE_TEAM:
            try:
                team = json.loads((L.STATE / "li_team_exported.json").read_text(encoding="utf-8"))
            except Exception:
                team = {}
            for k, day in team.items():
                self.stop.setdefault(k, f"given to the team ({day})")

    def _contacted(self):
        f = L.STATE / "foreign_contacted.json"
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            d = {}
        for group in ("job_posts", "brand_emails"):
            for r in d.get(group, []) or []:
                if isinstance(r, dict):
                    self._add(self.stop, "already emailed (Gmail history)",
                              r.get("who") or r.get("brand") or r.get("name") or "",
                              r.get("email"),
                              r.get("website") or r.get("site") or "")
        try:
            log = json.loads((L.STATE / "sent_log.json").read_text(encoding="utf-8"))
        except Exception:
            log = {}
        for k, v in (log.items() if isinstance(log, dict) else []):
            self._add(self.stop, "already sent (dispatch log)", k,
                      (v or {}).get("email", "") if isinstance(v, dict) else "")

    def _foreign_list(self):
        try:
            raw = json.loads((L.STATE / "foreign_raw.json").read_text(encoding="utf-8"))
        except Exception:
            raw = []
        for r in raw if isinstance(raw, list) else []:
            self._add(self.tag, "also in foreign workbook", r.get("name"),
                      r.get("email"), r.get("website"))

    def check(self, name="", email="", site="", li=""):
        """(stop_reason, tag) for a candidate. Blank strings when clean."""
        keys = identities(name, email, site, li)
        stop = next((self.stop[k] for k in keys if k in self.stop), "")
        if not stop and V.blocked(name):
            stop = "on DO_NOT_CONTACT"
        tag = next((self.tag[k] for k in keys if k in self.tag), "")
        return stop, tag


class Batch:
    """De-duplicates within one build, across all four sheets.

    Rows must be offered strongest-signal first (posts, then hiring, then
    niche), so when two rows are the same business the hotter one survives
    and the colder one is reported as its duplicate.
    """

    def __init__(self):
        self.owner = {}

    def claim(self, label, name="", email="", site="", li=""):
        keys = identities(name, email, site, li)
        hit = next((self.owner[k] for k in keys if k in self.owner), "")
        if hit:
            return f"duplicate of {hit}"
        for k in keys:
            self.owner[k] = label
        return ""
