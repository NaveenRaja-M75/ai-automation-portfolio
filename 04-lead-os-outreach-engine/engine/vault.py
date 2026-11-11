#!/usr/bin/env python3
"""
Vault layer — the markdown files ARE the database.

No Supabase, no Airtable, no Postgres. A lead is a .md file with YAML
frontmatter, which means:
  - Obsidian renders it as a real interface for free
  - you can edit any field by hand, mid-pipeline, without a migration
  - git gives you history and rollback at no cost
  - Claude Code reads and writes it natively, no ORM, no schema drift

The status field in frontmatter is the whole state machine. A note moves
folders as it advances, so the folder you are looking at always tells you
exactly what stage the lead is in.
"""
import os
import re
import json
import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VAULT = ROOT / "vault"
STATE = ROOT / "_state"
SEEN = STATE / "seen.json"

STAGES = {
    "sourced":  VAULT / "01_Sourced",
    "analyzed": VAULT / "02_Analyzed",
    "approved": VAULT / "03_Approved",
    "sent":     VAULT / "04_Sent",
    "rejected": VAULT / "99_Rejected",
}
ASSETS = VAULT / "_assets"
CONTROL = VAULT / "00_Control"


def slug(name):
    s = re.sub(r"[^A-Za-z0-9]+", "-", str(name)).strip("-").lower()
    return (s or "lead")[:60]


def norm(name):
    return re.sub(r"[^a-z0-9]", "", str(name).lower())


# ------------------------------------------------------------------ ledger
def load_seen():
    """Names already delivered or contacted. Never pitch the same brand twice."""
    try:
        return set(json.loads(SEEN.read_text(encoding="utf-8")))
    except Exception:
        return set()


def remember(names):
    s = load_seen()
    s |= {norm(n) for n in names if norm(n)}
    STATE.mkdir(parents=True, exist_ok=True)
    SEEN.write_text(json.dumps(sorted(s), indent=0), encoding="utf-8")
    return len(s)


def is_new(name):
    return norm(name) not in load_seen()


def blocklist():
    """Names that must NEVER be contacted, however they are rediscovered.

    Deliberately separate from the ledger. The ledger means "already pitched";
    this means "asked us not to". Collapsing the two would eventually re-pitch
    somebody who opted out, because a fresh search does not know the history.
    """
    txt = control("DO_NOT_CONTACT.md")
    out = set()
    started = False
    for line in txt.splitlines():
        s = line.strip()
        if s.startswith("## List"):
            started = True
            continue
        if not started or not s or s.startswith(("<!--", "#", "-")):
            continue
        out.add(norm(s))
    return out


def blocked(name):
    n = norm(name)
    if not n:
        return False
    bl = blocklist()
    return n in bl or any(b and b in n for b in bl)


# ----------------------------------------------------------------- notes
def _fm_dump(d, indent=0):
    """Minimal YAML writer. Avoids a pyyaml dependency for a flat dict."""
    out = []
    pad = " " * indent
    for k, v in d.items():
        if isinstance(v, dict):
            out.append(f"{pad}{k}:")
            out.append(_fm_dump(v, indent + 2))
        elif isinstance(v, (list, tuple)):
            out.append(f"{pad}{k}:")
            for item in v:
                out.append(f"{pad}  - {item}")
        elif v is None or v == "":
            out.append(f"{pad}{k}:")
        elif isinstance(v, str) and (":" in v or v.startswith(("#", "@", "+"))):
            out.append(f'{pad}{k}: "{v}"')
        else:
            out.append(f"{pad}{k}: {v}")
    return "\n".join(out)


def _fm_parse(text):
    """Read frontmatter without pyyaml. Flat keys plus one nesting level."""
    if not text.startswith("---"):
        return {}, text
    end = text.find("\n---", 3)
    if end == -1:
        return {}, text
    raw, body = text[3:end], text[end + 4:]
    data, parent = {}, None
    for line in raw.splitlines():
        if not line.strip() or line.strip().startswith("#"):
            continue
        indent = len(line) - len(line.lstrip())
        line_s = line.strip()
        if line_s.startswith("- ") and parent:
            # "tags:" opens as an empty mapping, and setdefault never replaced
            # it with a list, so every "- item" under it was thrown away and
            # each save wiped the note's tags. An empty mapping becomes a list
            # the moment its first item arrives.
            if data.get(parent) in ({}, None):
                data[parent] = []
            if isinstance(data[parent], list):
                data[parent].append(line_s[2:].strip())
            continue
        if ":" not in line_s:
            continue
        k, _, v = line_s.partition(":")
        k, v = k.strip(), v.strip().strip('"')
        if indent >= 2 and parent and isinstance(data.get(parent), dict):
            data[parent][k] = v
        elif v == "":
            data[k] = {}
            parent = k
        else:
            data[k] = v
            parent = None
    # A key with no value and no children is blank, not an empty mapping. As
    # {} it printed into a greeting as "clients in {}".
    for k, v in list(data.items()):
        if v == {}:
            data[k] = ""
    return data, body.lstrip("\n")


def write_note(stage, name, frontmatter, body):
    folder = STAGES[stage]
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{slug(name)}.md"
    content = "---\n" + _fm_dump(frontmatter) + "\n---\n\n" + body.strip() + "\n"
    path.write_text(content, encoding="utf-8")
    return path


def read_note(path):
    return _fm_parse(Path(path).read_text(encoding="utf-8"))


def notes_in(stage):
    folder = STAGES[stage]
    if not folder.exists():
        return []
    return sorted(folder.glob("*.md"))


def move_note(path, to_stage, updates=None):
    """Advance a lead. Frontmatter status and folder always stay in sync.

    Order matters here. Callers routinely pass the note's whole frontmatter
    dict as `updates` (enrich does exactly that after adding contacts), and
    that dict still carries the OLD status. Applying updates after setting
    the status silently reverted it, leaving notes filed in 02_Analyzed while
    claiming to be `sourced`. The folder and the field then disagree, and
    every status-driven query reads the wrong stage. So updates are applied
    first, and the stage is stamped last where nothing can overwrite it.
    """
    fm, body = read_note(path)
    if updates:
        fm.update(updates)
    fm["status"] = to_stage
    fm["updated"] = datetime.date.today().isoformat()
    dest = STAGES[to_stage]
    dest.mkdir(parents=True, exist_ok=True)
    new_path = dest / Path(path).name
    new_path.write_text("---\n" + _fm_dump(fm) + "\n---\n\n" + body.strip() + "\n",
                        encoding="utf-8")
    if Path(path) != new_path:
        os.remove(path)
    return new_path


CODE_SPAN = re.compile(r"`[^`\n]*`")
APPROVE_TAG = re.compile(r"(?<![\w`])#approved(?![\w`])")
REJECT_TAG = re.compile(r"(?<![\w`])#rejected(?![\w`])")


def _tags_only(text):
    """Strip inline code spans before looking for tags.

    The note template has to TELL you to add #approved, and that instruction
    is itself text in the file. Matching the raw string marked every lead as
    approved the moment it was created, which would have pushed unreviewed
    leads straight to dispatch. Tags written inside backticks are
    documentation; only a bare tag is a decision.
    """
    return CODE_SPAN.sub(" ", text)


def approved_notes():
    """A lead is approved only when YOU write a bare #approved tag. Nothing
    else promotes a lead, and nothing is prepared for sending without it."""
    out = []
    for stage in ("sourced", "analyzed", "approved"):
        for p in notes_in(stage):
            txt = _tags_only(p.read_text(encoding="utf-8"))
            if APPROVE_TAG.search(txt) and not REJECT_TAG.search(txt):
                out.append(p)
    return out


def control(filename):
    p = CONTROL / filename
    return p.read_text(encoding="utf-8") if p.exists() else ""


def campaign():
    """Parse CAMPAIGN.md into the config every stage reads.

    Packs exist because the niche IS the yield lever. A Chennai jewellery run
    kept 1 lead from 45 verified, with 32 of them already running video: metro
    jewellery brands have editors already. Switching pack is how you move to a
    market that still has the gap, and it must never require a code change.
    """
    txt = control("CAMPAIGN.md")
    cfg = {"niches": [], "markets": {}, "packs": {}, "pack": "", "market": "",
           "min_active_ads": 2, "min_days_running": 14, "max_video_ads": 0,
           "min_distinct_creatives": 6, "mode": "gap"}

    m = re.search(r"^active_pack:\s*(\S+)", txt, re.M)
    cfg["pack"] = m.group(1) if m else ""
    m = re.search(r"^active_market:\s*(\S+)", txt, re.M)
    cfg["market"] = m.group(1) if m else "india"

    blocks = re.findall(r"```yaml(.*?)```", txt, re.S)
    raw = "\n".join(blocks)

    # Only parse packs from inside the `packs:` block. Without this scope,
    # `never_target` and `offer` were being read as selectable packs, which
    # would let a run target the exclusion list by mistake.
    packs_block = ""
    pm = re.search(r"^packs:\s*\n(.*?)(?=^\w|\Z)", raw, re.M | re.S)
    if pm:
        packs_block = pm.group(1)

    for m2 in re.finditer(r"^\s{2,4}(\w+):\s*\n((?:\s*(?:#.*)?\n|\s+-\s*.+\n)+)",
                          packs_block, re.M):
        name, body = m2.group(1), m2.group(2)
        items = [re.sub(r"\s+#.*$", "", l.strip("- ").strip())
                 for l in body.splitlines() if l.strip().startswith("-")]
        items = [i for i in items if i]
        if items:
            cfg["packs"][name] = items

    for name, cc in re.findall(r"(\w+):\s*\{\s*country:\s*(\w+)", raw):
        cfg["markets"][name] = cc

    mm = re.search(r"^\s*mode:\s*(\w+)", raw, re.M)
    if mm:
        cfg["mode"] = mm.group(1)
    for key in ("min_active_ads", "min_days_running", "max_video_ads",
                "min_distinct_creatives"):
        mm = re.search(key + r":\s*(\d+)", raw)
        if mm:
            cfg[key] = int(mm.group(1))

    cfg["niches"] = cfg["packs"].get(cfg["pack"], [])
    if not cfg["niches"] and cfg["packs"]:
        cfg["niches"] = list(cfg["packs"].values())[0]
    return cfg


if __name__ == "__main__":
    print("vault root :", VAULT)
    print("ledger     :", len(load_seen()), "names")
    c = campaign()
    print("niches     :", ", ".join(c["niches"][:8]))
    print("markets    :", c["markets"])
    print("thresholds :", {k: v for k, v in c.items()
                           if k.startswith(("min_", "max_"))})
    for s in STAGES:
        print(f"  {s:<9} {len(notes_in(s)):>4} notes")
