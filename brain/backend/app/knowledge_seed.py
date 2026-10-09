"""Курированные ПАКЕТЫ ЗНАНИЙ для метода подготовки к собеседованию (kind = "interview_role").

Это ДАННЫЕ, а не код: универсальный орган подготовки к собеседованию загружает нужный пакет по распознанной роли.
Профессии, которых здесь нет, монстр получает отдельным лёгким процессом (генерация ТОЛЬКО пакета знаний одним вызовом
модели + проверка структуры и релевантности + сохранение) — без постройки нового органа.

Формат вопроса: (уровень, тип, вопрос, ключевые пункты ответа). Уровни: junior | mid | senior | lead.
Типы: concept | scenario | design | troubleshooting | behavioral.
Чтобы добавить роль — допишите словарь в ROLES (ключ, название, синонимы, темы, упражнения, критерии).
"""

ROLES = [
    {
        "key": "voip-engineer", "title": "VoIP Engineer", "domain": "telecommunications", "specialization": "voip",
        "aliases": ["voip", "voip engineer", "voip developer", "voip specialist", "voice over ip", "voice-over-ip", "sip engineer",
                    "ip telephony", "ip телефония", "ip-телефония", "voip инженер", "voip-инженер", "инженер voip", "ip telefonie"],
        "competencies": ["SIP signaling and transaction/dialog model", "RTP/RTCP media and call quality (jitter, loss, MOS)",
                         "SDP offer/answer and codec negotiation", "NAT traversal and Session Border Controllers",
                         "Carrier interconnects, SIP trunking and least-cost routing", "Asterisk, FreeSWITCH, Kamailio, OpenSIPS",
                         "Traffic KPIs: ASR, ACD, PDD, NER", "High availability and capacity planning for SIP platforms",
                         "Production troubleshooting with sngrep, tcpdump/Wireshark, Homer", "VoIP security and fraud prevention"],
        "topics": {
            "SIP signaling": [
                ("junior", "concept", "What are the main SIP request methods and what does each do?", "INVITE, ACK, BYE, CANCEL, REGISTER, OPTIONS; plus re-INVITE/UPDATE, INFO, REFER, NOTIFY"),
                ("junior", "concept", "Walk through a basic INVITE / 100 Trying / 180 Ringing / 200 OK / ACK / BYE call flow.", "Who sends each message, why ACK is end-to-end for 2xx, how BYE ends the dialog"),
                ("mid", "concept", "Explain the difference between a SIP transaction and a SIP dialog.", "Transaction = request plus its responses (Via branch); dialog = Call-ID + From/To tags spanning transactions; CSeq ordering"),
                ("mid", "concept", "What is the difference between 180 Ringing and 183 Session Progress, and how does early media work?", "183 carries SDP for early media (announcements, ringback from far end); PRACK/100rel for reliability"),
                ("mid", "troubleshooting", "How do you interpret SIP 403, 404, 408, 480, 486, 487 and 503 responses when a route fails?", "Authorization/blocked vs unknown number vs timeout vs unavailable vs busy vs cancelled vs overload/no route; which to fail over on"),
                ("senior", "design", "Which SIP responses should trigger failover to the next carrier and which must not? Justify your list.", "Fail over on 408/500/502/503/no response; never on 486/404/487 (user-level result); avoid duplicate calls and long PDD"),
                ("senior", "concept", "How do Record-Route, Route and loose routing keep a proxy in the signaling path?", "Record-Route inserted by proxy, route set built by UAs, lr parameter, impact on in-dialog requests"),
                ("senior", "scenario", "Calls drop exactly at 32 seconds. What is happening and how do you prove it?", "ACK for 200 OK not received (NAT/contact/Record-Route issue) -> Timer H/B expiry; check with sngrep that ACK reaches UAS"),
            ],
            "RTP, media and call quality": [
                ("junior", "concept", "What is the role of RTP and RTCP in a VoIP call?", "RTP carries media with sequence numbers/timestamps; RTCP carries reports (loss, jitter, RTT)"),
                ("mid", "concept", "Explain jitter, packet loss and latency and how each affects voice quality and MOS.", "Jitter buffers, loss concealment, one-way delay < 150 ms, E-model/R-factor, MOS ranges"),
                ("senior", "troubleshooting", "A customer reports one-way audio. Walk through your diagnosis from signaling to packets.", "Compare SDP c=/m= addresses with actual RTP source, NAT/private IPs, symmetric RTP, firewall/SBC pinholes, RTP to wrong port"),
                ("senior", "scenario", "MOS drops every evening on one carrier. How do you find whether it is the network, transcoding or the carrier?", "Per-route RTCP stats/Homer, compare codecs and transcoding load, traceroute/MTR, carrier-side test calls, time correlation"),
                ("lead", "design", "How would you build continuous call-quality monitoring for 50,000 concurrent calls?", "RTCP-XR/HEP to Homer, sampling, per-carrier dashboards, alerting on MOS/loss/jitter percentiles, storage sizing"),
            ],
            "SDP and codec negotiation": [
                ("mid", "concept", "How does the SDP offer/answer model negotiate codecs?", "Offer lists codecs by preference, answer selects subset, payload types, rtpmap/fmtp, a=sendrecv/sendonly"),
                ("mid", "concept", "Compare G.711, G.729 and Opus for a carrier network.", "Bandwidth, MOS, licensing, packet loss resilience, transcoding cost, WebRTC needs Opus"),
                ("senior", "troubleshooting", "Calls connect but immediately fail with 488 Not Acceptable Here. What do you check?", "No common codec, SRTP vs RTP mismatch, ptime/fmtp, SDP manipulation on SBC; decide where to transcode"),
                ("senior", "troubleshooting", "DTMF digits are lost in IVR menus. How do you diagnose and fix it?", "RFC 2833/4733 vs SIP INFO vs inband; payload type 101 negotiation; transcoding destroying inband tones"),
            ],
            "NAT traversal and SBC": [
                ("mid", "concept", "Why does NAT break SIP and RTP, and which techniques fix it?", "Private IPs in Contact/Via/SDP; rport, received, keepalives, STUN/TURN/ICE, media anchoring on SBC"),
                ("senior", "design", "What does a Session Border Controller do at the network edge, and when would you deploy one?", "Topology hiding, NAT traversal, media anchoring, transcoding, security/DoS, normalization, CAC"),
                ("senior", "troubleshooting", "Registrations from remote phones expire randomly behind a customer firewall. What do you change?", "NAT binding timeout vs registration interval, OPTIONS keepalives, SIP ALG disabling, TCP/TLS"),
                ("lead", "design", "How do you scale media relay (rtpengine/rtpproxy) horizontally behind Kamailio?", "Set-based load balancing, call-id affinity, kernel forwarding, capacity per node, failover of in-progress calls"),
            ],
            "Carrier routing and LCR": [
                ("mid", "concept", "What is least-cost routing and what data does it need?", "Rate decks per prefix, longest-prefix match, effective dates, billing increments, quality overrides"),
                ("mid", "concept", "Explain ASR, ACD, PDD and NER. Which one tells you a carrier is faking answers?", "ASR answered/attempts, ACD average duration, PDD post-dial delay, NER network effectiveness; very short ACD with high ASR = false answer supervision"),
                ("senior", "concept", "What is the difference between CLI and non-CLI routes, and why does it matter for wholesale traffic?", "Caller ID delivered vs stripped/altered; price, quality, regulatory and fraud implications"),
                ("senior", "design", "Design failover between wholesale carriers for one destination with quality-based routing.", "Ordered/weighted routes, failover codes, ASR/ACD thresholds, automatic route demotion, PDD budget"),
                ("senior", "troubleshooting", "ASR to one destination fell from 45% to 20% after a routing change. How do you investigate?", "Compare before/after CDRs per carrier and prefix, SIP response distribution, PDD, test calls, roll back"),
                ("lead", "design", "How would you implement rate deck imports for 300 carriers without breaking live routing?", "Validation, effective dates, versioned tables, atomic switch, prefix overlap checks, alerting on price jumps"),
            ],
            "Platforms: Asterisk, FreeSWITCH, Kamailio, OpenSIPS": [
                ("junior", "concept", "What is the difference between a SIP proxy (Kamailio/OpenSIPS) and a B2BUA (Asterisk/FreeSWITCH)?", "Proxy routes signaling statelessly/statefully without owning calls; B2BUA terminates and re-originates legs, can handle media"),
                ("mid", "scenario", "When would you put Kamailio in front of FreeSWITCH or Asterisk servers?", "Registration and load balancing, dispatcher, security, scaling media servers horizontally"),
                ("senior", "design", "What is a Class 4 softswitch and how does it differ from a Class 5 platform?", "Carrier transit/routing/billing vs end-user features; CDRs, LCR, interconnects, scale"),
                ("senior", "scenario", "How do you upgrade a production Kamailio cluster without dropping calls?", "Drain via dispatcher state, DNS SRV weights, keep dialog state in DB/DMQ, rolling restart, canary"),
            ],
            "Architecture, HA and capacity": [
                ("senior", "design", "Design a highly available SIP platform for 10,000 concurrent calls across two data centers.", "Anycast/SRV, active-active proxies, shared state (DMQ/DB), media servers N+1, SBC pairs, carrier redundancy"),
                ("senior", "concept", "How do you size CPS and concurrent-call capacity for a new region?", "Erlang B, busy hour, CPS limits per node, transcoding CPU cost, headroom and load tests (SIPp)"),
                ("lead", "design", "How would you build observability for a VoIP platform end to end?", "Metrics (CPS, ASR, PDD, errors), HEP/Homer traces, CDR analytics, log correlation by Call-ID, SLOs and alerting"),
                ("lead", "behavioral", "Tell me about a major VoIP incident you led. What was the root cause and what changed afterwards?", "Timeline, communication with carriers/customers, mitigation, postmortem actions"),
            ],
            "Production troubleshooting": [
                ("mid", "troubleshooting", "Which tools do you use to trace a single failing call and what do you look at first?", "sngrep/tcpdump/Wireshark, Homer, platform logs by Call-ID, CDR; start from the first error response"),
                ("senior", "troubleshooting", "Calls fail only for one destination prefix. Walk me through your investigation.", "Route selection, number normalization, carrier responses for that prefix, test calls, compare with other carriers"),
                ("senior", "troubleshooting", "PDD increased after a routing change. What could cause it and how do you prove it?", "Serial failover through dead routes, DNS/SRV timeouts, slow carrier, retransmissions; measure per hop"),
                ("senior", "troubleshooting", "You see intermittent SIP 503 responses from your own platform. How do you find the cause?", "Overload control, CPS limits, dispatcher marking nodes down, DB latency, licensing limits, carrier 503 passthrough"),
            ],
            "VoIP security and fraud": [
                ("mid", "concept", "How do SIP scanners and brute-force attacks work and how do you defend against them?", "friendly-scanner, REGISTER floods; pike/ratelimit, fail2ban, strong credentials, IP ACLs, no default contexts"),
                ("senior", "scenario", "How do you detect and stop toll fraud (IRSF) within minutes?", "Real-time thresholds per account/destination, high-risk prefixes, CPS/spend limits, automatic blocking, alerts"),
                ("senior", "concept", "When do you use TLS and SRTP, and what operational issues do they introduce?", "Signaling/media encryption, certificate management, SBC CPU cost, troubleshooting needs decryption, interop"),
                ("lead", "design", "How do STIR/SHAKEN and caller-ID attestation affect your interconnect design?", "Signing/verifying Identity header, attestation levels A/B/C, certificate infrastructure, carrier requirements"),
            ],
        },
        "exercises": [("mid", "Read a sngrep capture of a failed call and name the failing hop and fix"),
                      ("senior", "Diagnose a one-way audio case from SDP and a packet capture"),
                      ("senior", "Design carrier failover and quality-based routing for one high-volume destination"),
                      ("lead", "Draw an HA architecture with Kamailio, rtpengine and FreeSWITCH across two sites and explain failure modes")],
        "rubric": ["Correct protocol-level reasoning (SIP/SDP/RTP), not vendor buzzwords",
                   "Troubleshooting method: evidence from captures, CDRs and metrics before conclusions",
                   "Carrier and business awareness: ASR/ACD/PDD, cost vs quality",
                   "Senior level: designs for failure, capacity and observability; explains trade-offs"],
        "recommendations": ["Re-trace real call flows in sngrep until you can read them fluently", "Know the SIP response codes that should and should not trigger failover",
                            "Prepare one incident story with numbers (ASR/PDD/MOS before and after)", "Be ready to whiteboard an HA SIP architecture"],
    },
    {
        "key": "cybersecurity-engineer", "title": "Cybersecurity Engineer", "domain": "security", "specialization": "cybersecurity",
        "aliases": ["cybersecurity", "cyber security", "cyber-security", "security engineer", "information security", "infosec",
                    "it security", "kyberbezpečnost", "kybernetická bezpečnost", "кибербезопасность", "кибербезопасности",
                    "информационная безопасность", "информационной безопасности"],
        "competencies": ["Networking and security fundamentals", "Authentication and authorization", "OWASP Top 10 and application security",
                         "Threat modeling", "Security monitoring and detection", "Incident response", "Cryptography fundamentals",
                         "Vulnerability management", "Cloud and network security architecture"],
        "topics": {
            "Fundamentals": [
                ("junior", "concept", "What is the difference between authentication and authorization?", "Identity proof vs permissions; examples: MFA vs RBAC; failures of each"),
                ("junior", "concept", "Explain the CIA triad with a concrete example for each property.", "Confidentiality, integrity, availability; encryption, hashing/signatures, redundancy/DoS protection"),
                ("junior", "concept", "What happens in a TLS handshake at a high level?", "Cipher negotiation, certificate validation, key exchange, session keys, forward secrecy"),
                ("mid", "concept", "Symmetric vs asymmetric cryptography: when do you use each and why are they combined?", "Speed vs key distribution; hybrid encryption, signatures, key management"),
            ],
            "Application security": [
                ("junior", "concept", "Explain SQL injection and appropriate defenses.", "Untrusted input in queries; parameterized queries, least-privilege DB users, input validation, WAF as extra layer"),
                ("mid", "concept", "Compare stored, reflected and DOM-based XSS and how to prevent them.", "Output encoding by context, CSP, framework auto-escaping, HttpOnly cookies"),
                ("mid", "concept", "What is SSRF and why is it dangerous in cloud environments?", "Server fetches attacker-chosen URLs; metadata endpoints (IMDS), allow-lists, network egress controls"),
                ("senior", "design", "How would you build a secure SDLC for 30 development teams?", "Threat modeling, SAST/DAST/SCA in CI, security champions, risk-based gating, metrics"),
            ],
            "Detection and incident response": [
                ("mid", "scenario", "How would you investigate suspicious login activity on a corporate account?", "Logs (IdP, VPN, endpoints), impossible travel, MFA events, session revocation, scope and containment"),
                ("mid", "concept", "Walk through the phases of incident response.", "Preparation, identification, containment, eradication, recovery, lessons learned; evidence preservation"),
                ("senior", "scenario", "Ransomware is detected on three servers at 2 a.m. What are your first 60 minutes?", "Isolate, preserve evidence, identify patient zero and scope, protect backups, communication plan, legal"),
                ("senior", "design", "How do you design detection rules that are useful and not noisy?", "Threat-informed (MITRE ATT&CK), baselines, tuning, severity, playbooks, measuring true positives"),
            ],
            "Threat modeling and architecture": [
                ("mid", "design", "Threat-model a login page with password reset.", "STRIDE, brute force, credential stuffing, reset token leakage, enumeration, rate limiting, MFA"),
                ("senior", "design", "How would you segment a network that hosts payment systems?", "Zones, least privilege, jump hosts, PCI scope reduction, monitoring of east-west traffic"),
                ("senior", "concept", "What is zero trust in practice, beyond the slogan?", "Strong identity, device posture, per-request authorization, micro-segmentation, continuous verification"),
                ("lead", "design", "How do you prioritize a security roadmap with a limited budget?", "Risk assessment, crown jewels, quick wins vs structural fixes, metrics, stakeholder buy-in"),
            ],
            "Vulnerability management": [
                ("junior", "concept", "What is a CVE and how do you read a CVSS score?", "Identifier, base/temporal/environmental metrics, exploitability vs impact"),
                ("mid", "scenario", "A critical vulnerability in a widely used library is published. What do you do in the first day?", "Inventory/SBOM, exposure assessment, mitigations, patch plan, communication"),
                ("senior", "behavioral", "Tell me about a time you had to convince engineers to fix a security issue they disagreed with.", "Evidence, business impact, alternatives, compromise, outcome"),
            ],
        },
        "exercises": [("junior", "Classify ten log lines as benign or suspicious and explain why"), ("mid", "Threat-model a password-reset flow"),
                      ("senior", "Triage a phishing alert end to end and write the incident summary"), ("lead", "Present a 6-month security roadmap for a startup")],
        "rubric": ["Accurate security fundamentals", "Attacker mindset combined with practical defenses", "Structured incident handling and evidence",
                   "Senior level: risk-based prioritization and architecture"],
        "recommendations": ["Practice explaining OWASP Top 10 with real examples", "Prepare one incident story using a timeline",
                            "Know your logging stack and how you would detect an attack in it"],
    },
    {
        "key": "ux-ui-designer", "title": "UX/UI Designer", "domain": "design", "specialization": "ux-ui",
        "aliases": ["ux/ui", "ui/ux", "ux ui", "ui ux", "ux-ui", "ui-ux", "ux/ui designer", "ui/ux designer", "ux ui designer",
                    "ux/ui дизайнер", "ui/ux дизайнер", "ux/ui designér"],
        "competencies": ["User research", "Usability testing", "Information architecture", "Interaction design", "Wireframing and prototyping",
                         "Design systems", "Accessibility (WCAG)", "Visual design and typography", "Figma workflows", "Design-to-development handoff"],
        "topics": {
            "Research and discovery": [
                ("junior", "concept", "What is the difference between UX and UI?", "Experience/flows/research vs visual interface; how they depend on each other"),
                ("junior", "concept", "Which user research methods do you know and when would you use each?", "Interviews, surveys, usability tests, analytics, card sorting; qualitative vs quantitative"),
                ("mid", "scenario", "How do you turn interview findings into design decisions?", "Synthesis, affinity mapping, personas/JTBD, prioritized problems, traceability to decisions"),
                ("senior", "scenario", "How do you run research when there is no budget and no access to users?", "Guerrilla testing, proxies, support tickets, analytics, assumptions log, risk of bias"),
            ],
            "Interaction design and flows": [
                ("junior", "design", "How do you approach designing a user flow?", "Goal, entry points, happy path, edge cases, error states, validation"),
                ("mid", "design", "Redesign the checkout of an online shop: where do you start?", "Data on drop-off, heuristics, steps reduction, guest checkout, trust signals, testing"),
                ("mid", "concept", "What is information architecture and how do you validate it?", "Content structure, navigation, labeling; card sorting, tree testing"),
                ("senior", "design", "How do you design for complex B2B workflows with expert users?", "Efficiency over simplicity, keyboard, density, progressive disclosure, research with power users"),
            ],
            "Usability testing and metrics": [
                ("junior", "concept", "How do you plan and run a usability test?", "Goals, tasks, participants (5 per round), moderation, think aloud, findings severity"),
                ("mid", "concept", "Which metrics show that a design change worked?", "Task success, time on task, error rate, SUS, conversion, retention; A/B test caveats"),
                ("senior", "scenario", "An A/B test shows a +3% conversion but support tickets increased. What do you do?", "Look beyond one metric, segment, qualitative follow-up, long-term effects, decision framework"),
            ],
            "Design systems and visual design": [
                ("junior", "concept", "What makes a good component in a design system?", "Consistency, states, variants, tokens, documentation, accessibility built in"),
                ("mid", "scenario", "How do you keep Figma components and coded components in sync?", "Design tokens, naming conventions, shared review, versioning, Storybook"),
                ("senior", "design", "How would you introduce a design system to 5 product teams?", "Audit, governance, contribution model, adoption metrics, migration plan"),
            ],
            "Accessibility": [
                ("junior", "concept", "What are the basics of accessible design?", "Contrast, focus states, labels, alt text, keyboard navigation, not color-only signals"),
                ("mid", "concept", "Explain WCAG levels and how you check a design against them.", "A/AA/AAA, contrast ratios, tools, screen reader testing"),
            ],
            "Collaboration and portfolio": [
                ("junior", "behavioral", "Walk me through a project in your portfolio: your role, process and outcome.", "Problem, constraints, research, iterations, result with evidence"),
                ("mid", "behavioral", "How would you explain your design decisions to stakeholders who disagree?", "Tie to user evidence and business goals, options, trade-offs, testing as arbiter"),
                ("mid", "scenario", "How do you hand off a design to developers so that the result matches?", "Specs, states, tokens, prototypes, early involvement, design QA"),
                ("senior", "behavioral", "Tell me about a time research changed the direction of a product.", "Evidence, influence, outcome, what you would do differently"),
            ],
        },
        "exercises": [("junior", "Redesign a sign-up form and explain every change"), ("mid", "Redesign a checkout flow in Figma and test it with 5 users"),
                      ("senior", "Plan research and success metrics for a new B2B feature"), ("lead", "Present a design-system adoption strategy")],
        "rubric": ["User-centered reasoning backed by evidence", "Clear process from research to solution", "Accessibility awareness",
                   "Communication with stakeholders and developers"],
        "recommendations": ["Prepare 2-3 portfolio case studies with measurable outcomes", "Practice a live whiteboard flow exercise",
                            "Review WCAG AA basics and be ready to critique a screen"],
    },
    {
        "key": "frontend-developer", "title": "Frontend Developer", "domain": "software", "specialization": "frontend",
        "aliases": ["frontend", "front-end", "front end", "frontend developer", "frontend engineer", "front-end developer",
                    "фронтенд", "фронтенд-разработчик", "фронтенд разработчик", "frontend vývojář"],
        "competencies": ["HTML semantics and CSS layout", "JavaScript and TypeScript", "React (or another framework) state management",
                         "Browser rendering and web performance", "Accessibility", "Testing", "API integration and security in the browser"],
        "topics": {
            "JavaScript and TypeScript": [
                ("junior", "concept", "Explain the event loop, microtasks and macrotasks.", "Call stack, task queue, promises as microtasks, rendering between tasks"),
                ("junior", "concept", "What is the difference between == and ===, and between let, const and var?", "Coercion; block vs function scope; hoisting/TDZ"),
                ("mid", "concept", "How do TypeScript generics and union types help you model API data?", "Reusable typing, discriminated unions, narrowing, type guards"),
            ],
            "React and state": [
                ("junior", "concept", "What triggers a re-render in React?", "State/props/context changes, parent re-render, keys"),
                ("mid", "concept", "When would you use useMemo/useCallback, and when are they harmful?", "Expensive computations, referential stability for memoized children; overhead and premature optimization"),
                ("mid", "design", "How do you decide between local state, context and a state library?", "Scope of state, update frequency, server state vs client state (React Query)"),
                ("senior", "design", "How would you structure a large frontend codebase for 10 teams?", "Feature boundaries, shared design system, monorepo, ownership, micro-frontends trade-offs"),
            ],
            "Browser, performance and CSS": [
                ("junior", "concept", "Explain the CSS box model and the difference between flexbox and grid.", "content/padding/border/margin; one-dimensional vs two-dimensional layout"),
                ("mid", "concept", "How does the browser render a page, and what are reflow and repaint?", "DOM/CSSOM, render tree, layout, paint, composite; layout thrashing"),
                ("senior", "troubleshooting", "LCP regressed from 1.8 s to 4 s after a release. How do you find the cause?", "Lighthouse/RUM, waterfall, image/font changes, JS bundle, server timing, bisect"),
            ],
            "Testing, accessibility and security": [
                ("mid", "concept", "What do you test with unit, integration and end-to-end tests in a frontend?", "Pure logic, components with user events, critical flows; testing-library philosophy"),
                ("mid", "concept", "How do you prevent XSS in a frontend application?", "Framework escaping, avoid dangerouslySetInnerHTML, sanitization, CSP, token storage"),
                ("senior", "scenario", "How do you make an existing app accessible without a rewrite?", "Audit, semantic fixes, focus management, ARIA only when needed, automated + manual tests"),
                ("junior", "behavioral", "Tell me about a UI bug that was hard to find and how you solved it.", "Reproduction, tools, root cause, prevention"),
            ],
        },
        "exercises": [("junior", "Build an accessible dropdown component"), ("mid", "Implement a searchable list with debounced API calls and tests"),
                      ("senior", "Review a slow page and propose a performance plan")],
        "rubric": ["Correct fundamentals (JS, CSS, rendering)", "Clean component design and state management", "Performance and accessibility awareness",
                   "Senior level: architecture and trade-offs"],
        "recommendations": ["Practice explaining the event loop with a small example", "Be ready to live-code a component with tests"],
    },
    {
        "key": "backend-developer", "title": "Backend Developer", "domain": "software", "specialization": "backend",
        "aliases": ["backend", "back-end", "back end", "backend developer", "backend engineer", "back-end developer", "server-side developer",
                    "бэкенд", "бекенд", "бэкенд-разработчик", "backend vývojář"],
        "competencies": ["API design (REST/gRPC)", "Databases and data modeling", "Concurrency and performance", "Caching",
                         "Messaging and distributed systems", "Security", "Testing and observability"],
        "topics": {
            "APIs": [
                ("junior", "concept", "What makes a REST API well designed?", "Resources, HTTP methods and status codes, idempotency, pagination, versioning"),
                ("mid", "design", "How do you make a payment API idempotent?", "Idempotency keys, unique constraints, stored responses, retries"),
                ("senior", "design", "Design a rate limiter for a public API.", "Token bucket/sliding window, per-key limits, distributed counters (Redis), headers, fairness"),
            ],
            "Databases": [
                ("junior", "concept", "What is an index and when does it not help?", "B-tree lookup, selectivity, write cost, functions on columns"),
                ("mid", "concept", "Explain transaction isolation levels and a problem each prevents.", "Dirty/non-repeatable/phantom reads, serialization anomalies"),
                ("mid", "troubleshooting", "A query became slow after data grew 10x. How do you investigate?", "EXPLAIN plans, missing indexes, statistics, N+1, pagination"),
                ("senior", "design", "When would you choose a relational database vs a document store vs a key-value store?", "Consistency, relations, access patterns, scaling, operational cost"),
            ],
            "Distributed systems": [
                ("mid", "concept", "What problems do message queues solve and which new problems do they create?", "Decoupling, buffering; ordering, duplicates, poison messages, observability"),
                ("senior", "design", "How do you keep data consistent across two services without distributed transactions?", "Outbox pattern, sagas, idempotent consumers, eventual consistency"),
                ("senior", "troubleshooting", "p99 latency doubled but averages are fine. Where do you look?", "Tail latency causes: GC, locks, noisy neighbors, slow dependencies, retries; tracing"),
                ("lead", "design", "Design a URL shortener for 10k writes/s and 1M reads/s.", "ID generation, storage, caching, replication, hot keys, analytics pipeline"),
            ],
            "Security and operations": [
                ("junior", "concept", "How do you store user passwords?", "Slow salted hashes (bcrypt/argon2), never encryption, pepper, rate limiting"),
                ("mid", "concept", "What do you log, measure and trace in a backend service?", "Structured logs with correlation IDs, RED metrics, distributed tracing, alerts on SLOs"),
                ("senior", "behavioral", "Tell me about a production incident you owned end to end.", "Detection, mitigation, root cause, follow-up"),
            ],
        },
        "exercises": [("junior", "Build a small CRUD API with validation and tests"), ("mid", "Add idempotent payment endpoint with retries"),
                      ("senior", "System design: notifications service with fan-out")],
        "rubric": ["Correct data and API modeling", "Reasoning about failure and consistency", "Performance and observability", "Clear trade-offs"],
        "recommendations": ["Practice one system-design question end to end", "Know your database's isolation level defaults"],
    },
    {
        "key": "devops-engineer", "title": "DevOps Engineer", "domain": "operations", "specialization": "devops",
        "aliases": ["devops", "dev ops", "devops engineer", "devops инженер", "девопс", "devops inženýr"],
        "competencies": ["Linux and networking", "CI/CD pipelines", "Containers and Kubernetes", "Infrastructure as code", "Cloud platforms",
                         "Monitoring and incident response", "Security in pipelines"],
        "topics": {
            "Linux and networking": [
                ("junior", "troubleshooting", "A service is not reachable on its port. Which commands do you run?", "ss/netstat, systemctl/journalctl, curl, firewall rules, DNS, logs"),
                ("mid", "concept", "Explain what happens when you type a URL and press Enter, from DNS to response.", "DNS, TCP, TLS, load balancer, HTTP, caching"),
            ],
            "CI/CD": [
                ("junior", "concept", "What stages would you put in a CI/CD pipeline for a web service?", "Build, test, scan, artifact, deploy to staging, approval, production, rollback"),
                ("mid", "design", "Compare blue-green, canary and rolling deployments.", "Risk, cost, rollback speed, traffic shifting, database migrations"),
                ("senior", "design", "How do you handle database migrations in zero-downtime deployments?", "Expand/contract, backward compatibility, feature flags, separate deploy steps"),
            ],
            "Containers and Kubernetes": [
                ("junior", "concept", "What is the difference between a container image and a container?", "Immutable layers vs running instance; registry, tags"),
                ("mid", "troubleshooting", "A pod is in CrashLoopBackOff. How do you debug it?", "describe/logs/previous, probes, resources/OOMKilled, config/secrets, image"),
                ("mid", "concept", "Explain requests and limits in Kubernetes and their effect on scheduling and throttling.", "Scheduling on requests, CPU throttling, OOM on memory limit, QoS classes"),
                ("senior", "design", "How do you design a multi-tenant Kubernetes platform?", "Namespaces, RBAC, network policies, quotas, admission control, cost allocation"),
            ],
            "IaC, cloud and reliability": [
                ("mid", "concept", "Why use infrastructure as code and how do you manage Terraform state safely?", "Reproducibility, review; remote state, locking, workspaces, drift"),
                ("senior", "scenario", "Production is down after a deploy at peak hours. What do you do?", "Rollback first, communicate, collect evidence, root cause later, blameless postmortem"),
                ("senior", "concept", "What are SLOs and error budgets and how do they change release decisions?", "User-centric SLIs, budget consumption, release freezes"),
                ("lead", "behavioral", "How did you improve deployment frequency or reliability in a previous team?", "Baseline metrics (DORA), changes, results"),
            ],
        },
        "exercises": [("junior", "Write a Dockerfile and a CI job for a small app"), ("mid", "Debug a broken Kubernetes deployment"),
                      ("senior", "Design a zero-downtime release process with database changes")],
        "rubric": ["Systematic troubleshooting", "Automation mindset", "Reliability and security awareness", "Senior level: platform thinking"],
        "recommendations": ["Practice kubectl debugging commands", "Prepare a postmortem story with metrics"],
    },
    {
        "key": "qa-engineer", "title": "QA Engineer", "domain": "software", "specialization": "quality-assurance",
        "aliases": ["qa", "qa engineer", "quality assurance", "tester", "software tester", "test engineer", "testers", "qa automation",
                    "тестировщик", "тестер", "тестировщика", "tester software", "testér"],
        "competencies": ["Test design techniques", "Test strategy and risk", "Automation frameworks", "API testing", "Bug reporting",
                         "CI integration", "Performance and security testing basics"],
        "topics": {
            "Test design": [
                ("junior", "concept", "What is the difference between verification and validation?", "Building the product right vs building the right product"),
                ("junior", "concept", "Explain equivalence partitioning and boundary value analysis with an example.", "Classes of inputs, edges like 0, 1, max, max+1"),
                ("mid", "design", "How would you test a login form?", "Positive/negative, security (lockout, injection), accessibility, localization, sessions"),
            ],
            "Strategy and process": [
                ("junior", "concept", "What makes a good bug report?", "Steps, expected vs actual, environment, evidence, severity vs priority"),
                ("mid", "concept", "Explain the testing pyramid and when you would break it.", "Many unit, fewer integration, few E2E; legacy systems, contract tests"),
                ("senior", "design", "How do you build a test strategy for a release with two weeks left and limited people?", "Risk-based prioritization, exploratory sessions, automation where it pays off, exit criteria"),
            ],
            "Automation": [
                ("mid", "concept", "How do you reduce flaky end-to-end tests?", "Stable selectors, waits on conditions, test data isolation, retries only as diagnostics"),
                ("mid", "design", "How do you test a REST API automatically?", "Contracts/schemas, status codes, auth, negative cases, data setup and cleanup"),
                ("senior", "design", "Design an automation framework for web, API and mobile for 5 teams.", "Layers, page objects/screenplay, shared fixtures, CI parallelism, reporting, ownership"),
            ],
            "Collaboration": [
                ("junior", "behavioral", "Tell me about a critical bug you found and how you communicated it.", "Impact, evidence, escalation, follow-up"),
                ("senior", "behavioral", "How do you get developers to own quality?", "Shift-left, pairing, quality metrics, testability in design reviews"),
            ],
        },
        "exercises": [("junior", "Write test cases for a password-reset feature"), ("mid", "Automate an API test suite with negative cases"),
                      ("senior", "Present a risk-based test strategy for a payments release")],
        "rubric": ["Systematic test design", "Risk awareness", "Automation skills", "Communication"],
        "recommendations": ["Practice boundary-value examples aloud", "Bring an example of a test strategy you wrote"],
    },
    {
        "key": "data-analyst", "title": "Data Analyst", "domain": "data", "specialization": "analytics",
        "aliases": ["data analyst", "data analytics", "analytics engineer", "аналитик данных", "datový analytik", "bi analyst"],
        "competencies": ["SQL", "Statistics", "Data cleaning", "Visualization and dashboards", "A/B testing", "Business communication"],
        "topics": {
            "SQL": [
                ("junior", "concept", "Explain the difference between INNER, LEFT and FULL joins.", "Matching rows, NULLs from missing side, duplicates from one-to-many"),
                ("mid", "concept", "When do you use window functions? Give an example.", "Running totals, ranking, period-over-period with LAG"),
                ("mid", "troubleshooting", "Your revenue metric doubled overnight. How do you check whether it is real?", "Join fan-out duplicates, pipeline changes, timezone, data freshness, compare sources"),
            ],
            "Statistics and experiments": [
                ("junior", "concept", "What is the difference between mean and median, and when is the median better?", "Skewed distributions, outliers"),
                ("mid", "concept", "How do you design and evaluate an A/B test?", "Hypothesis, metric, sample size/power, randomization, significance, novelty effects"),
                ("senior", "scenario", "An experiment is significant on day 3 but not on day 14. What do you conclude?", "Peeking, novelty, seasonality, sequential testing, decision rules"),
            ],
            "Communication and dashboards": [
                ("junior", "design", "How do you choose a chart for a metric?", "Comparison vs trend vs distribution vs composition; avoid misleading axes"),
                ("mid", "behavioral", "Tell me about an analysis that changed a business decision.", "Question, method, result, influence"),
                ("senior", "design", "How do you define a north-star metric and its input metrics for a product?", "Value delivered, leading indicators, guardrails"),
            ],
        },
        "exercises": [("junior", "Write SQL for monthly active users by country"), ("mid", "Analyze an A/B test dataset and write the recommendation"),
                      ("senior", "Design a KPI dashboard for executives")],
        "rubric": ["Correct SQL and statistics", "Skepticism about data quality", "Clear business communication"],
        "recommendations": ["Practice SQL window functions", "Prepare one story where data changed a decision"],
    },
]


def seed_packs() -> list[dict]:
    """ROLES -> записи пакетов знаний (структура как у сгенерированных моделью)."""
    out = []
    for r in ROLES:
        topics = [{"name": name, "questions": [{"q": q, "level": lv, "kind": kind, "answer_hint": hint} for lv, kind, q, hint in qs]}
                  for name, qs in r["topics"].items()]
        content = {"title": r["title"], "domain": r["domain"], "specialization": r["specialization"], "competencies": r["competencies"],
                   "topics": topics, "exercises": [{"level": lv, "title": t} for lv, t in r["exercises"]],
                   "rubric": r["rubric"], "recommendations": r["recommendations"]}
        out.append({"kind": "interview_role", "key": r["key"], "title": r["title"], "domain": r["domain"],
                    "specialization": r["specialization"], "aliases": r["aliases"], "content": content})
    return out
