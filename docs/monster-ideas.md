# Monster ideas

Brainstorm of workflows worth turning into monsters. Criteria:

- **Complex enough to warrant a monster:** a fixed pipeline of 3–6 steps that combines several sources and would take a person 30 minutes to 3 hours by hand. Not "write an email".
- **Broadly needed:** a job lots of people repeat, not a one-off.
- **Deterministic(ish):** the flow is fixed at forge time; LLM calls are single steps inside it. Open-ended tasks like coding or "plan my trip" don't fit.

Limbs available: `http_fetch`, `web_search`, `llm`, `tts`, `sfx`, `image`, `sokosumi`, `notify` and forged Python, plus `for_each` and `when` on steps. Credit costs are Sokosumi list prices.

## Candidates

| # | Monster | Who needs it | Fixed flow | ≈ cost |
|---|---|---|---|---|
| 1 | **Meeting Dossier** (sales call, investor or job interview prep) | Sales, founders, job seekers | Company Researcher (30) → News Research, last 90 days (80) → `web_search` on the person → `llm`: 5 talking points, 3 likely objections, 1 opener → `tts` 60-second briefing | ~110 cr |
| 2 | **Landing Page Doctor** | Startups, marketers | One URL in parallel → Page Design Analysis (80), Page Copy Assessment (60), Page Ranking Insights (100) → `llm` merges into a top-5 fix list ranked by impact | ~240 cr |
| 3 | **Is This Legit?** (shop, supplier, client or landlord vetting). Built as `monsters/scam-sniffer.json` | Anyone dealing with an unknown party | `http_fetch` site + RDAP domain age (forged) → `web_search` "<name> scam / reviews / complaints" → forged rubric score → `when` borderline: Sokosumi escalation → report | 0–110 cr |
| 4 | **Rival Sniffer** (competitor snapshot) | Founders, marketing, strategy | Company Researcher `for_each` competitor (30 each) → Website Traffic Analysis on all domains in one call (90) → Page Copy Assessment `for_each` → optional Meta Ads Library (850) → `llm` comparison table plus "where they're weak" | ~300 cr, ~1150 with ads |
| 5 | **Should I Buy This?** | Consumers | Amazon URL → Product Reality Check (60) → `web_search` for alternatives → `when` price > X: Reddit Research (700) → `llm` verdict: buy / wait / alternative | 60–760 cr |
| 6 | **Name / Tagline Tester** | Founders, marketers | 3 options + target demographic → Let The Crowd Decide (280) → Ask the Crowd – Opinion on the winner, for the *why* (280) → `llm` recommendation | ~560 cr |
| 7 | **AI Visibility Check** ("what does ChatGPT say about my brand") | Brands | GEO Sniffer for 3 fixed queries (230 each, `for_each`) → LLM.txt Generator (100) → `llm` action list and a ready-made `llms.txt` | ~800 cr |
| 8 | **Location Scout** ("should I open a café here?") | Small business owners | Google Maps Intelligence (90) → `web_search` for local rents and foot traffic → `llm` saturation and opportunity score against a fixed rubric | ~90 cr |
| 9 | **Creator Health Check** | Creators, social media managers, brand partnership vetting | Handles → Instagram, TikTok and YouTube analysis, each behind a `when` for whether that handle was given (60–130) → `llm` cross-platform report and brand-safety flag | ~250 cr |
| 10 | **Daily Briefing** | Anyone with a recurring info need | Topic list → `web_search` `for_each` (free) or dpa (200) → forged dedup by URL → `llm` condense → `tts` 3-minute podcast | 0–200 cr |
| 11 | **Mock Interviewer** | Job seekers | Job posting URL → `http_fetch` → `llm` pulls out requirements → Company Researcher → `llm` writes 8 questions plus what a good answer contains → the voice agent asks them | ~30 cr |
| 12 | **Article → Content Pack** | Content marketers, newsrooms | Article URL → Headline Finder (280) → Quote Extractor (100) → `llm` writes LinkedIn and X posts → `image` creates a cover | ~380 cr |

## Rejected

- **Email or post writing:** a single LLM call, too thin.
- **Coding, "plan my trip", "help me decide":** too open-ended to fix in advance.
- **Organization Analysis (900):** needs internal company data that can't easily be dictated by voice.
- **Movie Production, Mass Image Generator:** novelty, not a real need.
- **Deepfake Detector, AttentionInsight, Ad-Campaign Generator, Digital Mockup:** they take file inputs, which a voice flow can't supply. Usable only if a monster gets a file URL from an earlier step, or the UI gets an upload box.

## Patterns

1. **Most good monsters share one shape:** gather from several sources in parallel, judge against a fixed rubric, produce a short spoken verdict plus a full report on screen. Worth offering as a forge template.
2. **`when` escalation is the strongest selling point:** run the cheap check and only hire the expensive agent if the result calls for it (#3, #5). The library estimate then becomes a range, not one number.
3. **Forged limbs take the deterministic parts:** domain age, price math, dedup, scoring rubrics. No LLM tokens, and it shows the "monster makes its own tools" idea.
4. **Voice fit:** results arrive minutes later, so each monster needs a fixed 30–60 second spoken summary; the long-form result belongs on screen.
5. **Watchers:** with `notify` and watches, "check X on a schedule and tell me when Y changes" monsters are possible too (see `monsters/crypto-price-watcher.json`). Daily Briefing (#10) and Rival Sniffer (#4) both work as watchers.

## Demo set

- **#1 Meeting Dossier:** cheap, fast, and everyone understands it.
- **#2 Landing Page Doctor:** several Sokosumi agents in parallel with a concrete result.
- **#3 Is This Legit?:** forged limbs, conditional escalation and the safety angle.
