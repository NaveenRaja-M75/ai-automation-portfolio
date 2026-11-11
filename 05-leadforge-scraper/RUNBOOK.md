# LeadForge RUNBOOK

The operating loop. Strategy and positioning live in `../LEAD-GEN-PLAYBOOK.md`; this file is *what you actually do*, in order, with the exact commands.

Budget: **$0**. Time: about **30 minutes a day**. The bottleneck is never the list — it is the messages you send.

---

## Day 0 — one-time setup (10 minutes)

```powershell
cd leadforge
python run.py doctor
```

Read the output honestly:

- `ok` sources work from your network right now.
- `needs-key` are optional upgrades. Ignore them until you have paying clients.
- `off` are the slow or IP-sensitive ones. Leave them off until you have an evening to babysit them.

Then set your ICP once, in `config.toml`:

1. `[[offers]]` — keep the offers you actually want to sell. Delete the rest. This is what the FIT score matches on.
2. `[keywords]` → `buy` — add the literal phrases your buyers write. Not "AI video solutions". Yes: `"looking for a video editor"`, `"need a ugc creator"`, `"our ads are dying"`.
3. `[profile]` → `market` — `global`, `india`, `usa`, `uk`, `mena` or `eu`.

Then the first run:

```powershell
python run.py fetch
python run.py stats
```

You should end up with a few hundred leads, a handful in tier A/B, and a clear picture of which sources are carrying you.

---

## The daily loop (~30 min)

### 1. Fetch and grade (2 min)

```powershell
python run.py fetch --sources high-signal --window 14d
python run.py rescore
```

`high-signal` is the intent-rich half: Hacker News, Reddit, job posts, live Meta advertisers. Run it daily. Add `job-boards` on Mondays.

### 2. Read the top of the list (8 min)

```powershell
python run.py list --tier A --body 400
python run.py list --tier B --limit 25 --body 200
```

Read the actual post. The score tells you *where to look*; your eyes decide whether it is real. A B lead you understand is worth more than an A lead you don't. If something is clearly irrelevant, the fix is a keyword in `config.toml`, not a click — then `rescore`.

### 3. Make them contactable (10 min)

```powershell
python run.py enrich --tier A,B --limit 25
```

This is where a name becomes an email, an ad pixel becomes a budget proof, and a dead domain gets flagged before you waste a send on it.

### 4. Send (10 min)

```powershell
python run.py export --tier A,B --only-with-email --drafts 10
```

You get CSV/JSONL/Markdown plus personalised drafts in `data/exports/`.

**Send 10–20 a day. Not 200.** Small, personal batches keep you out of spam folders and force you to actually read the signal. Every message should reference *their* specific words — the tool gives you the exact sentence.

---

## Which lead to work, and how

| Tier | What it is | What to do |
|---|---|---|
| **A** | Named one of your offers *and* showed intent | Send today, by hand. Reference their exact sentence. |
| **B** | Adjacent fit, or a proven advertiser | Send this week. Advertisers get the "10 more variants at a fraction of agency cost" angle. |
| **C** | Weak fit, or a very generic signal | Batch it. Use these to test subject lines, not to expect replies. |
| **D** | No fit signal | Ignore. It is stored only so you can see what the sources are actually returning. |

Within the same tier, work in this order:

1. **Live Meta advertisers** (`meta_ads_open`) — proven spend, zero education needed.
2. **HN "SEEKING FREELANCER" posts** — real paid gigs, replied to by email, no platform in the middle.
3. **Reddit buyer posts** (`reddit_rss`) — fast, direct, and a comment can beat a DM.
4. **Job posts** — a company hiring a video editor or a performance-creative lead is telling you it has a content problem and a budget. Most have a website with an ad pixel to confirm it.

---

## The signal behind each channel

**Hacker News — your best free channel.** The monthly "Freelancer? Seeking freelancer?" thread has a comment half that starts with `SEEKING FREELANCER`. Those are people with money, posting exactly what they want, and their email is often right there. Reply by email, not in the thread. Costs nothing.

**Live Meta advertisers.** A brand running video ads already pays for creative. You are not selling them the idea of video — you are selling speed and price. Show one concrete variant idea for *their* current ad.

**Reddit.** Search-driven intent, not audience-building. Answer the actual question in the thread where you can, then DM. Read the subreddit rules first; some ban promotion outright.

**Job boards as signals, not applications.** You cannot win Upwork for $0 — bidding costs Connects and its public RSS feed is gone. But the job post tells you the company, and the company almost always has a site you can email. Only do this where it does not breach the platform's terms; it usually does on Upwork, so weigh that.

**Fiverr.** Inbound-only. Fiverr removed Buyer Requests in 2023, so treat Fiverr as a *shop window* — optimise your gig, do not expect it to source outbound leads.

**LinkedIn.** Use the free tier manually: a connection request with a specific note, or a comment on the post where they said they were hiring. ~100 connection requests a week is the free ceiling. Do not automate it — that is the fastest way to lose the account.

**Threads / X / Instagram.** Search is real but the free surface is thin, and scraping them breaches their ToS. Do not build around them. If you want them, do it by hand, sparingly, from your own account.

---

## What to actually send

Keep it under 90 words. Three moves: prove you read their post, offer one specific thing, make the next step tiny.

> **Subject:** the video editor post
>
> Saw your post — you're after someone who can turn around short-form ad edits fast.
>
> I produce AI UGC and ad variants: 10 hooks cut from one script, no creators, no reshoot costs. Here's a free sample against your current ad so you can judge the output before spending anything.
>
> Want me to send it?

For an advertiser, swap the opener:

> Saw you're running [specific ad]. I make AI variants of exactly that format — usually 8–12 angles from one concept, delivered in a couple of days.
>
> Free sample of one variant first, so you can see the quality. Interested?

**Rules:** one ask, one link at most, no attachment on the first touch, and never more than two follow-ups (day 3 and day 10), then stop.

---

## Weekly maintenance (10 min)

```powershell
python run.py stats
python run.py doctor
python run.py rescore
```

Check three numbers:

1. **Tier A + B trending up or down?** Down means your keywords are wrong, or the sources have gone quiet.
2. **`advertisers` count.** These are your warmest leads — if it is 0, your `enrich` runs are not reaching anyone with a real website.
3. **Sources with 0 leads.** Something broke. Run `python run.py doctor --source <name>` and check.

Then spend five minutes on the highest-leverage thing in this whole system: **your reply rate.** Read your last 20 sent messages. If nobody answers, the list is fine and the message is wrong.

---

## When something breaks

| Symptom | Cause | Fix |
|---|---|---|
| A source returns 0 and an error | The site changed its HTML, or is throttling your IP | `python run.py doctor --source <name>`. Many free sources are simply intermittent. |
| Tier A/B is suddenly full of junk | Your keywords are too loose, or scores are stale | Tighten `[keywords]` in `config.toml`, then `python run.py rescore` |
| A company resolved to the wrong website | Name too generic | Expected — names under 5 characters are skipped. Add the domain manually if you need it. |
| Every source fails at once | Your IP is rate-limited, or you have no network | Wait an hour. Do not retry in a loop. |
| `websearch` stores nothing | Your IP gets a degraded SERP | Normal on rented/VPN IPs. Leave it off. |
| `enrich` is slow | It is resolving company names over the network | Normal for a new batch. Domains are cached for 60 days, so the second run is fast. |

---

## The one honest caveat

This finds people who have already said, in public, that they want what you sell. It does not close them. The difference between a studio that gets clients and one that doesn't is not the list quality — both are fine — it is that the first one sends twenty precise messages every day for six weeks.
