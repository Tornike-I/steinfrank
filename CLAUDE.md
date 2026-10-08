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
