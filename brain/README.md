# brain/ — Monster Lab UI on the "brain" engine

This folder joins the two halves of the project while keeping one canonical UI:

| | Where it comes from | What changed |
|---|---|---|
| `../monster-lab/` | the team's original UI (Three.js lab, tabs, workflow boxes, live voice) | used directly, so its original WebAudio sounds cannot drift from a copied UI |
| `backend/` | copy of the "brain" backend (FastAPI) | new `app/lab_api.py`, `app/lab_voice.py`, DB migration 7, CORS |

`../frankenstein/` and `../monster-lab/` are unchanged and still work on their own.

## Run

```powershell
.\start.ps1            # venv + npm install on first run, API on :8010, lab on :5173
.\start.ps1 -Port 8020 # optional custom port
```

First run creates `backend/.env` from `.env.example`: put `ELEVENLABS_API_KEY` there (brain LLM, TTS, voice agents)
and optionally `SOKOSUMI_API_KEY`. `ALLOW_NETWORK_CAPABILITIES=true` is needed for the weather/web limbs.
`?offline=1` still forces the scripted mode.

At startup the lab backend imports missing global skills, knowledge packs and safe cache entries from
`../../backend/data` in read-only mode. Tasks, monsters and run history are never copied. The import is idempotent, so the
original brain's fast learned paths are reused without mixing the two projects' state.

The UI's original procedural sounds remain immediate. Generated ElevenLabs birth effects stay optional
(`FRANK_LAB_GENERATED_AUDIO=0`), while final answers are voiced by default through exactly the same Friend pipeline:
the archetype voice from `../frankenstein/frankenstein/voices.json`, model `eleven_v3`, and playback through the UI's
`monsterVoice` (`FRANK_LAB_TYPED_TTS=1`). Live voice sessions are unaffected.

Tests: `cd backend; .venv\Scripts\python -m pytest -q` (no real keys used; `tests/test_lab_api.py` covers the UI contract).

## How the lab maps onto the brain

The lab speaks the `frankenstein/api.py` contract; `lab_api.py` translates every call:

| Lab | Brain |
|---|---|
| `POST /forge` | **instant** monster, 0 tokens. If a verified template covers the topic (weather, interview) its skill is installed right away; other organs grow on the first question that needs them |
| re-forge of a known topic (`learnJob`) | same monster, `version + 1`, jobs merged |
| `POST /monsters/{id}/publish` | card with limbs, sounds + birth scene (ElevenLabs theatrics), voice agent if `voice: true` |
| `POST /monsters/{id}/runs` | a chain of brain tasks: skill fast path → knowledge packs → LLM only for what is unknown → build (gated) → Sokosumi (gated) → compose |
| `needs_input` | the skill recognised the request but a value is missing ("For which city?") |
| `needs_confirmation` | the creation gate (new organ) **or** a Sokosumi research offer with the chosen agent and price |
| workflow boxes | the real path: `understand, knowledge, skill, think, build, research, compose` with per-step usage and ≈ $ |
| `GET /monsters/{id}/voice-session` | ElevenLabs agent per monster with client tools `start_run`, `check_run`, `answer_input`, `confirm_spend` |

What the brain adds to the lab: repeated questions cost 0 tokens (skills, knowledge packs, LLM cache), no organ is built
and no credits are spent without a click, token budgets per task, Sokosumi agents picked automatically from the local catalog,
research results reused for 30 days.

Three lab-specific rules (the lab monster is born empty, so the brain's "a new monster grows its first organs for free" is off):
- any new organ (generated code) waits for **Build it?** in the chat;
- words such as "current", "latest", "market" or a year do not trigger paid research by themselves. Sokosumi is offered
  only after an explicit request to research/search online (including the UI's **Research** button);
- this conservative policy is `FRANK_LAB_RESEARCH_MODE=explicit`; set it to `automatic` only if offers on freshness hints
  are desired.

Routine planning and answer composition use the configured fast model in the lab (`FRANK_LAB_FAST_RESPONSES=1`). Code
generation, organ builds and repairs still use the stronger model. Learned skills and cached answers remain the first,
model-free path.
