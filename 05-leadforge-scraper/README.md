# LeadForge

A free, local lead engine for an AI studio selling **AI ads · AI product ads · AI commercial ads · AI UGC · 3D websites · AI films · AI receptionists**.

It does the four jobs the paid tools do — find prospects, prove they have budget, pull contact details, hand you a send-ready list — with **no subscription, no account, and no API keys required**.

```powershell
cd leadforge
python run.py doctor          # confirm every source works from your machine
python run.py fetch           # go and get leads
python run.py enrich --tier A,B
python run.py export --tier A,B --only-with-email --drafts 10
```

Python 3.11+ and nothing else. No `pip install`, no Node, no browser.

> Strategy (who to target, what to say) lives in `../LEAD-GEN-PLAYBOOK.md`.
> The day-to-day operating loop lives in `RUNBOOK.md` next to this file.

---

## 1. Why this is not just "another scraper"

Apollo, Apify and Clay sell you **lists**. A list is a guess: a company that *could* buy is worth almost nothing, because you still have to convince them they have a problem.

LeadForge scrapes **signals** instead — a person or company that has already publicly said they need the thing you sell. Two of those signals are the reason this tool exists:

| Signal | What it means | Where it comes from |
|---|---|---|
| **Buyer language** — "looking for a video editor", "need UGC creators", "$2k budget" | Someone is asking for your service *right now*, in their own words | Hacker News, Reddit, job posts, news |
| **A live ad pixel on their site** (Meta Pixel, TikTok Pixel, Google Ads, LinkedIn Insight) | They are **already spending money on ads and creative** | Their own public HTML |

The ad-pixel check is free, unlimited, and cannot be bought from any list vendor. If a prospect's site fires a Meta Pixel and a Google Ads tag, they have a budget and a creative problem — you do not have to educate them.

That is why the final list is short. **A dozen tier A/B leads beat 10,000 Apollo rows.**

---

## 2. The commands

| Command | What it does |
|---|---|
| `doctor` | Health-check every active source and tell you what works from *your* network |
| `fetch` | Run the enabled sources, score everything, store it |
| `list` | Show stored leads (`--tier`, `--min-score`, `--offer`, `--body N`, `--json`, `--csv`) |
| `enrich` | Resolve each company's website, detect ad pixels, find emails + socials, verify MX |
| `rescore` | Re-grade the whole database with the current scorer (run after editing keywords) |
| `export` | Write CSV / JSONL / Markdown, plus personalised outreach drafts |
| `stats` | Tier counts, per-source counts, advertisers, leads per offer |
| `sources` | List every source, its group and its platform bonus |
| `addkey` | Drop an optional API key into `config.toml` safely |

Useful flags:

```powershell
python run.py fetch  --sources high-signal           # one group instead of everything
python run.py fetch  --sources hn_whoishiring,reddit_rss
python run.py fetch  --window 7d --limit 100
python run.py fetch  --enrich                        # fetch then enrich in one go
python run.py list   --tier A --body 300             # read the actual post
python run.py enrich --tier A,B --all --limit 50     # re-enrich, not just the email-less
python run.py doctor --source high-signal            # check one group
```

Sources you switched off in `config.toml` are skipped when you ask for everything, but are **always run when you name them explicitly** — so `fetch --source websearch` is a safe way to test one.

---

## 3. The sources

Zero keys, always on:

| Source | Bought signal |
|---|---|
| `hn_whoishiring` | The "SEEKING FREELANCER" half of Hacker News' monthly thread. Real paid gigs, posted monthly, replied to by email. **Your best free channel.** |
| `hn_search` | Full-text search across every HN comment ever |
| `linkedin_jobs` | LinkedIn's public guest job search — no login, no account |
| `remoteok` `remotive` `jobicy` `arbeitnow` `himalayas` `workingnomads` `weworkremotely` | Remote job boards; companies hiring creative roles |
| `producthunt` | New launches — every launch needs a promo video |
| `bingnews` | Bing News RSS for any query you configure |
| `reddit_rss` | Reddit via public Atom feeds — buyer posts only, no API key |
| `meta_ads_open` | Businesses running **live** Meta ads, scraped from the public Ad Library. No token. Proven ad spend. |

Optional upgrades (the only sources that ever ask for a key):

| Source | Key | Why bother |
|---|---|---|
| `reddit` | Free Reddit app client_id + secret | Wider, more reliable Reddit coverage than the RSS path |
| `meta_ads` | Free Meta developer token | Structured Ad Library search instead of the public page |

Off by default, on purpose:

| Source | Why |
|---|---|
| `websearch` | Free web search for buyer phrases. Most engines serve a **degraded SERP** to datacenter/VPN IPs — they match the first word and silently ignore quotes and operators. Rather than store that poison, every hit must corroborate the query's own words; if nothing does, the source reports it and stores nothing. From a normal home connection it works well. |
| `osm_local` | Local businesses by category + city via OpenStreetMap. Free and public, but the public Overpass API is strictly rate-limited and mostly throttles automated use. |
| `google_maps` | Rich local-business data (phone, website, rating). Needs a real browser and is slow; best for targeted local runs. |

`doctor` tells you the truth for your machine — run it first.

---

## 4. How a lead is graded

Score out of 100, in six axes:

| Axis | Max | What earns it |
|---|---|---|
| **FIT** | 45 | Does the text name one of *your* offers (strong = 35+, weak = 15+), or hire for a matching role? |
| **INTENT** | 20 | Did they say, in their own words, that they need it? |
| **MONEY** | 12 | A stated budget ($500 → 8, $1,000+ → 12) |
| **FRESHNESS** | 8 | Posted within 48h → 8, within a week → 6 |
| **REACH** | 10 | An email in the post (7) and/or a resolvable company domain (3) |
| **PLATFORM** | 5 | Per-source weight — HN and Meta-advertisers rank above generic job boards |

**Tiers:** A ≥ 65 · B ≥ 45 · C ≥ 25 · D below.

### The two things that stop this being keyword garbage

1. **Two scoring modes.** Every job description on earth contains "we are looking for". Naively keyword-matching that scores Sales Director roles at 100. So `job_board` sources are scored differently from human `post` sources: on a job board the **title** decides and the body is only weak supporting context; on a forum post the body *is* the request.

2. **The FIT gate.** If a lead has no fit signal at all, it is capped at 24 regardless of what its body mentions. On a job board, a title with no fit signal is capped at 32.

On top of that, `rescore` re-grades the *entire* stored database from the text whenever you change a keyword in `config.toml` — stale grades are worse than no grades, because they put junk in tier A and hide real buyers.

**Enrichment can move a score.** Detecting a live ad pixel adds 8 (they already have budget). A domain that cannot receive mail subtracts 15. This adjustment lives in exactly one function and is recomputed from scratch every time, so scores can never drift upward across repeated runs.

---

## 5. What `enrich` turns a name into

A job-board lead gives you a company name and nothing else. `enrich` turns it into a contactable record:

1. **Company name → website.** Guessed and then *verified*: a candidate domain is accepted only when its domain label strictly matches the company name (allowing `get-`/`use-`/`try-` affixes) **and** the live site's own `<title>` / `og:site_name` / `<h1>` corroborates it. A wrong domain is worse than no domain — you would email an unrelated company. Names shorter than 5 characters are skipped as too ambiguous.
2. **Ad-pixel detection** — Meta, Google Ads, GTM, GA4, TikTok, LinkedIn Insight, Twitter, Pinterest, Snap. The strongest buyer signal, and free.
3. **Contact discovery** — mailto: addresses, named people with roles, social handles and phone numbers, crawled from their own site with `robots.txt` respected.
4. **MX verification** over DNS-over-HTTPS (no port 25 needed) so you never send to a domain that cannot receive mail.
5. **Pattern guesses** — `first@`, `first.last@` — plus optional Hunter / Snov / Apollo free tiers if you ever add keys.

---

## 6. Tuning it to your ICP

Everything you'd want to change is in `config.toml`; you should never need to edit the code.

- **`[[offers]]`** — your services, each with `strong` and `weak` keyword lists. Add the exact words **your buyers** use, not the words you use. This drives the FIT score.
- **`[keywords]`** — `buy` (intent phrases), `money` (budget phrases), `negative` / `hard_negative` (what to punish or ban outright), `role` (job titles that mean "needs creative").
- **`[sources.*]`** — `enabled`, and per-source settings such as queries, countries and cities.

Then re-grade what you already have:

```powershell
python run.py rescore
```

Two traps worth knowing, because they cost real accuracy:

- **Bare words cause false positives.** `"commercial"` matches "Commercial Account Executive"; `"ai video"` matches "AI Video Annotator". Both were removed for exactly this reason. Prefer phrases.
- **Do not add `freelancer? seeking freelancer` to `extraction_negative`.** It matches the HN thread title and would delete your single best source.

---

## 7. Honest limits

Read these once so nothing surprises you later.

- **There is no free Upwork feed.** Upwork retired its public RSS jobs feed. Its public guest job search is read-only, needs Connects to apply, and cannot be won for free. LeadForge treats Upwork/Fiverr posts as *demand signals* — a way to learn which companies need creative right now — not as a channel you can win on for $0.
- **Scraping LinkedIn / Instagram / X / Threads violates their Terms of Service** and can get accounts banned. LeadForge deliberately does **not** log in anywhere and does not scrape private surfaces. The compliant free alternatives — Hacker News, Reddit, job boards, the Meta Ad Library, local directories — are in the source list above, and in practice they carry most of the intent.
- **Free APIs are fragile and rate-limited.** Sources change their HTML without warning, throttle rented IPs, or disappear. That is why `doctor` exists, why failures are reported as errors rather than silently swallowed, and why a source that cannot corroborate its own results stores nothing instead of inventing rows.
- **A signal is not a close.** This tool gets you to a warm, specific, well-informed first message. It does not write the deal for you.
- **Deliverability is on you.** Mass-sending from a fresh domain burns it. Send in small batches, personalise the first line, and warm up slowly.

---

## 8. Files

```
leadforge/
  run.py                 CLI entry point
  config.toml            your ICP, offers, keywords, source switches — edit this
  README.md              this file
  RUNBOOK.md             the daily operating loop
  lf/
    core.py              HTTP client, helpers, SQLite store
    sources.py           every source adapter + the registry
    icp.py               scoring, tiers, regrading
    enrich.py            ad pixels, emails, MX, company -> domain
    report.py            CSV / JSONL / Markdown export
  data/leadforge.db      the lead database (SQLite) — this is your asset
  data/cache/            HTTP cache, so re-runs are cheap and polite
```
