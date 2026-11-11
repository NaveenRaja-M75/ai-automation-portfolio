# Lead Scoring CRM Dashboard

A small CRM for inbound real-estate leads: a public intake form, automatic
lead scoring, a dashboard of lead stats, and an AI chat simulator.

## Features

- **Lead intake** (`/intake`) posts to `api/leads/intake`, with input sanitization (`src/lib/sanitization.ts`)
- **Scoring** (`src/lib/scoring.ts`), 100 points: budget (30, parses "85 Lakhs" or "1.5 Cr"), timeline (30), requirement clarity (20), engagement (20). Score sets the lead status.
- **Dashboard:** stats, lead list, lead detail with status updates (`api/leads/[id]`)
- **AI chat simulator** using the Gemini API (`api/chat`)
- **Database:** Supabase Postgres schema in `supabase/schema.sql`, with mock data for demo mode

## Run locally

```bash
npm install
npm run dev
```

Set `NEXT_PUBLIC_SUPABASE_URL`, `NEXT_PUBLIC_SUPABASE_ANON_KEY` and `GEMINI_API_KEY`
in `.env.local`.

Stack: Next.js (App Router), TypeScript, Tailwind CSS, Supabase, Gemini API.
