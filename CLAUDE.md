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
