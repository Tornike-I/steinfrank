// Topics. Each topic is one persistent assistant ("topic monster"). A topic
// supplies:
//   - keywords for topic detection from a laboratory question
//   - assistant instructions (its system prompt, for a future real model)
//   - a visual spec for the generator: palette, body plan, signature props
//   - naming parts and copy for its tab
// Only one assistant exists per topic; questions on that topic reuse it.
import * as P from './themeProps.js';

const hs = (a) => Math.min(a.faceRad.x, a.faceRad.y);
const Hof = (api) => api.m.height / api.m.scale;

// Small helper so most topics are a head prop + a held prop (+ extras).
function kit({ head, face, chest, hand, prefer, extra }) {
  return (api) => {
    const a = api.anchors, H = Hof(api);
    if (head) a.top.add(head(api, a, H));
    if (face) a.face.add(face(api, a, H));
    if (chest) a.chest.add(chest(api, a, H));
    const held = hand ? P.holdProp(api, hand(api, a, H), { prefer }) : null;
    api.m.props = { held };
    extra?.(api, a, H, held);
  };
}

const T = (id, label, o) => ({
  id, label,
  keywords: o.keywords,
  names: o.names,
  intro: o.intro,
  placeholder: o.placeholder || `Ask me anything about ${label.toLowerCase()}…`,
  instructions: o.instructions || `You are the ${label} monster. Only answer questions about ${label.toLowerCase()}. Politely redirect anything else to the Laboratory.`,
  workflow: o.workflow || id,
  spec: { ...o.spec, minArms: o.spec?.minArms ?? 1, handArms: true },
});

export const THEMES = Object.fromEntries([
  T('weather', 'Weather', {
    keywords: ['weather', 'forecast', 'rain', 'temperature', 'climate', 'storm', 'sunny', 'wind', 'snow', 'umbrella', 'humid', 'cold outside', 'hot outside'],
    names: [['Drizzle', 'Sleet', 'Fog', 'Squall', 'Murk', 'Hail', 'Gust'], ['gut', 'snout', 'belly', 'wort', 'grub', 'maw']],
    intro: 'sniffs the air and squints at the sky.',
    placeholder: 'Ask about the weather anywhere…',
    spec: {
      palette: [0x7f9fb8, 0x9fb4c4, 0x6a8aa8, 0xb8c8d4, 0x5f7d9e, 0x86a0b6],
      bodies: [['blob', 4], ['pear', 2], ['stack', 1]],
      heads: [[1, 5], [0, 1]],
      eyes: [[1, 2], [2, 3], [3, 1]],
      tops: [['none', 1]],
      decorate(api) {
        const a = api.anchors, H = Hof(api);
        // Cloud-like anatomy, a raining storm cloud for hair, lightning horns.
        api.m.torso.add(P.cloudPuffs(api, a.torsoRadii, api.r.int(4, 7)));
        const cloud = P.stormCloud(api, hs(a) * 0.55);
        a.top.add(cloud);
        if (api.r.chance(0.6)) a.top.add(P.lightningHorns(a.faceRad));
        api.m.props = { cloud, held: P.holdProp(api, api.r.chance(0.7) ? P.umbrella(H) : P.ladle(H)) };
      },
    },
  }),
  T('meetings', 'Meetings', {
    keywords: ['meeting', 'dossier', 'brief', 'briefing', 'calendar', 'attendee', 'agenda', 'prep me', 'call with', 'interview', 'client', 'standup', 'presentation'],
    names: [['Madame ', 'Clerk ', 'Agent ', 'Auditor '], ['Minutes', 'Agendra', 'Paperjaw', 'Memorandum', 'Folderbelly']],
    intro: 'adjusts its spectacles and opens a bulging folder.',
    placeholder: 'Which meeting should I prepare you for?',
    workflow: 'dossier',
    spec: {
      palette: [0xc9b896, 0xa8a088, 0x8a8f78, 0xd8c8a8, 0x9a8a70, 0xb8a890],
      bodies: [['tall', 5], ['pear', 2], ['stack', 1]], heads: [[1, 6], [2, 1]], eyes: [[2, 6], [1, 1], [3, 1]], tops: [['none', 3], ['tuft', 1]], minArms: 2,
      decorate: kit({
        face: (api, a) => P.glasses(a.faceRad),
        chest: (api, a) => P.tie(api, a.torsoRadii),
        hand: (api, a, H) => P.folder(H), prefer: 'L',
        extra: (api, a, H) => { const pen = P.pencil(H); pen.position.set(a.faceRad.x * 0.9, 0, -a.faceRad.z * 0.2); pen.rotation.z = 1.2; a.top.add(pen); },
      }),
    },
  }),
  T('research', 'Research', {
    keywords: ['research', 'investigate', 'study', 'sources', 'evidence', 'compare', 'archive', 'what do we know', 'look into'],
    names: [['Gristle ', 'Old ', 'Professor ', 'Brother '], ['Footnote', 'Archivix', 'Indexgut', 'Bibliomaw', 'Citation']],
    intro: 'peers at you through a cracked magnifying glass.',
    spec: {
      palette: [0x9a7fc4, 0x8a6aa0, 0xb48aa8, 0x7a6a8a, 0xa890b8],
      bodies: [['pear', 3], ['blob', 2], ['stack', 2]], heads: [[1, 5], [2, 1]], eyes: [[3, 2], [4, 2], [5, 1], [2, 1]], tops: [['none', 1]],
      decorate: kit({ head: (api, a) => P.bookStack(api, hs(a) * 0.7), hand: (api, a, H) => P.magnifier(H) }),
    },
  }),
  T('writing', 'Writing', {
    keywords: ['write', 'writing', 'draft', 'poem', 'story', 'email', 'copy', 'blog', 'essay', 'letter', 'caption', 'haiku', 'rewrite'],
    names: [['Ink', 'Scrib', 'Quill', 'Blot', 'Verse'], ['wretch', 'gob', 'snarl', 'ling', 'mire']],
    intro: 'chews its quill and stares into the middle distance.',
    placeholder: 'What should I write?',
    workflow: 'writer',
    spec: {
      palette: [0x5f6f9e, 0x4a5a8a, 0x7a88b8, 0x6a6a9a, 0xe2cdb4],
      bodies: [['tall', 3], ['blob', 2], ['pear', 2]], heads: [[1, 1]], eyes: [[2, 3], [1, 1]], tops: [['none', 1]],
      decorate: kit({ head: (api, a) => P.beret(api, a.faceRad), hand: (api, a, H) => P.quill(H) }),
    },
  }),
  T('math', 'Math', {
    keywords: ['math', 'calculate', 'calculator', 'percent', '%', 'sum', 'divide', 'multiply', 'equation', 'split the bill', 'split a', 'convert'],
    names: [['Abacus ', 'Tally ', 'Digit ', 'Ledger '], ['Gorgle', 'Crunchjaw', 'Sumgut', 'Remainder', 'Fractus']],
    intro: 'clacks its abacus beads impatiently.',
    placeholder: 'Give me some numbers to crunch…',
    workflow: 'numbers',
    spec: {
      palette: [0x8fae5e, 0xa8c27a, 0x6f9488, 0x7cb8a0, 0x9ab86a],
      bodies: [['bean', 2], ['blob', 3], ['stack', 1]], heads: [[1, 4], [2, 1]], eyes: [[2, 2], [4, 1], [6, 1]], tops: [['none', 1]], minArms: 2,
      decorate(api) {
        const a = api.anchors, H = Hof(api);
        a.top.add(P.visor(api, a.faceRad));
        a.face.add(P.glasses(a.faceRad));
        const held = P.holdProp(api, P.abacus(H));
        api.m.props = { held };
        const beads = held.obj.userData.beads;
        api.m.updaters.push((t, s) => {
          const speed = (s.intensity || 0) * 14;
          for (const b of beads) {
            const flick = speed ? Math.max(0, Math.sin(t * speed + b.userData.row * 1.7 + b.userData.k)) : 0;
            b.position.x = b.userData.base + flick * held.obj.userData.w * 0.45;
          }
        });
      },
    },
  }),
  T('cooking', 'Cooking', {
    keywords: ['cook', 'recipe', 'bake', 'dinner', 'lunch', 'breakfast', 'food', 'meal', 'ingredient', 'pasta', 'soup', 'oven', 'kitchen', 'eat'],
    names: [['Gravy', 'Stew', 'Gristle', 'Broth', 'Mince'], ['gob', 'belly', 'paunch', 'ladle', 'chops']],
    intro: 'stirs something that is definitely still moving.',
    placeholder: 'What are we cooking?',
    spec: {
      palette: [0xd8a46a, 0xc8845a, 0xe0b880, 0xb06a4a, 0xd89a6a],
      bodies: [['blob', 4], ['pear', 3]], heads: [[1, 1]], eyes: [[2, 3], [3, 1]], tops: [['none', 1]],
      decorate: kit({ head: (api, a) => P.chefHat(api, a.faceRad), hand: (api, a, H) => P.ladle(H) }),
    },
  }),
  T('travel', 'Travel', {
    keywords: ['travel', 'trip', 'flight', 'hotel', 'visit', 'vacation', 'holiday', 'itinerary', 'passport', 'sightseeing', 'tourist', 'backpack'],
    names: [['Wander', 'Roam', 'Trek', 'Nomad'], ['gut', 'snout', 'foot', 'lurch']],
    intro: 'dusts off a suitcase covered in suspicious stickers.',
    placeholder: 'Where are we going?',
    spec: {
      palette: [0xc8b07a, 0xa89060, 0x8aa07a, 0xb8a888],
      bodies: [['tall', 3], ['stack', 2], ['pear', 1]], legs: [[2, 5], [4, 1]], heads: [[1, 1]], eyes: [[2, 3], [1, 1]], tops: [['none', 1]],
      decorate: kit({ head: (api, a) => P.pithHelmet(api, a.faceRad), hand: (api, a, H) => P.suitcase(H) }),
    },
  }),
  T('health', 'Health & Fitness', {
    keywords: ['health', 'fitness', 'workout', 'exercise', 'sleep', 'diet', 'doctor', 'symptom', 'gym', 'stretch', 'running', 'calories', 'protein', 'headache'],
    names: [['Pulse', 'Sinew', 'Vein', 'Marrow'], ['gut', 'flex', 'thump', 'grunt']],
    intro: 'flexes something that should not be flexible.',
    placeholder: 'Ask about health, sleep or exercise…',
    spec: {
      palette: [0xd87a7a, 0xe8a0a0, 0xc86a6a, 0xd8a090],
      bodies: [['tall', 3], ['blob', 2]], arms: [[2, 4], [4, 2]], heads: [[1, 1]], eyes: [[2, 3]], tops: [['none', 1]], minArms: 2,
      decorate: kit({ head: (api, a) => P.sweatband(api, a.faceRad), chest: (api, a) => P.stethoscope(api, a.torsoRadii), hand: (api, a, H) => P.dumbbell(H) }),
    },
  }),
  T('music', 'Music', {
    keywords: ['music', 'song', 'band', 'album', 'playlist', 'guitar', 'piano', 'sing', 'concert', 'lyrics', 'chord', 'melody', 'drum'],
    names: [['Bass', 'Croon', 'Hum', 'Shriek', 'Riff'], ['gob', 'throat', 'warble', 'bellow']],
    intro: 'hums a tune in a key that doesn’t exist.',
    placeholder: 'Ask about music…',
    spec: {
      palette: [0xb06ad8, 0x8a5ac0, 0xd87ab8, 0x6a5ab0],
      bodies: [['blob', 2], ['stack', 2], ['tall', 2]], heads: [[1, 4], [2, 1]], eyes: [[2, 2], [1, 1]], tops: [['none', 1]],
      decorate: kit({ head: (api, a) => { const g = P.headphones(a.faceRad); g.add(P.noteAntenna(a.faceRad)); return g; } }),
    },
  }),
  T('tech', 'Tech & Code', {
    keywords: ['code', 'coding', 'program', 'bug', 'javascript', 'python', 'computer', 'software', 'app', 'website', 'laptop', 'wifi', 'api', 'html', 'css', 'database'],
    names: [['Byte', 'Glitch', 'Kernel', 'Segfault'], ['gnaw', 'gut', 'crawler', 'muncher']],
    intro: 'blinks its antenna bulbs in binary.',
    placeholder: 'Ask about code, computers or apps…',
    spec: {
      palette: [0x6a9a8a, 0x5a8a9a, 0x7aa8a0, 0x4a7a7a],
      bodies: [['bean', 2], ['blob', 2], ['stack', 1]], legs: [[4, 3], [6, 2], [2, 1]], heads: [[1, 3], [0, 1]], eyes: [[1, 2], [3, 2], [6, 1]], tops: [['none', 1]],
      decorate: kit({ head: (api, a) => P.bulbAntennae(a.faceRad), hand: (api, a, H) => P.laptop(H) }),
    },
  }),
  T('history', 'History', {
    keywords: ['history', 'ancient', 'war', 'empire', 'century', 'medieval', 'king', 'queen', 'historical', 'roman', 'viking', 'pharaoh', 'revolution'],
    names: [['Grim', 'Old', 'Rust', 'Dusty'], ['beard', 'skull', 'bones', 'relic']],
    intro: 'unrolls a scroll that crumbles slightly.',
    placeholder: 'Ask about the past…',
    spec: {
      palette: [0xa8987a, 0x8a7a6a, 0xb8a88a, 0x9a8a70],
      bodies: [['stack', 3], ['tall', 2]], heads: [[1, 1]], eyes: [[2, 3], [1, 1]], tops: [['none', 1]],
      decorate: kit({ head: (api, a) => P.vikingHelmet(api, a.faceRad), hand: (api, a, H) => P.scroll(H) }),
    },
  }),
  T('space', 'Space & Science', {
    keywords: ['space', 'planet', 'star', 'galaxy', 'nasa', 'moon', 'mars', 'jupiter', 'saturn', 'venus', 'comet', 'asteroid', 'astronaut', 'telescope', 'science', 'physics', 'chemistry', 'universe', 'black hole', 'atom', 'gravity', 'rocket'],
    names: [['Nebula', 'Quasar', 'Orbit', 'Comet'], ['gut', 'snot', 'blob', 'oid']],
    intro: 'floats a little, then remembers gravity.',
    placeholder: 'Ask about space or science…',
    spec: {
      palette: [0x5a6ad8, 0x3a4aa8, 0x7a5ad8, 0x4a8ad8],
      bodies: [['blob', 3], ['bean', 1], ['pear', 1]], heads: [[1, 1]], eyes: [[3, 2], [5, 1], [1, 1]], tops: [['none', 1]],
      decorate: kit({ head: (api, a) => P.fishbowlHelmet(a.faceRad), hand: (api, a, H) => P.rocket(H) }),
    },
  }),
  T('money', 'Money', {
    keywords: ['money', 'invest', 'stock', 'saving', 'budget', 'bank', 'finance', 'crypto', 'salary', 'loan', 'mortgage', 'tax', 'debt', 'price'],
    names: [['Coin', 'Penny', 'Ledger', 'Miser'], ['purse', 'gut', 'grub', 'sack']],
    intro: 'counts its coins, then counts them again.',
    placeholder: 'Ask about money, saving or budgets…',
    spec: {
      palette: [0x7a9a5a, 0x5a7a4a, 0xa8b86a, 0x8aa86a],
      bodies: [['pear', 4], ['blob', 2]], heads: [[1, 1]], eyes: [[2, 3], [1, 1]], tops: [['none', 1]],
      decorate: kit({ head: (api, a) => P.topHat(api, a.faceRad), face: (api, a) => P.glasses(a.faceRad), hand: (api, a, H) => P.moneyBag(H) }),
    },
  }),
  T('sports', 'Sports', {
    keywords: ['sport', 'football', 'soccer', 'basketball', 'tennis', 'match', 'team', 'score', 'league', 'olympic', 'goal', 'champion', 'player'],
    names: [['Bruise', 'Dunk', 'Tackle', 'Sprint'], ['er', 'gut', 'jaw', 'knee']],
    intro: 'bounces on the spot, mostly on one leg.',
    placeholder: 'Ask about sports…',
    spec: {
      palette: [0xd8783a, 0xe8a04a, 0xc85a3a, 0xd8b04a],
      bodies: [['tall', 3], ['blob', 2]], legs: [[2, 4], [1, 1]], heads: [[1, 1]], eyes: [[2, 3]], tops: [['none', 1]],
      decorate: kit({ head: (api, a) => P.sweatband(api, a.faceRad), hand: (api, a, H) => P.ball(H) }),
    },
  }),
  T('movies', 'Movies & TV', {
    keywords: ['movie', 'film', 'series', 'tv show', 'watch', 'actor', 'netflix', 'cinema', 'episode', 'director', 'trailer', 'anime'],
    names: [['Reel', 'Popcorn', 'Flick', 'Matinee'], ['gob', 'muncher', 'eye', 'gawk']],
    intro: 'munches popcorn loudly through every sentence.',
    placeholder: 'Ask about films and shows…',
    spec: {
      palette: [0xc83a4a, 0xa82a3a, 0xd85a5a, 0x8a3a5a],
      bodies: [['blob', 3], ['pear', 2]], heads: [[1, 1]], eyes: [[2, 4], [4, 1]], tops: [['none', 1]],
      decorate: kit({ face: (api, a) => P.glasses3d(a.faceRad), hand: (api, a, H) => P.popcorn(H) }),
    },
  }),
  T('animals', 'Animals & Pets', {
    keywords: ['animal', 'pet', 'dog', 'cat', 'bird', 'fish', 'wildlife', 'zoo', 'puppy', 'kitten', 'hamster', 'horse', 'spider'],
    names: [['Fur', 'Whisker', 'Paw', 'Fang'], ['ball', 'snout', 'muzzle', 'tail']],
    intro: 'sniffs your hand and decides you are acceptable.',
    placeholder: 'Ask about animals and pets…',
    spec: {
      palette: [0x8a6a4a, 0xa8845a, 0x6a5a4a, 0xc8a070],
      bodies: [['bean', 3], ['blob', 2]], legs: [[4, 5], [2, 1]], heads: [[1, 1]], eyes: [[2, 4]], tops: [['none', 1]],
      decorate: kit({ head: (api, a) => P.catEars(api, a.faceRad), hand: (api, a, H) => P.bone(H) }),
    },
  }),
  T('gardening', 'Gardening', {
    keywords: ['garden', 'plant', 'flower', 'grow', 'seed', 'soil', 'tree', 'houseplant', 'compost', 'tomato', 'lawn', 'prune', 'watering', 'cactus', 'succulent', 'repot', 'orchid', 'weeds', 'fertili'],
    names: [['Moss', 'Mulch', 'Root', 'Thistle'], ['belly', 'gut', 'sprout', 'muck']],
    intro: 'shakes soil out of its ears.',
    placeholder: 'Ask about plants and gardens…',
    spec: {
      palette: [0x6a9a4a, 0x8ab05a, 0x5a8a3a, 0x9ab86a],
      bodies: [['pear', 3], ['blob', 2]], heads: [[1, 1]], eyes: [[2, 3], [1, 1]], tops: [['none', 1]],
      decorate: kit({ head: (api, a) => P.flowerPotHat(api, a.faceRad), hand: (api, a, H) => P.wateringCan(H) }),
    },
  }),
  T('language', 'Languages', {
    keywords: ['language', 'translate', 'translation', 'grammar', 'spanish', 'french', 'german', 'georgian', 'italian', 'japanese', 'meaning of', 'pronounce', 'vocabulary', 'how do you say'],
    names: [['Babel', 'Polyglot', 'Lexi', 'Syntax'], ['gob', 'tongue', 'mouth', 'jaw']],
    intro: 'greets you in eleven languages at once.',
    placeholder: 'Ask about words and languages…',
    spec: {
      palette: [0x4aa8a8, 0x3a8a9a, 0x6ab8b0, 0x5a9ab8],
      bodies: [['blob', 2], ['stack', 2]], heads: [[2, 3], [1, 2]], eyes: [[2, 3]], tops: [['none', 1]],
      decorate: kit({ hand: (api, a, H) => P.speechSign(H, api.r.pick(['¡Hola!', 'Bonjour', 'Ciao', 'Gamarjoba'])) }),
    },
  }),
  T('love', 'Love & Relationships', {
    keywords: ['love', 'relationship', 'date', 'dating', 'partner', 'crush', 'breakup', 'romance', 'boyfriend', 'girlfriend', 'wedding', 'marriage', 'friendship'],
    names: [['Heart', 'Cupid', 'Swoon', 'Smooch'], ['gut', 'throb', 'wort', 'flutter']],
    intro: 'sighs dramatically and clutches a wilting rose.',
    placeholder: 'Ask about love and relationships…',
    spec: {
      palette: [0xe07a9a, 0xd8608a, 0xf0a0b8, 0xc85a7a],
      bodies: [['blob', 4], ['pear', 1]], heads: [[1, 1]], eyes: [[2, 4], [1, 1]], tops: [['none', 1]],
      decorate: kit({ head: (api, a) => P.heartAntenna(a.faceRad), hand: (api, a, H) => P.rose(H) }),
    },
  }),
  T('games', 'Games', {
    keywords: ['video game', 'gaming', 'chess', 'puzzle', 'board game', 'minecraft', 'nintendo', 'playstation', 'xbox', 'dungeons', 'level', 'speedrun'],
    names: [['Pixel', 'Respawn', 'Checkmate', 'Dice'], ['gob', 'goblin', 'grub', 'snout']],
    intro: 'mashes buttons on a controller that isn’t plugged in.',
    placeholder: 'Ask about games…',
    spec: {
      palette: [0x6a5ad8, 0x8a4ac8, 0x4a6ad8, 0xa85ac8],
      bodies: [['blob', 3], ['bean', 2]], heads: [[1, 1]], eyes: [[1, 2], [2, 2], [3, 1]], tops: [['none', 1]],
      decorate: kit({ head: (api, a) => P.crown(api, a.faceRad), hand: (api, a, H) => P.gamepad(H) }),
    },
  }),
  T('generic', 'Odd Jobs', {
    keywords: [],
    names: [['Grub', 'Snork', 'Mulch', 'Wobble', 'Gristle', 'Lump'], ['kin', 'snout', 'gob', 'bert', 'muncher']],
    intro: 'blinks several eyes at you, eager to help with anything that doesn’t fit elsewhere.',
    placeholder: 'Ask me about anything that doesn’t fit elsewhere…',
    spec: {},
  }),
].map((t) => [t.id, t]));

// Score every topic's keywords against the text; the best wins.
export function scoreTopics(text) {
  const t = ` ${text.toLowerCase().replace(/[^\p{L}\p{N}%\s]/gu, ' ')} `;
  const scores = {};
  for (const th of Object.values(THEMES)) {
    let s = 0;
    for (const k of th.keywords) {
      const re = new RegExp(`(^|\\s)${k.replace(/[%]/g, '\\%')}`, 'i');
      if (re.test(t)) s += k.length > 5 ? 2 : 1;
    }
    scores[th.id] = s;
  }
  // Bare arithmetic is math even without keywords.
  if (/\d\s*[-+*/x×÷]\s*\d/.test(text) || /\d\s*%/.test(text)) scores.math = (scores.math || 0) + 2;
  return scores;
}

export function matchTheme(text) {
  const scores = scoreTopics(text);
  let best = 'generic', score = 0;
  for (const [id, s] of Object.entries(scores)) if (s > score) { score = s; best = id; }
  return best;
}

export function makeName(themeId, rng) {
  const [a, b] = (THEMES[themeId] || THEMES.generic).names;
  return rng.pick(a) + rng.pick(b);
}

export const themeOf = (id) => THEMES[id] || THEMES.generic;
export const specOf = (id) => ({ id, ...(themeOf(id).spec) });
