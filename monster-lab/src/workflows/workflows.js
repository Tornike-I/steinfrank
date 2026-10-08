// Simulated workflows, one per theme. Outputs are plausible but invented
// (the number cruncher really does the arithmetic). Swap any step for a real
// one by giving it a `run(ctx, signal)` and reading the result in respond().
import { Rng } from '../core/rng.js';

const hash = (s) => { let h = 2166136261; for (const c of s) h = Math.imul(h ^ c.charCodeAt(0), 16777619); return h >>> 0; };
const cap = (s) => s.charAt(0).toUpperCase() + s.slice(1);
const clean = (s) => s.replace(/[?!.]+$/, '').trim();

function after(input, words) {
  const m = input.match(new RegExp(`\\b(?:${words})\\s+(.+?)(?:\\s+(?:this|next|tomorrow|today|tonight|on|for|at)\\b|[?.!,]|$)`, 'i'));
  return m ? clean(m[1]) : null;
}

const NOTE = '\n\n*Simulated workflow output — no real data sources were consulted.*';

export const WORKFLOWS = {
  weather: {
    parse(input) {
      const place = after(input, 'in|at|near|around') || after(input, 'for') || clean(input).split(' ').slice(-2).join(' ') || 'your area';
      const when = (input.match(/\b(today|tonight|tomorrow|this weekend|weekend|next week|monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b/i) || [])[0] || 'the next few days';
      return { place: cap(place), when, rng: new Rng(hash(place.toLowerCase())) };
    },
    steps: (c) => [
      { id: 'sniff', label: `Sniffing the air over ${c.place}`, kind: 'gather', duration: [1100, 1700] },
      { id: 'baro', label: 'Tapping the barometer (it bit me)', kind: 'gather', duration: [900, 1400] },
      { id: 'radar', label: 'Reading the radar entrails', kind: 'read', duration: [1200, 1800] },
      { id: 'clouds', label: 'Arguing with the clouds', kind: 'think', duration: [1300, 2000] },
      { id: 'write', label: 'Scribbling the forecast', kind: 'write', duration: [700, 1100] },
    ],
    respond(c) {
      const r = c.rng;
      const conds = [['Drizzle', '🌧'], ['Sunny spells', '🌤'], ['Overcast', '☁'], ['Thunderstorms', '⛈'], ['Fog', '🌫'], ['Clear', '☀'], ['Showers', '🌦']];
      const base = r.int(6, 24);
      const days = ['Today', 'Tomorrow', 'Day after'].map((d, i) => {
        const [cond, icon] = r.pick(conds);
        return { d, cond, icon, hi: base + r.int(-2, 5) + i, lo: base - r.int(4, 9), rain: r.int(0, 90), wind: r.int(3, 35) };
      });
      const wet = days.some((x) => x.rain > 55);
      return `### ${days[0].icon} ${c.place} — ${c.when}

My left nostril says **${days[0].cond.toLowerCase()}**, my right nostril says **${days[1].cond.toLowerCase()}**. I trust the left one more.

| Day | Sky | High | Low | Rain | Wind |
| --- | --- | --- | --- | --- | --- |
${days.map((x) => `| ${x.d} | ${x.icon} ${x.cond} | ${x.hi}°C | ${x.lo}°C | ${x.rain}% | ${x.wind} km/h |`).join('\n')}

**Advice:** ${wet ? 'Bring an umbrella. Preferably one without holes, unlike mine.' : 'Leave the umbrella. Bring sunglasses, or a hat to hide your shame.'}${NOTE}`;
    },
  },

  dossier: {
    parse(input) {
      const who = after(input, 'with|for|on|about') || clean(input);
      return { subject: cap(who), rng: new Rng(hash(who.toLowerCase())) };
    },
    steps: (c) => [
      { id: 'invite', label: 'Pulling the calendar invite', kind: 'gather', duration: [900, 1400] },
      { id: 'people', label: 'Looking up the attendees', kind: 'read', duration: [1300, 1900] },
      { id: 'notes', label: 'Rummaging through old meeting notes', kind: 'read', duration: [1200, 1800] },
      { id: 'cross', label: 'Cross-referencing everyone’s grudges', kind: 'think', duration: [1300, 1900] },
      { id: 'assemble', label: 'Stapling the dossier together', kind: 'write', duration: [800, 1200] },
    ],
    respond(c) {
      const r = c.rng;
      const first = ['Mara', 'Theo', 'Priya', 'Jonas', 'Lena', 'Kofi', 'Ines', 'Davit'];
      const last = ['Okafor', 'Lindqvist', 'Beridze', 'Moreau', 'Tanaka', 'Novak', 'Haddad'];
      const roles = ['Head of Product', 'CFO', 'Engineering Lead', 'Account Director', 'Design Lead', 'Procurement'];
      const people = Array.from({ length: r.int(2, 4) }, () => `**${r.pick(first)} ${r.pick(last)}** — ${r.pick(roles)}`);
      const h = r.int(9, 16);
      return `## Dossier: ${c.subject}

**When:** ${['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday'][r.int(0, 4)]}, ${h}:00–${h}:45 · **Where:** the room with the broken projector

### Attendees
${people.map((p) => `- ${p}`).join('\n')}

### Context
- Last time this group met, a decision was "parked". It is still parked. It has grown moss.
- One attendee has asked the same question in three consecutive meetings. Have the answer ready.

### Talking points
1. Open with the one number everyone agrees on.
2. Name the decision you need, before minute 10.
3. Leave five minutes for the inevitable "one more thing".

### Questions to ask
- What would make this a clear yes?
- Who else needs to be in the room next time?${NOTE}`;
    },
  },

  research: {
    parse: (input) => ({ topic: clean(input.replace(/^(research|find|look up|compare|tell me about|what do we know about)\s+/i, '')) || 'that', rng: new Rng(hash(input)) }),
    steps: (c) => [
      { id: 'search', label: `Searching the archives for “${c.topic.slice(0, 40)}”`, kind: 'gather', duration: [1100, 1600] },
      { id: 'skim', label: `Skimming ${c.rng.int(9, 31)} dusty sources`, kind: 'read', duration: [1600, 2400] },
      { id: 'weigh', label: 'Weighing the evidence on my tongue', kind: 'think', duration: [1300, 1900] },
      { id: 'draft', label: 'Drafting the summary', kind: 'write', duration: [800, 1200] },
    ],
    respond(c) {
      return `## ${cap(c.topic)}

**Short version:** the evidence is interesting, mostly consistent, and occasionally written in something that is not ink.

### What the sources agree on
- The core idea is well established and has been studied for decades.
- Most disagreement is about *degree*, not direction.

### Where they argue
- Older sources overstate the effect; newer ones are more cautious.
- One source contradicts everything and smells faintly of smoke. I have discounted it.

### Worth reading next
1. A recent review article (start there).
2. One primary study with a decent sample size.
3. A sceptical counterpoint, to keep yourself honest.${NOTE}`;
    },
  },

  writer: {
    parse: (input) => ({ brief: clean(input) }),
    steps: () => [
      { id: 'chew', label: 'Chewing the quill', kind: 'think', duration: [1000, 1500] },
      { id: 'voice', label: 'Finding the right voice', kind: 'think', duration: [1100, 1700] },
      { id: 'draft', label: 'Drafting in blood-red ink', kind: 'write', duration: [1400, 2000] },
      { id: 'polish', label: 'Polishing the adjectives', kind: 'read', duration: [900, 1300] },
    ],
    respond(c) {
      const b = c.brief.toLowerCase();
      if (/haiku|poem/.test(b)) {
        return `*${cap(c.brief)}*

> The kettle screams first,
> then the inbox, then the cat —
> I scream last of all.

I can do another, angrier one. My quill is very angry.`;
      }
      if (/email|reply|decline/.test(b)) {
        return `**Subject:** Re: your invitation

Hi there,

Thank you so much for thinking of me. Unfortunately I won't be able to make this one — my calendar is fully booked with prior commitments (several of them are lurching).

I'd love to stay in the loop, though. Could you send over any notes afterwards?

Warm regards,
*Your name*`;
      }
      return `**${cap(c.brief)}**

Some things are made in factories. This one was stitched together by hand, at midnight, during a thunderstorm — and it shows, in all the right ways. Sturdy, a little strange, and impossible to forget.

*Want it shorter, punchier, or more unhinged?*`;
    },
  },

  numbers: {
    parse(input) {
      return { input, result: crunch(input) };
    },
    steps: () => [
      { id: 'fingers', label: 'Counting on all my fingers', kind: 'compute', duration: [900, 1300] },
      { id: 'run', label: 'Running the numbers', kind: 'compute', duration: [1200, 1700] },
      { id: 'check', label: 'Double-checking with the abacus', kind: 'compute', duration: [900, 1400] },
      { id: 'format', label: 'Writing it down neatly', kind: 'write', duration: [600, 900] },
    ],
    respond(c) {
      const res = c.result;
      if (!res) return `I couldn't find a sum in that. Give me something like \`17% of 2450\`, \`186 / 4\` or \`(42 * 7) / 3 + 12\` and I'll crunch it.`;
      return `**${res.expr} = ${res.value}**

${res.note || 'Checked twice on the abacus. The red beads agree.'}`;
    },
  },

  generic: {
    parse: (input) => ({ topic: clean(input) }),
    steps: () => [
      { id: 'ponder', label: 'Pondering with all my brains', kind: 'think', duration: [1100, 1600] },
      { id: 'rummage', label: 'Rummaging for something useful', kind: 'gather', duration: [1000, 1500] },
      { id: 'write', label: 'Writing it down', kind: 'write', duration: [700, 1000] },
    ],
    respond: (c) => `Here's my best attempt at **${c.topic || 'that'}**:

- Start small, with the part you're most sure of.
- Ask one person for feedback before you polish anything.
- If it goes wrong, it was an experiment. If it goes right, it was the plan all along.

I'm an odd-job monster — give me a more specific job description in the Lab and I'll grow the right organs for it.`,
  },
};

// Tiny, safe arithmetic: digits, + - * / ( ) . and "x% of y". No eval.
function crunch(text) {
  const t = text.replace(/,/g, '').replace(/\$/g, '');
  const pct = t.match(/(\d+(?:\.\d+)?)\s*%\s*of\s*(\d+(?:\.\d+)?)/i);
  if (pct) return { expr: `${pct[1]}% of ${pct[2]}`, value: fmt((+pct[1] / 100) * +pct[2]) };
  const split = t.match(/split\D*(\d+(?:\.\d+)?)\D+(\d+)\s*(?:ways|people)/i);
  if (split) return { expr: `${split[1]} ÷ ${split[2]}`, value: fmt(+split[1] / +split[2]), note: 'Each person pays that. Round up, for the tip and for the waiter’s soul.' };
  const m = t.match(/[\d.(][\d.\s+\-*/()x×÷]*[\d)]/);
  if (!m) return null;
  const expr = m[0].replace(/[x×]/g, '*').replace(/÷/g, '/').trim();
  try {
    const v = parseExpr(expr);
    return Number.isFinite(v) ? { expr, value: fmt(v) } : null;
  } catch { return null; }
}

function fmt(v) { return (Math.round(v * 1000) / 1000).toLocaleString('en-US'); }

function parseExpr(src) {
  const toks = src.match(/\d+(?:\.\d+)?|[+\-*/()]/g) || [];
  let i = 0;
  const peek = () => toks[i];
  const num = () => {
    const t = toks[i++];
    if (t === '(') { const v = add(); i++; return v; }
    if (t === '-') return -num();
    if (t === undefined || Number.isNaN(+t)) throw new Error('bad');
    return +t;
  };
  const mul = () => { let v = num(); while (peek() === '*' || peek() === '/') { const op = toks[i++]; const r = num(); v = op === '*' ? v * r : v / r; } return v; };
  const add = () => { let v = mul(); while (peek() === '+' || peek() === '-') { const op = toks[i++]; const r = mul(); v = op === '+' ? v + r : v - r; } return v; };
  const v = add();
  if (i < toks.length) throw new Error('trailing');
  return v;
}

// ------------------------------------------------- wider topic roster ----
// Compact scripted workflows: a few themed steps, then an in-character answer
// that quotes the question. Real providers will replace these per assistant.
function topical({ steps, open, tips, close }) {
  return {
    parse: (input) => ({ q: clean(input), rng: new Rng(hash(input)) }),
    steps: () => steps.map(([label, kind], i) => ({ id: `s${i}`, label, kind, duration: [900, 1600] })),
    respond(c) {
      const r = c.rng;
      const picks = [...tips].sort(() => r.next() - 0.5).slice(0, 3);
      return `${open(c.q)}

${picks.map((t, i) => `${i + 1}. ${t}`).join('\n')}

${close}${NOTE}`;
    },
  };
}

Object.assign(WORKFLOWS, {
  cooking: topical({
    steps: [['Raiding the pantry', 'gather'], ['Sniffing the spice rack', 'gather'], ['Tasting the stew (it tasted back)', 'think'], ['Writing the recipe', 'write']],
    open: (q) => `**${cap(q)}** — the cauldron approves. Here's how I'd do it:`,
    tips: ['**Prep everything first.** Chop, measure and line it all up before the heat goes on.', '**Salt in layers**, a pinch at each stage, not a landslide at the end.', '**Brown things properly.** Colour is flavour; pale food is sad food.', '**Taste as you go** and adjust with acid — a squeeze of lemon fixes most regrets.', '**Rest the meat** for a few minutes so the juices stay inside, where they belong.', '**Double the garlic.** It has never once been too much.'],
    close: 'Serve hot. Lock the pantry. Something in there is learning to open jars.',
  }),
  travel: topical({
    steps: [['Unfolding the cursed map', 'read'], ['Checking the timetables', 'gather'], ['Plotting the route', 'think'], ['Packing your itinerary', 'write']],
    open: (q) => `Ah, **${cap(q)}**. My suitcase is already twitching. A rough plan:`,
    tips: ['**Arrive, then walk.** The first afternoon is for wandering, not schedules.', '**Book the one thing that sells out** (a museum, a train, a table) before anything else.', '**Stay central** even if the room is small; you’ll trade it for hours of travel.', '**Eat where the locals queue**, not where the menu has photographs.', '**Keep one day unplanned.** The best stories happen there.', '**Photograph your passport** and email it to yourself. Trust no pocket.'],
    close: 'Bon voyage. Bring back a souvenir. Preferably not a curse.',
  }),
  health: topical({
    steps: [['Taking your pulse (from here)', 'gather'], ['Consulting the anatomy charts', 'read'], ['Flexing thoughtfully', 'think'], ['Writing your regimen', 'write']],
    open: (q) => `On **${q}** — general guidance only; for anything worrying, see a real doctor (one with fewer stitches than me).`,
    tips: ['**Sleep is the cheapest medicine.** Aim for a regular bedtime, not just more hours.', '**Start smaller than you think.** Ten minutes daily beats an hour once a month.', '**Drink water before coffee.** Your organs will file fewer complaints.', '**Protein at every meal** helps muscles recover after you torment them.', '**Warm up** — cold muscles tear like old sutures.', '**Walk after eating.** Even fifteen minutes helps.'],
    close: 'Stay limber. Keep all your limbs attached.',
  }),
  music: topical({
    steps: [['Tuning my vocal cords', 'gather'], ['Flipping through records', 'read'], ['Humming possibilities', 'think'], ['Composing an answer', 'write']],
    open: (q) => `*${cap(q)}* — music to my ears (both pairs). My thoughts:`,
    tips: ['**Practice slowly first.** Speed is just accuracy that got impatient.', '**Learn three chords** and you can play a terrifying number of songs.', '**Build playlists like a story** — a beginning, a peak, and a quiet end.', '**Listen to the bass line.** It’s where the secrets live.', '**Record yourself.** Painful, but nothing teaches faster.', '**Play with other people** as early as possible.'],
    close: 'Now, if you’ll excuse me, I need to scream in four-part harmony.',
  }),
  tech: topical({
    steps: [['Rebooting my brain', 'compute'], ['Reading the stack traces', 'read'], ['Bisecting the bug', 'compute'], ['Typing the fix', 'write']],
    open: (q) => `\`${q}\` — let me plug my antennae in. Here's what I'd try:`,
    tips: ['**Reproduce it reliably** before changing anything. A bug you can’t repeat is a ghost.', '**Read the actual error message**, top to bottom. It usually tells you.', '**Change one thing at a time** and test after each.', '**Turn it off and on again.** Laugh, but do it.', '**Check the versions** — half of all bugs are two libraries disagreeing.', '**Write a tiny test** that fails, then make it pass.'],
    close: 'If it still breaks, it’s haunted. That is technically a valid diagnosis.',
  }),
  history: topical({
    steps: [['Dusting off the chronicles', 'read'], ['Interrogating a skull', 'gather'], ['Weighing the accounts', 'think'], ['Inking the summary', 'write']],
    open: (q) => `**${cap(q)}** — I was there. Probably. Here's the short history:`,
    tips: ['**Context matters most.** People acted on what they knew, not what we know now.', '**Follow the money and the food.** Empires rise and fall on supply lines.', '**Primary sources disagree.** The winners wrote louder, not truer.', '**Dates are scaffolding;** the causes are the building.', '**Technology changed everything quietly** — stirrups, printing, steam.', '**Ordinary lives** tell you more than any king’s portrait.'],
    close: 'History doesn’t repeat, but it does reanimate occasionally.',
  }),
  space: topical({
    steps: [['Polishing the telescope', 'gather'], ['Reading the star charts', 'read'], ['Doing orbital maths', 'compute'], ['Transmitting the answer', 'write']],
    open: (q) => `**${cap(q)}** — cosmic. Here's the gist from my fishbowl:`,
    tips: ['**Space is mostly empty.** Even galaxies colliding rarely hit a single star.', '**Light is slow at cosmic scale** — you see the Sun as it was eight minutes ago.', '**Gravity is a curve, not a pull,** at least according to Einstein.', '**Scale breaks intuition:** if Earth were a marble, the Moon would be a pea about 75 cm away.', '**Every heavy element** in your body was forged in a star.', '**Experiments beat opinions** — science is just organised arguing with evidence.'],
    close: 'Keep looking up. Something up there is looking back.',
  }),
  money: topical({
    steps: [['Counting my coins', 'compute'], ['Reading the ledgers', 'read'], ['Calculating compound interest', 'compute'], ['Writing the plan', 'write']],
    open: (q) => `On **${q}** — general information, not financial advice. I am a monster with a top hat.`,
    tips: ['**Know where it goes.** Track a month of spending before making any rules.', '**Pay yourself first** — move savings out on payday, before temptation.', '**Build a small emergency fund** before chasing returns.', '**High-interest debt first.** It grows faster than nearly any investment.', '**Low fees, long time, broad funds** is boring, and boring works.', '**Automate it** so willpower isn’t required.'],
    close: 'Spend wisely. Hoard sensibly. Bite anyone who touches the sack.',
  }),
  sports: topical({
    steps: [['Checking the scoreboard', 'gather'], ['Reviewing the tapes', 'read'], ['Drawing up tactics', 'think'], ['Writing the game plan', 'write']],
    open: (q) => `**${cap(q)}** — game on. My coaching notes:`,
    tips: ['**Fundamentals win** more games than highlight moves.', '**Defence travels;** good nights shooting don’t.', '**Rest is training too** — fatigue loses close matches.', '**Watch the off-ball movement.** That’s where games are decided.', '**Momentum is real but short.** Call the timeout.', '**Form matters more than reputation** in any single match.'],
    close: 'May your team win and your knees survive.',
  }),
  movies: topical({
    steps: [['Rewinding the tapes', 'gather'], ['Reading the reviews', 'read'], ['Munching thoughtfully', 'think'], ['Writing the verdict', 'write']],
    open: (q) => `*${cap(q)}* — *crunch* — excellent question. My picks and thoughts:`,
    tips: ['**Go in blind** when you can; trailers spoil more than they used to.', '**Watch the first ten minutes twice** — good films plant everything there.', '**Pick by director, not poster.**', '**Subtitles over dubbing** for most foreign films.', '**Rewatch your favourites** every few years; you’ll have changed.', '**Double features** work best with contrasting moods.'],
    close: 'Lights down. Phones off. Popcorn guarded with my life.',
  }),
  animals: topical({
    steps: [['Sniffing around', 'gather'], ['Consulting the bestiary', 'read'], ['Thinking like an animal', 'think'], ['Writing it down', 'write']],
    open: (q) => `**${cap(q)}** — oh, I *love* animals. Some notes:`,
    tips: ['**Routine calms most pets** — same food, same walks, same times.', '**Reward what you want**, ignore what you don’t; punishment teaches fear.', '**Watch body language:** ears, tails and posture say more than noise.', '**Enrichment matters** — puzzles and new smells keep minds sharp.', '**Regular vet checks** catch problems early.', '**Give wild animals space.** Admire, don’t approach.'],
    close: 'Give your pet a treat from me. Not a bone. I need those.',
  }),
  gardening: topical({
    steps: [['Digging in the dirt', 'gather'], ['Reading the seed packets', 'read'], ['Pondering the seasons', 'think'], ['Writing the care plan', 'write']],
    open: (q) => `**${cap(q)}** — the soil whispers to me. Here's what it says:`,
    tips: ['**Right plant, right place.** Light matters more than love.', '**Water deeply, less often,** so roots grow down.', '**Most houseplants die of overwatering.** Check the soil with a finger first.', '**Mulch** keeps moisture in and weeds out.', '**Feed in the growing season,** rest in winter.', '**Prune the dead bits** so the plant spends energy on the living.'],
    close: 'Grow wild. Something in my pot already has teeth.',
  }),
  language: topical({
    steps: [['Consulting my tongues', 'gather'], ['Flipping the dictionaries', 'read'], ['Conjugating furiously', 'think'], ['Writing the answer', 'write']],
    open: (q) => `**${cap(q)}** — ah, words! My favourite organs. Here's how I'd approach it:`,
    tips: ['**Learn phrases, not word lists.** Chunks stick better than single words.', '**Speak badly, early.** Fluency comes from mistakes made out loud.', '**Ten minutes a day** beats two hours on Sunday.', '**Shadow native audio** — repeat it right behind the speaker.', '**Learn the 300 most common words** first; they cover most of conversation.', '**Context over translation** — many words don’t map one-to-one.'],
    close: 'Gamarjoba, ¡hola, bonjour! I said all three with different mouths.',
  }),
  love: topical({
    steps: [['Listening to my heart(s)', 'gather'], ['Rereading old love letters', 'read'], ['Sighing deeply', 'think'], ['Writing heartfelt advice', 'write']],
    open: (q) => `Oh, **${q}**. *Clutches rose.* Here's my tender, slightly decomposed advice:`,
    tips: ['**Say the honest thing kindly.** Both parts matter.', '**Ask more questions** than you answer on a first date.', '**Small consistent gestures** beat grand rare ones.', '**Listen to understand,** not to reply.', '**Respect a no** the first time.', '**Keep your own friends and hobbies** — whole people make better partners.'],
    close: 'Love is just two monsters agreeing to share a slab.',
  }),
  games: topical({
    steps: [['Loading the save file', 'gather'], ['Reading the strategy guide', 'read'], ['Planning the combo', 'compute'], ['Writing the walkthrough', 'write']],
    open: (q) => `**${cap(q)}** — player one ready. My strategy:`,
    tips: ['**Learn the fundamentals** before the flashy tech.', '**Watch a replay of your losses**, not your wins.', '**Control the centre** — it works in chess and in most arenas.', '**Take breaks.** Tilted players lose streaks.', '**Read the patch notes**; the meta changes under your feet.', '**Play with friends** — co-op teaches faster than solo grinding.'],
    close: 'Good game. Rematch at midnight.',
  }),
});
WORKFLOWS.meetings = WORKFLOWS.dossier;
WORKFLOWS.writing = WORKFLOWS.writer;
WORKFLOWS.math = WORKFLOWS.numbers;
