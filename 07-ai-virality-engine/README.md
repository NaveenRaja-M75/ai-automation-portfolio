# AI Virality Engine

A multi-agent LLM pipeline that turns a niche, topic, audience and tone into a
structured, high-retention video script, plus a dashboard to run it.

## Agents

Defined in `src/utils/ai/agents.ts`. Each returns JSON that feeds the next:

0. Context Compiler: core tension, audience psychology, storytelling style
1. Research Agent
2. Hook Specialist
3. Narrative Architect
4. Script Writer
5. Retention Critic

## App

- `dashboard/analyzer` and `api/analyzer`: analysis step
- `dashboard/script-engine` and `api/scripts/generate`: script generation
- Supabase Auth login with route-protecting middleware

## Run locally

```bash
npm install
npm run dev
```

Set `NEXT_PUBLIC_SUPABASE_URL`, `NEXT_PUBLIC_SUPABASE_ANON_KEY` and `GEMINI_API_KEY` in `.env.local`.

Stack: Next.js, TypeScript, Tailwind CSS, Supabase Auth, Gemini API.
