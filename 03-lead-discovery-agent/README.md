# lead-agent

Finds and scores local-business leads for an AI automation agency. It discovers
businesses in a geography, reads their websites, and has Claude score each one
against a niche playbook — then hands you a ranked list with the reasoning and
the evidence attached.

It **finds and scores only**. It does not send anything to anyone.

## What makes a good lead here

The offer is AI agents that absorb repeated, scripted work — a WhatsApp
appointment bot, a lead-qualifying bot, an AI voice receptionist. So the scoring
is close to the opposite of what a web-design agency would want:

| Weight | Dimension | What it measures |
|---|---|---|
| 40% | ability to pay | review volume and velocity, price level, staff count, multi-location, ad spend |
| 35% | pain intensity | reviews saying *"nobody answered"*, call-only booking, hand-worked WhatsApp, high inbound volume |
| 15% | reachability | named owner, direct phone, published email, WhatsApp |
| 10% | digital gap | no site, stale site, no online booking |

A busy clinic with a polished website and 400 reviews scores **higher** than a
struggling one with no website. Volume of repetitive work is the product, and
only a business with volume has enough of it to be worth removing.

## Setup

```bash
python -m venv .venv
.venv\Scripts\python.exe -m pip install -e .
copy .env.example .env
```

Put your `ANTHROPIC_API_KEY` in `.env`. Nothing else is required to start —
discovery defaults to OpenStreetMap, which needs no key and no billing.

## Use

```bash
python -m leadagent run
```

It asks for geography before every run, then shows a plan and waits for
confirmation. Non-interactively:

```bash
python -m leadagent run --geo Chennai,Bengaluru --niches dentist,real_estate
python -m leadagent run --state "Tamil Nadu" --niches dentist
python -m leadagent run --all-india --max-tier 1 -y
python -m leadagent run --no-score          # discover and enrich, zero model spend
```

Export, filtering at export time:

```bash
python -m leadagent export --tiers A,B --min-band 3
python -m leadagent export --tiers A --min-band 4 --out out/premium.csv
python -m leadagent status
```

## Why the budget band is an output, not a setting

You have not fixed your pricing yet. So the agent **estimates** what each
business could carry (band 1–5, under ₹10k/month up to ₹1L+/month) and stores
it. The cutoff is applied when you export, not when you collect.

Run once, look at real businesses with real bands, then decide what you charge —
and re-export against any threshold forever without re-fetching or re-scoring
anything.

## Why it resumes

Work is split into `(city, niche)` targets in a SQLite queue. Each one is
checkpointed. When a run hits a budget ceiling it stops clean and marks the rest
pending; the next run continues from there. Businesses are cached permanently by
source key, so re-running a city never re-pays for a business already seen.

That is what makes all-India affordable: identical code, run in waves. Top
metros this week, tier-2 next month. `--max-cities` and the ceilings in
`settings.yaml` are the only dials.

## The evidence gate

Every reason Claude gives must cite a field that is actually populated in the
record. Reasons citing empty fields are dropped before the lead is stored and
kept in `dropped_reasons` so you can see when the scorer is straining.

`pain_evidence` must be a **verbatim** quote from a real customer review. It is
checked against the review text, and discarded if it does not match — because
that string is what you paste into an outreach message, and a fabricated quote
is worse than an empty field.

`confidence` is forced down when signal families are missing. Scoring a business
with no reviews and no reachable website can never report high confidence.

Verify this logic without spending anything:

```bash
.venv\Scripts\python.exe tests\test_scoring_offline.py
```

## Discovery sources

**OpenStreetMap (default).** Free, no key, no per-query cap — a whole city comes
back in one bounding-box request. The catch is sparse data: most Indian
businesses in OSM have no website and OSM has no reviews at all. That means the
two heaviest scoring dimensions run empty, and the scorer correctly reports low
confidence. Good for proving the pipeline; not good enough to sell from.

**Google Places.** Set `discovery.source: places` and `GOOGLE_PLACES_API_KEY`.
Brings ratings, review counts, price level, and review text — which is where the
pain signals live. Costs money: results cap at ~60 per query so each city is
covered by a grid of tiled searches, and `places.reviews` is a premium field.

Set a **quota cap in the Google Cloud console** as well. That is your hard stop,
independent of this app's budget guard.

## Adding a niche

Drop a YAML file in `config/niches/`. No code changes. Copy `dentist.yaml`, change
the OSM filters, Places queries, signals and angle. That is the whole extension
mechanism — the remaining niches (gyms, restaurants, plumbers, barbers, car
stickering, second-hand shops) are each one file.

## Politeness

These are small businesses you intend to contact later. `robots.txt` is honoured,
responses are size-capped, and only a handful of pages are read per site. **Put a
real contact address in `enrichment.user_agent` in `settings.yaml` before running
at any scale** — site owners are entitled to know who is fetching their pages.

## Layout

```
config/settings.yaml       ceilings, weights, models, source selection
config/niches/*.yaml       one playbook per business type
data/cities_in.csv         city coordinates, tier, search radius
src/leadagent/geo.py       city lookup, bounding boxes, Places tiling
src/leadagent/store.py     SQLite, resumable target queue, permanent cache
src/leadagent/discover/    osm.py (default), places.py (needs a key)
src/leadagent/enrich/      robots-respecting fetcher, signal extraction
src/leadagent/score/       prompt, output schema, evidence gate
src/leadagent/export/      csv_out.py, sheets.py (stubbed)
```
