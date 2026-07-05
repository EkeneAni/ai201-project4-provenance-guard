# Provenance Guard — Planning

A service that estimates whether a piece of text was AI-generated, returns a
probabilistic **transparency label** (never a verdict), logs every decision, and
gives creators an **appeal** path. Built on Flask + flask-limiter + Groq.

---

## Milestone 1 — Architecture

### Architecture narrative (the path of one piece of text)

A creator pastes text into the **client UI** and submits. That fires
**`POST /submit`** to the **Flask application** (the router). Flask passes the
request through **flask-limiter** (per-IP rate limit protecting the Groq-backed
endpoint), then **validates** the payload (non-empty, within a max length).

The raw text is handed to **Signal 1 — the Groq LLM classifier**, which calls the
external **Groq API** and normalizes the reply to a probability `P(AI)` in `0–1`.
The same raw text goes to **Signal 2 — the stylometry module** (pure Python, no
network), which computes surface-form statistics and maps them to its own `0–1`
"how AI-uniform" score.

Both scores flow into the **confidence scorer**, which combines them into a single
`confidence` value. That goes to the **label generator**, which maps it to a band
and produces the human-readable **transparency label** — always an estimate, never
a verdict. Before responding, the system writes to the **audit log** (append-only:
submission id, timestamp, both signal scores, combined score, label) and persists
the result in the **submission store** with status `submitted`. Flask returns JSON
(`submission_id`, both signals, `confidence`, `label`) and the **client UI renders
the label plus an appeal button**.

If the creator disputes the label, the appeal button fires **`POST /appeal`** with
the `submission_id` and a statement. The **appeals handler** creates an appeal
record, flips submission status `submitted → under_review`, writes another
**audit-log** entry, and returns an `appeal_id`. A **human reviewer** later opens
the **appeal queue** (`GET /appeals`), sees the text, both scores, the label, and
the creator's statement, and resolves it (`upheld`/`overturned`) — writing a final
audit entry.

**Components:** Client UI → flask-limiter → Flask router/validator → Groq classifier
(→ Groq API) → stylometry module → confidence scorer → label generator → audit log
→ submission store → response. Appeal path: Client UI → Flask router → appeals
handler → submission store (status) → audit log → response → reviewer queue.

### Diagram

```
SUBMISSION FLOW
┌────────┐  raw text   ┌──────────────┐  raw text   ┌──────────────────┐
│ Client │────────────▶│ Flask router │────────────▶│ Signal 1: Groq   │──▶ Groq API
│  UI    │ POST /submit│ (limiter +   │             │ LLM classifier   │◀── P(AI) 0–1
└────────┘             │  validate)   │             └──────────────────┘
     ▲                 └──────┬───────┘  raw text   ┌──────────────────┐
     │                        └────────────────────▶│ Signal 2:        │
     │                                               │ stylometry       │──▶ score 0–1
     │  {id, signals,                                └──────────────────┘
     │   confidence, label}          llm score + stylometry score
     │                                          │
     │                                          ▼
     │                                 ┌──────────────────┐ combined 0–1 ┌──────────────┐
     │                                 │ confidence scorer│─────────────▶│ label        │
     │                                 └──────────────────┘              │ generator    │
     │                                                                   └──────┬───────┘
     │                                          label text + scores             │ label text
     │                          ┌──────────────┐◀──────────────────────────────┘
     └──────────────────────────│ audit log +  │
        response (id + label)    │ submission   │
                                 │ store        │
                                 └──────────────┘

APPEAL FLOW
┌────────┐ submission_id     ┌──────────────┐  status change   ┌──────────────┐
│ Client │ + statement       │ Flask router │─────────────────▶│ submission   │
│  UI    │──────────────────▶│  /appeal     │ submitted→       │ store        │
└────────┘   POST /appeal    └──────┬───────┘  under_review    └──────────────┘
     ▲                              │ appeal event
     │  {appeal_id, status}         ▼
     │                       ┌──────────────┐
     └───────────────────────│ audit log    │
                             └──────┬───────┘
                                    │ text + scores + label + statement
                                    ▼
                             ┌──────────────┐  upheld/overturned
                             │ Reviewer     │  → status + audit
                             │ queue        │
                             └──────────────┘
```

---

## Detection signals

### Signal 1 — Groq LLM classifier
- **Measures:** a holistic semantic judgment of how "AI-generated" the text reads —
  coherence, generic phrasing, characteristic transitions/hedging, distributional
  typicality. Output: `P(AI)` in `0–1`.
- **Why it differs (human vs. AI):** LLMs generate high-probability,
  distributionally "average" prose — smooth structure, balanced hedging, telltale
  connectives. A large model recognizes its own family's fingerprints; human writing
  carries idiosyncratic voice and uneven emphasis that reads as atypical.
- **Blind spot:** no ground truth — a vibe judgment that can be *confidently wrong*.
  Shares blind spots with what it judges: a different model family, a
  paraphraser/"humanizer", or light human editing can fool it. Non-deterministic and
  prompt-sensitive; short inputs give it little to judge; it flags bland *human*
  prose (formulaic business/technical writing) as AI.

### Signal 2 — Stylometric heuristics (pure Python)
- **Measures:** statistical regularity of surface form — sentence-length variance
  ("burstiness"), type-token ratio (lexical diversity), punctuation density —
  combined into a `0–1` "how AI-uniform" score.
- **Why it differs (human vs. AI):** AI text trends toward low variance — uniform
  sentence lengths, even punctuation, smoothed vocabulary. Human writing is *bursty*:
  it mixes short and long sentences and varies diversity. Uniformity → higher AI score.
- **Blind spot:** blind to meaning — only sees shape. A human in a uniform register
  (legal, technical, listicles, or just a plain writer) scores as AI. A **poem or
  lyric with heavy repetition and simple vocabulary** scores as AI. AI text prompted
  for variety, or long and edited, evades it. Very short text has undefined variance
  (no signal). Thresholds are genre/language-dependent.

**Why these two together:** they fail *differently*. The LLM reads meaning but has no
objective anchor; stylometry has objective math but is meaning-blind. Neither is
trustworthy alone — which is exactly why the label must be probabilistic and appealable.

---

## False-positive analysis (informs Milestone 2)

**Scenario:** a human engineer writes a concise, plainly-worded release note — short,
uniform sentences, plain vocabulary, clean punctuation.

- **Signal 2 (stylometry):** low variance + moderate TTR → uniform → ~**0.72**.
- **Signal 1 (Groq):** clean, generic-sounding prose → ~**0.66**.
- **Combined:** high band → label generator lands on **"likely AI."** A real human is
  misclassified.

**How the system must respond — and the M2 consequences:**
1. **The score is a probability, not a verdict.** `confidence = 0.9` means "~90%
   AI-likelihood estimate," not "this is AI." Label text says *estimate* + shows the number.
2. **Near-boundary agreement is weak.** When signals only mildly agree, or the score
   sits near a band edge, wording softens → favor a **wide "inconclusive" middle band**
   over a hard AI/human cutoff.
3. **Every non-human label carries an appeal path.** Creator opens an appeal on
   `submission_id` with a statement + optional evidence (drafts, edit history).
   `POST /appeal` sets `submitted → under_review` and audit-logs. A reviewer opens the
   queue, sees text + both raw scores + combined score + label + statement, and can set
   `overturned`.

**M2 stance:** humility over decisiveness — wide uncertain band, "estimate" language,
no verdict phrasing, fully wired appeal path. A false positive (silencing a real writer)
costs more than an "inconclusive."

---

## API surface (the contract)

| Endpoint | Accepts | Returns | Does |
|---|---|---|---|
| `POST /submit` | `{ "text": string }` | `{ submission_id, signals: { llm, stylometry }, confidence, label, created_at }` | rate-limit → validate → both signals → score → label → audit-log → persist (`submitted`) |
| `POST /appeal` | `{ submission_id, appellant, statement, evidence? }` | `{ appeal_id, submission_id, status: "under_review", created_at }` | create appeal → flip status → audit-log |
| `GET /submission/<id>` | — | stored result + current status | read-back for the UI |
| `GET /appeals` | — (reviewer) | list of `{ appeal_id, submission_id, text, signals, confidence, label, statement, status }` | reviewer queue |
| `POST /appeal/<id>/resolve` | `{ decision: "upheld"｜"overturned", reviewer }` | `{ appeal_id, status, resolved_at }` | reviewer decision → status → audit-log |
| `GET /health` | — | `{ status: "ok" }` | liveness |

---

## Milestone 2 — Scoring, labels, appeals, edge cases (LOCKED)

> This replaces the broken draft in `.venv/planning.md`, whose bands overlapped
> (0.6 was both "Certainly Human" and "Most likely AI") and whose combine formula
> was discontinuous (0.60→0.61 jumped the result 0.60→0.81). The design below is
> continuous, non-overlapping, and calibrated against the Milestone 4 sample set.

### Signal outputs
- **Signal 1 (Groq LLM):** `llm_score` = P(AI) in `0–1`.
- **Signal 2 (stylometry):** `sty_score` = P(AI) in `0–1`, a weighted blend of three
  sub-metrics (higher = more AI-uniform):
  - `sub_burstiness` (weight **0.60**) — `1 − clamp(sentence-length CV / 0.65)`. Low
    variance → AI. This is the strongest discriminator.
  - `sub_punct` (weight **0.20**) — `clamp(punctuation-per-word / 0.20)`. Weak/noisy.
  - `sub_ttr` (weight **0.20**) — `clamp((TTR − 0.70) / 0.30)`. Length-confounded on
    short text, so anchored to sit near neutral (~0.5) rather than bias upward.
  - Insufficient text (< 8 words or < 2 sentences) → neutral `0.5`.

### Combine formula
`confidence = 0.60 · llm_score + 0.40 · sty_score`, clamped to `0–1`.
The LLM is weighted higher (more reliable, meaning-aware); stylometry moderates it.
A confidence of `0.6` means: **the system's calibrated estimate is a 60% AI-likelihood** —
an estimate, never a determination.

### Bands (non-overlapping, wide uncertain band — reflects the FP stance)
| Range | Attribution | Meaning |
|---|---|---|
| `[0.00, 0.35)` | `likely_human` | signals lean human |
| `[0.35, 0.65)` | `uncertain` | inconclusive — no provenance label applied |
| `[0.65, 1.00]` | `likely_ai` | signals lean AI |

### Label variants (three, one per band)
- **likely_ai:** "Likely AI-generated — our automated signals estimate a {N}%
  AI-likelihood. This is an estimate, not a determination." *(+ appeal CTA in M5)*
- **likely_human:** "Likely human-written — our automated signals estimate a {N}%
  AI-likelihood. This is an estimate, not a determination."
- **uncertain:** "Uncertain — our signals could not confidently determine authorship
  (estimated {N}% AI-likelihood). No provenance label applied."

### Calibration evidence (M4 sample set)
| Input | llm | sty | combined | band |
|---|---|---|---|---|
| Clearly AI | 0.80 | 0.49 | 0.68 | likely_ai |
| Clearly human | 0.20 | 0.24 | 0.22 | likely_human |
| Formal human (borderline) | 0.80 | 0.52 | 0.69 | likely_ai — *false positive* |
| Lightly edited AI (borderline) | 0.70 | 0.56 | 0.64 | uncertain |

The **formal-human → likely_ai** result is the canonical false positive: the LLM
itself rates academic prose 0.8 and stylometry sees low burstiness, so both signals
converge on "reads AI." This is precisely why the label is an estimate and every
result is appealable (Milestone 5).

### Named edge cases (system handles poorly)
- **Formal/academic human prose** (uniform sentence length, low burstiness) → both
  signals drift AI → false positive. *Mitigation: wide uncertain band + appeals.*
- **Poem/lyric with heavy repetition + simple vocabulary** → uniform short lines score
  as AI on burstiness. *Mitigation: appeals; low TTR partly offsets.*
- **Very short input** (< 8 words / < 2 sentences) → stylometry has no stable
  statistics (returns neutral 0.5) and the LLM is under-informed.

---

## AI Tool Plan (Milestones 3–5)

- **M3 (submission endpoint + first signal):** provide the *Detection signals* section
  (Signal 1) + the diagram. Ask for a Flask app skeleton with `POST /submit`
  (validation + flask-limiter) and the Groq classifier function returning `0–1`. Verify
  by calling the signal function directly on a few clearly-AI and clearly-human samples
  before wiring it into the endpoint.
- **M4 (second signal + confidence scoring):** provide *Detection signals* (Signal 2) +
  *Uncertainty representation* + the diagram. Ask for the stylometry function (`0–1`) and
  the confidence scorer. Check that scores vary meaningfully between clearly-AI and
  clearly-human text, and that the inconclusive band actually catches ambiguous inputs.
- **M5 (production layer):** provide the label variants + appeals workflow + the diagram.
  Ask for the label generator and the `/appeal` endpoint (+ resolve + queue). Verify all
  three label variants are reachable and that an appeal correctly flips status and writes
  an audit entry.
