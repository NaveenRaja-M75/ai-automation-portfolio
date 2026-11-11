# AI Automation Portfolio — Naveen Raja M

B.E. Computer Science (S.A. Engineering College, Chennai, 2022–2026).
Projects in workflow automation, lead enrichment, CRM tooling, API and webhook
integration, and LLM agents. Each was built with AI-assisted development
(Claude Code), with me owning the design, debugging, integration and testing.

| # | Project | What it does | Stack |
|---|---|---|---|
| 01 | [Dental Clinic WhatsApp Bot](01-dental-clinic-whatsapp-bot) | Patients book, cancel and confirm dental appointments over WhatsApp; staff dashboard shows daily, monthly and upcoming bookings and marks check-ins and no-shows | Node.js, Twilio WhatsApp webhooks, Supabase (Postgres), Netlify Functions |
| 02 | [Lead Scoring CRM Dashboard](02-lead-scoring-crm-dashboard) | Lead intake form, rule-based lead scoring, lead list and detail views, AI chat simulator | Next.js, TypeScript, Supabase SQL schema, Gemini API |
| 03 | [Lead Discovery Agent](03-lead-discovery-agent) | Discovers local businesses, reads their websites, and has Claude score each against a niche playbook; exports a ranked list with evidence | Python, OpenStreetMap / Google Places, Claude API, SQLite, CSV / Google Sheets |
| 04 | [Lead OS Outreach Engine](04-lead-os-outreach-engine) | Finds brands already paying for ads, enriches and verifies contacts, de-duplicates, and queues human-approved outreach drafts | Python, web scraping, data validation, Markdown vault |
| 05 | [LeadForge Scraper](05-leadforge-scraper) | Multi-source lead engine: fetch, ICP scoring, contact enrichment, export | Python (standard library only) |
| 06 | [Jarvis / FROST Voice Assistant](06-jarvis-voice-assistant) | Local voice assistant with tools, memory, sandboxed execution, and a permission gate with an audit log | Python, Claude Agent SDK, faster-whisper, edge-tts, Docker |
| 07 | [AI Virality Engine](07-ai-virality-engine) | Multi-agent pipeline that turns a niche and topic into a structured video script; dashboard with login | Next.js, TypeScript, Supabase Auth, Gemini API |
| 08 | [Algo Trading Bot](08-algo-trading-bot) | Paper-first crypto trading bot with backtesting, walk-forward tests, risk gate, and live signal scoring | Python, Binance API, pytest |

## Skills shown

- **APIs and webhooks:** Twilio signature-verified webhooks, Supabase, Google Places, Claude and Gemini APIs
- **Data work:** validation, normalization, duplicate detection, contact enrichment, lead scoring
- **SQL:** schema design (`02-lead-scoring-crm-dashboard/supabase/schema.sql`), SQLite store in project 03
- **LLM workflows:** prompt-based scoring, multi-agent pipelines, human approval before anything is sent
- **Engineering habits:** tests (`tests/` in 03, 06, 08), `.env.example` configs, logging, error handling, Git

## Notes

Secrets are never committed: every project reads keys from a local `.env`
(see each `.env.example`). Scraped lead data and personal contact details were
left out of this repository.
