# Monster Lab (Frankenstein Laboratory)

The visual front end for Steinfrank. It's a browser app where a stop-motion-style
mad scientist, Dr. Frankenstein, builds a **topic-specific assistant monster**
for each broad subject you ask about. Each monster then lives in its own tab
with its own conversation. While it works, it acts out each workflow step so
there's something fun to watch.

Everything is client-side: Vite, vanilla JS and Three.js. There is no backend
and no AI connection yet. Answers come from scripted, simulated workflows,
behind a provider interface meant to be replaced by real ones (for example
Frankenstein monsters or ElevenLabs agents).

```bash
cd monster-lab
npm install
npm run dev        # http://localhost:5173
npm run build      # static bundle in dist/
```

---

## 1. What the user sees

### Laboratory tab (home)
- The 3D lab fills most of the screen. The scientist, operating table and
  wandering monsters are in the scene, with a single prompt bar at the
  bottom. **No text answers are shown here.**
- Typing a question detects its **topic** (20 topics, see §4). A banner shows
  what will happen:
  - **New topic:** "Creating a Weather monster". This plays a short
    surgical cinematic: pull back to the slab, incision close-up, attach or
    sew, lever, lightning, reveal. The monster leaps off the table and starts
    wandering, its tab is created and opened, and the question is answered
    there.
  - **Existing topic:** "Summoning the Weather monster". Its wandering
    counterpart turns to face the viewer, charges at the screen growing huge,
    and screams ("AAAARGH!" visually; audio only when sound is on). Then its
    tab opens with the question appended. If the counterpart was destroyed in
    cleanup, a temporary stand-in walks in for the transition. No duplicate
    assistant is ever created.
- **Stop during creation:** the scientist hoists the unfinished body into the
  bin and returns to idle. No assistant or tab is created.
- **Clicking a wandering monster** (or its name tag) opens its tab.
- **Overcrowding cleanup:** when wandering monsters plus corpses reach the
  threshold (gear menu, default 8), the current operation finishes, and then:
  bomb, scientist takes cover, KA-BLAM, acid dissolves the remains, and the
  scientist hoses down the lab floor. Cleanup destroys **only the wandering
  counterparts**; assistants, tabs and histories are never touched. You can
  keep typing during cleanup, and a submitted question is queued until the
  wash ends.

### Monster tabs (sidebar)
The sidebar is the tab list: a permanent **Laboratory** tab, then one
persistent tab per assistant (portrait, topic label, monster name, and a
spinner while it's answering). Each tab has:
- A large animated version of the monster in its themed **den**.
- A chat with streaming answers and its own history. A collapsible
  **workflow checklist** ("thinking" box) shows each step live.
- A prompt bar for follow-up questions. These never trigger surgery.

While answering, the monster **performs** each step: looking or sniffing,
reading, thinking (hand to chin, smoke puffs), computing, writing. A thought
bubble names the current step. Its mouth moves while text streams, and it
cheers when done.
- **Stop:** "sad" beat, then idle.
- **Failure:** include `fail` in a question to simulate one.
- **Regenerate:** reruns the answer with the same assistant. No new monster
  and no surgery.

Off-topic questions are redirected ("That sounds like a question for the
Cooking monster — ask it in the Laboratory").

Sound starts **muted**. All sound effects are synthesised with WebAudio.

---

## 2. Architecture

```
monster-lab/
  index.html               App shell (sidebar tabs, stage, thread, composer)
  src/
    main.js                Boot, render loop, adaptive resolution, thumbnails, picking
    styles.css
    core/        store.js (localStorage), rng.js (seeded)
    ai/assistants.js       Provider layer per assistant (+ off-topic redirect)
    workflows/   engine.js (step runner), workflows.js (scripted workflows per topic)
    chat/        ui.js (DOM), monsterController.js (tab chats), markdown.js
    lab/         labController.js (topic routing), labScene.js (set, lights, CameraRig),
                 scientist.js (rig + IK + pose blending), actions.js (choreography),
                 patient.js (body on the slab, wounds, limbs), props.js, fx.js, constants.js
    director.js            Lifecycle → choreography (create / summon / scrap / cleanup)
    monsters/    monsterGen.js (procedural generator), themes.js (20 topics),
                 themeProps.js (signature props), monsterAnim.js (gait + verbs),
                 registry.js (assistants)
    creatures/creatureLayer.js   Wandering counterparts, deaths, explosion, acid, summon
    den/den.js             A monster's themed room, thought bubble, performance
    fx/overlay.js          Brief full-screen flash above the UI (explosions)
    audio/sfx.js           Synthesised sounds
```

### Rendering layers
A single WebGL canvas sits **behind** the DOM (`#gl`, `pointer-events: none`).
Each frame:
1. The active 3D scene, either the **lab** or the selected monster's **den**,
   is drawn scissored into the `#stage` rectangle with its own perspective
   camera.
2. In the Lab only, the **creature layer** is drawn over the whole viewport
   with an orthographic camera in CSS-pixel units.

Chat, composer and sidebar are opaque DOM on top, so monsters can never
cover text or catch clicks. Clicks on empty scene areas are hit-tested
against creatures in JS (`creatures.pick`). A second canvas, `#fx`, above the
UI draws the brief explosion flash.

**Motion:** character poses (scientist, monsters) are evaluated every frame
for smooth animation; the handmade look comes from the models and materials.

**Collision:** the scientist's hands are pushed out of solids (the table slab
and the patient's torso ellipsoid). If an arm would pass through one, the IK
tries raised elbow poles, and as a last resort lifts the hand until the whole
arm clears it (`Scientist._keepOut` / `_armHits`; the organ pull opts out to
reach into the wound). Floor monsters never step into the table, the machine,
the bin, the crate or each other: they pick reachable targets, steer around
solids and other monsters, and refuse steps that would overlap.

**Attach shots:** new limbs are delivered to the ends of the slab on the
scientist's side, and the attach camera looks down from higher up, framing the
socket so the hands coming over the body stay in view.

**Adaptive resolution** (`main.js`): if the frame rate drops below 40 (while
focused and not throttled), the pixel ratio steps down to as low as 0.45×,
and it recovers above 57 fps. This keeps integrated GPUs smooth with the
full-screen lab.

### Scientist (`lab/scientist.js`, `lab/actions.js`)
- A puppet built from primitives in a joint hierarchy (no skinned mesh):
  egg-shaped head, brass loupe, hunchback, stained coat and apron, big rubber
  gloves. Lumpy vertex-noise surfaces, procedural thumbprint bump maps,
  instanced stitches.
- **2-bone IK** for arms and legs. Actions output a `Pose` with world-space
  wrist targets, hand orientations, feet targets, spine lean and twist, look
  target, jaw, brows and blink. Tool tips are placed exactly via
  `wristFor(tip)`. Poses blend on action change.
- Actions: `idle`, `greet`, `snap`, `walk` (planted alternating feet),
  `incise`, `sew`, `attach`, `organ`, `inject`, `zap`, `scrap`,
  `throwThing` (bomb or acid), `cower`, `hose`, `celebrate`.

### Director (`director.js`)
- One serial async chain. `reset()` cancels it by bumping an epoch.
- `create(def)`: a short fixed cinematic, made of drop-in, snap, walk, two
  surgical actions, zap and reveal, then a leap into the creature layer.
  It **resolves on hand-off** and rejects if cancelled (the scrap animation).
- `summon(def)`: the creature layer's turn, charge and scream, plus a
  scientist reaction.
- `maybeCleanup()`: bomb, acid and hose, only while the Lab is visible.
  Afterwards it calls back so a queued question can run.
- Camera shots are subject, direction and how much must fit (`fitH`/`fitW`),
  so they adapt to any stage aspect ratio.

### Monsters (`monsters/`)
- `buildMonster(seed, spec)`: seeded generator varying body type, legs,
  arms (mitten, claw, tentacle, bone), heads, eyes, mouths, patches,
  stitches, organs, horns, tails and so on. A topic **spec** biases palette,
  body plan and counts. `spec.decorate()` attaches signature props through
  generated **anchors** (`top`, `face`, `chest`, `handR`, `handL`).
- The body on the slab, the wandering counterpart, the den monster and the
  tab portrait are all rebuilt from the **same seed and topic**, so the
  monster being built looks like the one that comes out.
- `animateMonster(m, t, state)`: gait, blinks, chomping, plus blended
  performance verbs (`look`, `read`, `think`, `compute`, `write`, `speak`,
  `cheer`, `sad`, `greet`) and prop updaters (rain from the storm cloud,
  abacus beads, …).

---

## 3. Assistants, providers and workflows

An assistant (persisted in `state.monsters`):

```js
{
  id, theme,            // topic id, e.g. 'weather' (also drives the look)
  name, seed,           // e.g. 'Drizzlegut'; seed rebuilds the exact monster
  instructions,         // the assistant's system prompt (for a real model)
  provider: { kind: 'scripted', model: null, endpoint: null },
  chat: [ { id, role, content, status, steps: [{ id, label, kind, state }] } ],
  createdAt,
}
```

- **One assistant per topic.** `registry.findByTopic()`. A draft is
  committed only when creation completes.
- `ai/assistants.js → respond(assistant, input, { signal, history })` returns
  an async iterable of events:
  `{type:'step', step}`, `{type:'stepDone', step}`, `{type:'token', text}`.
  It must honour `signal` (throw `AbortError`) and throw on failure.
  `PROVIDERS[assistant.provider.kind]` selects the implementation. Today
  that's only `scripted` (`workflows/engine.js`).
- Workflow steps carry a **kind** (`gather | read | think | compute | write`),
  and that kind is what the monster acts out. A step may define
  `run(ctx, signal)` to do real work, for example calling the Frankenstein
  runtime, a Sokosumi job or an API, and stash results for `respond()`.

### Hooking up the real backend (suggested)
- Add `PROVIDERS.frankenstein`, which POSTs to the Frankenstein API
  (`python -m uvicorn frankenstein.api:app`). It maps the monster spec's
  steps to `step`/`stepDone` events and streams the `report` markdown as
  tokens. `speech` could go to ElevenLabs.
- Long Sokosumi jobs map naturally onto the den performance: emit a `step`
  when the job starts and `stepDone` when polling completes.
- Store per-assistant config in `assistant.provider`
  (`{ kind: 'frankenstein', monsterId, endpoint }`).

---

## 4. Topics (`monsters/themes.js`)

Weather · Meetings · Research · Writing · Math · Cooking · Travel · Health &
Fitness · Music · Tech & Code · History · Space & Science · Money · Sports ·
Movies & TV · Animals & Pets · Gardening · Languages · Love & Relationships ·
Games · Odd Jobs (fallback).

Each topic defines `keywords` (detection), `instructions`, naming parts,
`intro`, `placeholder`, and a visual `spec`. For example, **Weather** has
cloud-tuft anatomy on the torso, a raining storm-cloud head that flashes
lightning while working, optional lightning-bolt horns, an umbrella, and a
blue-grey palette. Variation within a topic comes from the seed.

Detection (`matchTheme`) scores keyword hits, with longer keywords weighted
higher and bare arithmetic counted as Math. Ties and no-hits fall back to
Odd Jobs.

**Adding a topic:**
1. Add a `T('id', 'Label', {...})` entry with keywords, names, intro and a
   spec. `kit({ head, face, chest, hand })` makes props one-liners.
2. Add a workflow in `workflows/workflows.js`. `topical({ steps, open, tips, close })` covers the common case.
3. Optionally add props in `themeProps.js` and a den set in `den/den.js`.

---

## 5. Persistence (`localStorage["stitchwick-lab.v1"]`)

```js
{
  monsters: [...assistants],                       // tabs + histories
  labCreatures: [{ id, seed, defId, theme, name, state: 'alive'|'dead', x, y, side }],
  mode: 'lab' | 'monsters', selectedMonster,
  settings: { threshold: 8, sound: false },        // sound always starts muted
}
```

A refresh restores assistants, tabs, conversations, the open tab, and the
wandering counterparts (and corpses) at their positions. Streams interrupted
by a refresh become `stopped`. Switching tabs never clears anything.

---

## 6. Debugging

- `?debug=stage` expands the active 3D stage to the full window.
- `window.lab` exposes `{ lab, creatures, director, ctrl, mon, den, overlay, renderer }`.
  - `lab.ctrl.send('Will it rain in Oslo?')`: a Lab question
  - `lab.mon.send('…')`: ask the open monster
  - `lab.director.maybeCleanup()` (lower the threshold first)
- The browser throttles `requestAnimationFrame` to about 5/s when the window
  is unfocused. Cinematics slow down accordingly. That's expected, not a bug.

## 7. Frankenstein integration, floor monsters and limbs

- **Backend:** run `python -m uvicorn frankenstein.api:app` from `frankenstein/` (port 8000). The Vite dev server proxies `/frank/*` to it (`FRANK_URL` overrides the target). The header chip shows *Frankenstein* when the API is reachable and *Offline · scripted* otherwise. Force offline with `?offline=1` or `localStorage["stitchwick-lab.offline"]="1"`.
- **Several tabs** share storage: each tab merges the others' assistants (by id) via the `storage` event, so a stale tab can't drop monsters created elsewhere.
- **Creation (real mode):** a new-topic Lab question → `POST /forge` with a brief asking for a single `question` input. The surgery keeps going until the forge returns. Then `publish` runs (voice only with `VITE_FRANK_VOICE=1`), each limb in the spec is delivered onto the slab and sewn on, there's lightning, and the monster leaps onto the floor. Forge errors (refused, invalid, or missing `OPENAI_API_KEY`) scrap the body and show the reason in the banner.
- **Answers (real mode):** `POST /monsters/{id}/runs` + SSE `/runs/{id}/events`. The UI infers running steps from the spec order and the run log. `needs_input` becomes a question callout answered from the composer, and `needs_confirmation` becomes an Approve/Decline card (credits are never spent without a click). `output.speech` is quoted (audio plays only if sound is on) and `output.report` streams as the answer. There's no cancel endpoint, so Stop only stops following the run. A JSON object typed into a monster tab is passed as raw inputs.
- **Limbs → body parts** (`src/monsters/limbParts.js`): web_search = telescope eye, http_fetch = grabber claw, llm = brain jar, forged:* = carved wooden limb, sokosumi = hired tentacle with a price tag, tts = gramophone horn, sfx = squeezebox, image = camera eye, notify = alarm bell. The matching part animates while its step runs. Scripted monsters get limbs implied by their workflow's step kinds.
- **Floor:** wandering counterparts are 3D actors on the lab floor (`src/lab/labCreatures.js`), with blob shadows, name-tag sprites and raycast clicking. The camera's resting shot pulls back to show the floor whenever monsters exist. Bomb and acid land in the crowd. The scientist then hoses the floor itself (`src/lab/water.js`): he turns round, picks the nozzle up off the hose coiled on the floor behind him, and sweeps a glossy ballistic stream (droplets breaking off) side to side across the floor with both hands, then drops it back on the coil. Where it lands, a wet film spreads and keeps flowing toward the camera (the bottom of the screen), darkening the floor with moving ripple glints. Blood, scorch marks and acid puddles under the flow are diluted and carried along until they wash off, and the film drains away after the hose stops.

## 8. Known limitations

- Answers are simulated. Workflow outputs are invented, except that Math
  really does the arithmetic. Each simulated answer says so.
- Topic detection is keyword-based. A real router (an LLM classifier) can
  replace `matchTheme()` without touching the choreography.
- The surgery's wounds and stitches exist only on the lab instance. The
  wandering and den copies are rebuilt from the seed with their generated
  stitches.
- Desktop-first. Narrow screens work, but they aren't the target.
