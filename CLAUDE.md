# Steinfrank

Hackathon project (~12h). **Frankenstein** is the builder that creates **monsters**; each monster is a workflow that achieves one specific task. The user talks to a monster by voice.

## Stack

- **Voice + LLM:** ElevenLabs Agents Platform. Use its built-in LLM (fast, low-reasoning model, e.g. Claude Haiku 4.5 / Gemini Flash); latency matters more than smarts in the voice loop.
- **Tools (monster "limbs"):** Sokosumi agent marketplace. Not an LLM API: you hire an agent by creating a job, which runs async for minutes and costs credits.
- **Backup LLM:** OpenAI API ($5 budget), for non-realtime work such as generating monster definitions.

## Sokosumi

- Base URL `https://api.sokosumi.com/v1`, `Authorization: Bearer $SOKOSUMI_API_KEY`. Spec: `/v1/openapi.json`.
- Flow: `GET /agents/{id}/input-schema` → `POST /agents/{id}/jobs` with `{inputSchema, inputData, maxCredits}` → poll `GET /jobs/{id}` until `status` is terminal (`completed`, `failed`, `input_required`, …). `input_required` is answered via `POST /jobs/{id}/inputs`.
- `sokosumi-test/soko.py` is a working CLI client (`agents`, `schema`, `hire`, `job`, `wait`).
- Jobs cost 30–1850 credits. Ask before running one.

## Design constraints

- A voice tool call can't block for minutes: split Sokosumi use into a `start_job` tool (returns job ID) and a result check/poll.
- Prefer ElevenLabs **client tools** (run in the browser) over server/webhook tools, which need a public URL.

## Secrets

Keys live in the root `.env` (gitignored): `SOKOSUMI_API_KEY`, `ELEVENLABS_API_KEY`. The ElevenLabs key is permission-scoped; creating agents via API needs the Agents permissions on it.

## Frankenstein (`frankenstein/`)

- A monster is a JSON spec (`frankenstein/spec.py`, examples in `monsters/`) run by a deterministic interpreter (`runtime.py`): sequential steps or `parallel` groups, `when` branches, bounded `for_each`, `{{memory.*}}` between scheduled runs, `{{run.now}}`. Outputs must include `speech` (spoken via ElevenLabs automatically, <= 900 chars) and `report` (markdown).
- Forge pipeline `forge/pipeline.py`: moderation → policy check → design (`forge/designer_prompt.md`) → validate → budgets auto-fitted → dry run on the designer's examples (paid limbs mocked, plus a declined-paid pass) → speech review → draft → publish (+ private ElevenLabs agent).
- Paid Sokosumi steps above `FRANK_CONFIRM_ABOVE_CREDITS` (100) pause at `needs_confirmation`; daily cap `FRANK_DAILY_CREDIT_CAP`.
- Delivery: `channels.py` (ui, ntfy, email/Resend, slack, telegram, webhook). Recipients are registered destinations, never chosen by a spec. Watchers: `watches.py`.
- Run from `frankenstein/`: `python -m pytest -q`, `python -m frankenstein.cli ...`, `python -m uvicorn frankenstein.api:app` (voice test page at `/dev/voice`).
- Designer model `gpt-5.4-mini` (`FRANK_DESIGN_MODEL`); run-time llm steps `gpt-4.1-nano` (non-reasoning on purpose: no hidden tokens).

## Monster Lab UI (`monster-lab/`)

- Browser front end (Vite + vanilla JS + Three.js, no backend yet). Stop-motion-style 3D: the scientist (Dr. Stitchwick) builds one persistent **topic assistant monster** per broad topic; each gets its own tab with a chat. Full doc: `monster-lab/README.md`.
- Run: `cd monster-lab && npm install && npm run dev` (`npm run build` for a static bundle). `?debug=stage` shows the 3D stage full-window; `window.lab` exposes internals.
- Flow: a Laboratory question → `matchTheme()` topic detection (`src/monsters/themes.js`, 20 topics) → new topic: short surgery cinematic (`director.create`) then the tab opens and answers; existing topic: its wandering counterpart charges the screen and screams (`director.summon`), then its tab answers. Follow-ups in a tab never trigger surgery.
- Assistants (`src/monsters/registry.js`, persisted in localStorage `stitchwick-lab.v1`): `{ id, theme, name, seed, instructions, provider, chat }`. One per topic; the seed + topic rebuilds the identical monster everywhere (slab, wandering, den, portrait).
- Answers come from `src/ai/assistants.js → respond()`, an async iterable of `step` / `stepDone` / `token` events; `PROVIDERS[provider.kind]`, currently only `scripted` (`src/workflows/`). Step `kind` (gather/read/think/compute/write) drives the monster's thinking animation. To connect Frankenstein, add a provider that maps spec steps to step events and streams `report` as tokens.
- Wandering counterparts are separate from assistants: overcrowding cleanup (threshold default 8; bomb → acid → hose) only destroys counterparts, never assistants, tabs or histories.
- Rendering: one WebGL canvas behind the DOM draws the active scene into `#stage` plus the creature layer; DOM stays on top so creatures never block text or clicks. Poses tick at 12 fps (stop-motion); adaptive pixel ratio protects integrated GPUs.
