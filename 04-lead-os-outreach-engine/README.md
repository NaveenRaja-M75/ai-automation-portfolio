# Monarc Lead OS

A local, file-based lead engine for an AI film and ad studio. Finds brands that
are **already paying for ads and running no video**, puts a real contact route
on each one, and hands you a reviewed draft to send by hand.

No SaaS. No database. No extra API keys. Your vault is plain markdown, so
Obsidian is the interface and git is the history.

---

## The one idea

> Money already moving **+** no video **=** the exact gap Monarc fills.

You are never creating a budget, only redirecting one that already exists.
That is the easiest sale available, and every part of this system exists to
find that situation and prove it before you open your mouth.

---

## Run it

```bash
python run.py status                    # where every lead is
python run.py full --market chennai     # find + enrich
python run.py pitch                     # brief to paste into Claude Code
python run.py dispatch                  # prepare approved leads
```

Markets: `chennai`, `india`, `international`. All of them, and every niche,
are set in **`vault/00_Control/CAMPAIGN.md`**. Nothing is hard-coded, so
pivoting from jewellery to FMCG to SaaS is a text edit.

---

## How the work is divided

This is the design decision everything else follows from.

| Layer | Does | Costs |
|---|---|---|
| **Python** | fetch, verify, de-duplicate, parse contacts | nothing |
| **Claude Code** | look at their creative, write the concept and the message | already paid for |
| **You** | approve, then press send | four seconds |

Because **Claude Code is the reasoning layer**, this needs no Anthropic API
key, no Fal.ai key, no Replicate key, no Supabase, no vector database and no
agent framework. The scripts do only what does not require intelligence.

---

## The pipeline

```
CAMPAIGN.md ─ you aim it here
     │
     ▼
01_Sourced    source.py   Meta Ad Library, verified per advertiser
     │
     ▼
02_Analyzed   enrich.py   email, phone, instagram, messenger
     │
     ▼        Claude Code reads the note, looks at their ads,
     │        writes the concept and three drafts
     │
     ▼        YOU add #approved
03_Approved
     │
     ▼
04_Sent       dispatch.py opens the window, copies the text.
                          It never sends anything itself.
```

---

## What makes this different from the usual blueprint

Each of these is here because the obvious approach was tried and failed
against live data.

**1. Verification is per advertiser, never inferred.**
The common design runs one broad "all ads" search and one broad "video ads"
search and subtracts. Broad searches cap around 200 results, so the video set
arrives truncated and brands whose video ads fell outside it look like they
have none. Measured against real data that over-reported by about **3x**. One
brand flagged as the hottest lead on a list turned out to be running fourteen
video ads. This system spends one extra API call per advertiser to turn that
guess into a fact.

**2. Claims are graded before they are spoken.**
Every note carries an `evidence` block marking each fact `confirmed`,
`probable` or `unknown`. Only `confirmed` facts may appear in a message. Open
a pitch with *"you're running no video"* to a brand running fourteen and you
have proven you are a bot to the one person you needed to impress.

**3. No auto-generated video or image.**
The fashionable version of this idea renders a clip per lead and attaches it.
Two problems. It costs real money per lead, and an unsupervised render of a
product you scraped will often look wrong: wrong angle, wrong finish,
artefacts. For a studio whose entire product is taste, one bad auto-render
does more damage than ten missed leads. Instead the concept and shot list are
prepared, **you** generate it in your own tool, look at it, and attach it.
Human QC on anything carrying your name.

**4. Nothing sends itself.**
Automated Instagram and WhatsApp messaging gets accounts banned, and those
accounts are worth more than any list. `dispatch.py` opens the right window
and puts the text on your clipboard. You press send.

**5. Credentials never touch a third party.**
Tools that offer "free" Instagram, X or LinkedIn access want your session
cookie. That cookie is your logged-in account. Never hand it over. Everything
here uses public APIs and public pages only.

**6. The vault never stores a token.**
Meta returns ad snapshot URLs with your access token embedded in the query
string. Writing that into a note would leak a live credential into every
device your vault syncs to. Notes store the public Ad Library link instead,
and a scrubber strips any token that slips through.

**7. De-duplication is permanent.**
`_state/seen.json` holds every name ever delivered or contacted. It arrived
carrying **664** names from the previous system. Nothing is ever pitched twice.

---

## The vault

```
vault/
  00_Control/
    CAMPAIGN.md        aim the system here, this is the only file you edit
    VOICE.md           banned words, AI tells, the shape that works
    EVIDENCE_RULES.md  what may be claimed, and why it matters
  01_Sourced/          found, contacts not yet resolved
  02_Analyzed/         contacts added, waiting for your review
  03_Approved/         you tagged it
  04_Sent/             prepared and logged
  99_Rejected/
  _assets/
```

A lead is one markdown file. Its `status` in frontmatter and the folder it
sits in are always the same thing, so the folder you are looking at tells you
the stage without opening anything.

**In Obsidian:** open **`Monarc_Lead_OS/vault`** as the vault, not the parent
folder, so the Python code stays out of your sidebar. Install the **Dataview**
plugin, open `DASHBOARD.md`, and press `Ctrl+E` for Reading view. Dataview only
renders there.

Do not use the Kanban plugin for this. A Kanban board stores its own cards
inside one file and cannot read your lead notes from folders, so it will always
show an empty board no matter how many leads you have. Dataview queries the
notes themselves, so the dashboard reflects reality after every run.


---

## Daily loop

1. `python run.py full --market chennai` — takes a while, filters hard
2. Open `vault/02_Analyzed` in Obsidian, read what came back
3. `python run.py pitch`, paste the brief into Claude Code, let it write drafts
4. Read each draft. Kill the weak ones. Add `#approved` to the good ones
5. `python run.py dispatch` — opens each window, copies the text, you send
6. Log the reply in the note. It is already your CRM

---

## Honest limits

- **Yield is deliberately low, and quality beats count.** A Chennai jewellery
  run verified 45 advertisers and kept 1, which then graded **tier C** on
  inspection: a two-ad micro-brand reachable only at a personal Gmail. The
  system now grades every lead A/B/C rather than presenting all survivors as
  equal, because "1 lead" and "1 lead worth calling" are different numbers.
- **Most advertisers already run video.** Across two packs, 32 of 45 and 82 of
  90 had video. The genuine gap is narrow. That is a market fact no code
  change fixes; widening markets is the only real lever.
- **A personal Gmail is a weak signal, not a disqualifier.** Blanket-rejecting
  free mail would discard real businesses like somebrand.1920@gmail.com.
  What matters is whether the address names the BRAND or a PERSON, which is
  what `email_quality` grades.
- **Instagram, X and LinkedIn have no usable free API.** Tested directly:
  Instagram 400, X 401 (paid, from $200/month), LinkedIn 401. Meta's Ad
  Library is the one platform API that gives real lead data for free, and it
  is what this is built on.
- **Meta tokens expire.** The default dies in about an hour. Extend it in the
  [Access Token Debugger](https://developers.facebook.com/tools/debug/accesstoken)
  for sixty days.
- **Contact resolution runs 25–75%.** Brands with a real site resolve well;
  dropshippers publish nothing. Messenger is the fallback that always works
  for a Meta advertiser.

---

## Optional add-ons, judged honestly

| Tool | Verdict |
|---|---|
| **Agent-Reach** (77k stars) | Fine for YouTube transcripts, RSS, GitHub, page reads. Its Instagram/X/Facebook modes need **your login cookie**. Do not use those with studio accounts. |
| **Firecrawl** (175k stars) | Genuinely good page-to-markdown. Free tier is small. Worth it only if you start reading many sites per day. |
| **Bright Data MCP** (2.6k) | Real, works, **paid proxies**. Only if you hit blocks at scale. |
| **CrewAI / LangGraph** (58k / 41k) | Excellent frameworks, unnecessary here. For a single-user pipeline they add abstraction and dependency churn over a file-based state machine you can read with your eyes. |
| **socialcrawl/mcp** | **9 stars.** Do not build on it. |

---

*Data from Pick an Agency (pickanagency.com) is CC-BY and requires a link back
where used.*
