# ai201-project4-provenance-guard

Provenance Guard estimates whether a piece of text was AI-generated, returns a
probabilistic **transparency label** (an estimate, never a verdict), logs every
decision to an audit trail, and gives creators a way to **appeal**. Built on Flask +
Flask-Limiter + Groq.

## How a submission flows

A creator submits text via `POST /submit`. Flask rate-limits (Flask-Limiter) and
validates it, then runs two independent detection signals: a **Groq LLM classifier**
(holistic semantic judgment → `P(AI)`) and a pure-Python **stylometry** module
(surface-form statistics → an "AI-uniform" score). A **confidence scorer** combines
them into a single `0–1` value, a **label generator** turns that into human-readable
text, and the decision is written to an **audit log** and **submission store** before
the response returns. Disputes go through `POST /appeal`, which flips the submission to
`under_review`, logs the event, and surfaces it for a reviewer.

```
POST /submit → [signal 1: Groq] + [signal 2: stylometry] → confidence score
             → transparency label → submission store + audit log → response
POST /appeal → status update (submitted → under_review) → audit log → response
```

See [planning.md](planning.md) for the full architecture narrative, diagram, detection
signals (with blind spots), false-positive analysis, scoring spec, and API contract.

## Detection signals — and why these two

Detection is unreliable in principle: there is no watermark or ground truth in raw
text. My design assumption is therefore that **any single signal is untrustworthy**, so
I picked two signals that fail in *different* directions and force them to corroborate.

1. **Groq LLM classifier** ([signals.py](signals.py)) — a hosted LLM judges how
   AI-generated the passage reads and returns `P(AI)` in `0–1`. *Why:* it's the only
   signal that reasons about *meaning* — generic phrasing, hedging, distributional
   typicality — the way a human skim-reader would. *Blind spot:* it has no ground
   truth, so it can be confidently wrong; it's fooled by paraphrasers and light human
   editing, and it over-flags bland or formal human prose as AI.

2. **Stylometry, pure Python** ([signals.py](signals.py)) — measures surface-form
   regularity: sentence-length variance ("burstiness", the strongest metric),
   punctuation density, and type-token ratio, blended into an "AI-uniform" score. *Why:*
   it's an *objective, deterministic* counterweight to the LLM's black-box judgment, and
   it needs no network. Human writing is bursty (mixes short and long sentences); AI
   trends uniform. *Blind spot:* it is completely meaning-blind — uniform human writing
   (legal, technical) and repetitive poetry both read as "AI".

The LLM reads meaning but has no anchor; stylometry has objective math but is
meaning-blind. Neither is trusted alone, which is exactly why the final label is always
a probabilistic *estimate* and every result is appealable.

**If I were deploying this for real** I would not ship stylometric thresholds tuned on a
handful of English essays — I'd calibrate against a labeled corpus per genre and
language (the current anchors are hand-tuned), replace the raw type-token ratio with a
length-normalized measure (MATTR) to kill the short-text confound, add a third signal of
a different *kind* (e.g. a perplexity/detector model) so a paraphraser can't defeat the
whole system at once, and treat the LLM signal's non-determinism by averaging repeated
calls rather than a single shot.

## Confidence scoring — approach and reasoning

`confidence = 0.60 · llm_score + 0.40 · sty_score`, clamped to `0–1`.

*Why a weighted average rather than something cleverer?* It's transparent and auditable —
anyone can reconstruct the score from the two logged signal values, which matters for a
system whose whole point is accountability. *Why 0.60/0.40?* The LLM is the more
reliable, meaning-aware judge, so it leads; stylometry is a noisier, form-only signal, so
it *moderates* rather than drives. (My original planning draft used a discontinuous
formula where nudging one signal 0.60→0.61 jumped the result 0.60→0.81; I threw it out
for this continuous blend.) Non-overlapping bands, with a deliberately **wide uncertain
band** — the humility stance from the false-positive analysis, so ambiguous inputs are
labeled inconclusive rather than confidently misattributed:

| Range | Attribution | Label |
|---|---|---|
| `[0.00, 0.35)` | `likely_human` | "Likely human-written …" |
| `[0.35, 0.65)` | `uncertain` | "Uncertain … No provenance label applied." |
| `[0.65, 1.00]` | `likely_ai` | "Likely AI-generated …" |

*What I'd change for real:* map raw scores to a **calibrated** probability against a
labeled dataset (Platt scaling / isotonic regression) so that "0.68" genuinely means
"68% of texts scoring here are AI" — right now it's an ordinal estimate, not a
calibrated one — and weight the two signals by their measured reliability instead of my
judgment.

### Two example submissions (from Milestone 4 testing)

Real scores, showing the pipeline produces meaningful variation — not a constant:

**High-confidence case — clearly human** (casual restaurant review):
> "ok so i finally tried that new ramen place downtown and honestly? underwhelming.
> the broth was fine but they put WAY too much sodium in it …"

| llm_score | sty_score | combined | attribution |
|---|---|---|---|
| 0.20 | 0.22 | **0.209** | `likely_human` |

Both signals agree strongly (the LLM reads idiosyncratic voice; stylometry sees high
burstiness, CV ≈ 0.61), so the score sits far from the 0.5 midpoint — the system is
*confident*.

**Lower-confidence case — lightly edited AI** (remote-work reflection):
> "I've been thinking a lot about remote work lately. There are genuine tradeoffs —
> flexibility and no commute on one side, isolation and blurred work-life boundaries …"

| llm_score | sty_score | combined | attribution |
|---|---|---|---|
| 0.70 | 0.53 | **0.633** | `uncertain` |

Here the signals only partly agree — the LLM leans AI, stylometry is middling — so the
score lands in the wide uncertain band near the midpoint. The system is *not confident*,
and honestly says so rather than forcing a verdict. The 0.209 → 0.633 spread is the
meaningful variation.

## API

| Endpoint | Method | Body | Purpose |
|---|---|---|---|
| `/submit` | POST | `{text, creator_id}` | classify → label → store + log |
| `/appeal` | POST | `{content_id, creator_reasoning}` | flip status to `under_review`, log appeal |
| `/log` | GET | — | recent audit entries (newest first) |
| `/health` | GET | — | liveness |

## Setup & run

```bash
python -m venv .venv
.venv/Scripts/activate               # Windows
pip install -r .venv/requirements.txt
# GROQ_API_KEY is read from .venv/.env (or a root .env, or the environment)
python app.py                        # serves on http://127.0.0.1:5000
```

## Feature evidence (Milestone 5)

### 1. Transparency label varies by confidence

The label text changes with the confidence band — all three variants are reachable:

```
clearly human  → 0.209  likely_human
  "Likely human-written — our automated signals estimate a 21% AI-likelihood.
   This is an estimate, not a determination."

lightly-edited → 0.633  uncertain
  "Uncertain — our signals could not confidently determine authorship
   (estimated 63% AI-likelihood). No provenance label applied.
   If you believe this is mistaken, you can appeal."

clearly AI     → 0.676  likely_ai
  "Likely AI-generated — our automated signals estimate a 68% AI-likelihood.
   This is an estimate, not a determination. If you believe this is mistaken,
   you can appeal."
```

### 2. Appeals workflow

```bash
curl -s -X POST http://localhost:5000/appeal \
  -H "Content-Type: application/json" \
  -d '{"content_id": "<from a /submit response>",
       "creator_reasoning": "I wrote this myself from personal experience. I am a
        non-native English speaker and my writing style may appear more formal
        than typical."}'
```

Response:

```json
{
  "message": "Appeal received. This submission is now under review.",
  "appeal_id": "726896a8-344c-4ce1-962b-d23aa23af070",
  "content_id": "d35a4ae3-ff63-46bd-8da6-cc61a86bdda0",
  "status": "under_review"
}
```

### 3. Rate limiting — 10/min, 100/day on `/submit`

**Chosen limits: `10 per minute; 100 per day` (per client IP).** Reasoning: a genuine
creator checks their own work a handful of times — 10/minute leaves generous headroom
for that while stopping a script from flooding the Groq-backed endpoint (each call
costs an API round-trip). The 100/day ceiling caps sustained abuse that stays under the
per-minute bar. A light `20/hour` limit also guards `/appeal`. Storage is in-memory
(`storage_uri="memory://"`); a shared backend (e.g. `redis://`) would be used across
processes in production.

Evidence — 12 rapid requests, first 10 pass, the rest are throttled:

```
request 1  -> 200
request 2  -> 200
request 3  -> 200
request 4  -> 200
request 5  -> 200
request 6  -> 200
request 7  -> 200
request 8  -> 200
request 9  -> 200
request 10 -> 200
request 11 -> 429
request 12 -> 429
```

### 4. Complete audit log (`GET /log`)

Structured JSONL ([audit.py](audit.py)); each entry carries timestamp, content ID,
attribution, combined confidence, **both** individual signal scores, and appeal state.
Below: three classifications spanning all bands, plus one appeal (`status:
under_review`, `appeal_reasoning` populated).

```json
{
  "entries": [
    {
      "event": "appeal", "status": "under_review",
      "appeal_id": "726896a8-344c-4ce1-962b-d23aa23af070",
      "content_id": "d35a4ae3-ff63-46bd-8da6-cc61a86bdda0", "creator_id": "ai-1",
      "attribution": "likely_ai", "confidence": 0.676,
      "llm_score": 0.8, "sty_score": 0.489,
      "appeal_reasoning": "I wrote this myself from personal experience. I am a non-native English speaker and my writing style may appear more formal than typical.",
      "timestamp": "2026-07-04T21:58:03.667Z"
    },
    {
      "event": "classification", "status": "classified",
      "content_id": "d35a4ae3-ff63-46bd-8da6-cc61a86bdda0", "creator_id": "ai-1",
      "attribution": "likely_ai", "confidence": 0.676,
      "llm_score": 0.8, "sty_score": 0.489, "appeal_reasoning": null,
      "timestamp": "2026-07-04T21:58:03.469Z"
    },
    {
      "event": "classification", "status": "classified",
      "content_id": "dd5a48ce-033b-4209-83c5-3d624706c401", "creator_id": "edit-1",
      "attribution": "uncertain", "confidence": 0.633,
      "llm_score": 0.7, "sty_score": 0.533, "appeal_reasoning": null,
      "timestamp": "2026-07-04T21:58:02.263Z"
    },
    {
      "event": "classification", "status": "classified",
      "content_id": "e8633b02-bd03-49e1-8eb3-8a6f503d34d5", "creator_id": "human-1",
      "attribution": "likely_human", "confidence": 0.209,
      "llm_score": 0.2, "sty_score": 0.223, "appeal_reasoning": null,
      "timestamp": "2026-07-04T21:58:01.097Z"
    }
  ]
}
```

## Project layout

| File | Role |
|---|---|
| [app.py](app.py) | Flask app: `/submit`, `/appeal`, `/log`, `/health` + rate limiting |
| [signals.py](signals.py) | Signal 1 (Groq LLM) and Signal 2 (stylometry) |
| [scoring.py](scoring.py) | Combine formula, attribution bands, label variants |
| [audit.py](audit.py) | Append-only JSONL audit log |
| [store.py](store.py) | Submission state store (status tracking for appeals) |
| [planning.md](planning.md) | Architecture, signals, scoring spec, edge cases |

## Known limitations

**Formal/academic human prose is the signature failure.** Tested on a real central-bank
economics passage, the Groq signal scored it 0.80 and stylometry saw low burstiness
(sentence-length CV ≈ 0.26), so the combined score was **0.69 → `likely_ai`** — a false
positive on genuine human writing. This isn't a tuning glitch; it's structural. The
property that makes writing "read as AI" to *both* signals — uniform, hedged, evenly
punctuated sentences — is also the house style of academic, legal, and corporate prose.
No threshold fixes it, because the human author genuinely writes the way the signals are
built to flag. The mitigations are systemic, not numeric: the wide uncertain band pulls
some of these into "inconclusive," and the appeals workflow exists precisely so a
misclassified writer can contest the result. **Non-native English speakers** are exposed
to the same failure for the same reason — more formal, more uniform phrasing.

A second, milder case: **very short inputs** (< ~8 words / 2 sentences) give stylometry
no stable statistics, so it returns a neutral 0.5 and the LLM alone decides — the
multi-signal design collapses to one signal exactly when text is too thin to judge.

## Spec reflection

**Where the spec helped:** the required *false-positive analysis* in Milestone 1 forced a
decision before any code existed — trace what happens when a real human is flagged. That
single exercise set the entire downstream posture: labels phrased as *estimates* not
verdicts, a deliberately **wide uncertain band** instead of a hard AI/human cutoff, and
appeals as a first-class endpoint rather than an afterthought. Without that prompt I'd
likely have built a confident binary classifier and discovered the harm later.

**Where I diverged:** my Milestone 2 planning draft specified six fine-grained
confidence bands ("Certainly Human", "Most likely AI", …) and a bespoke combine formula
(`sum/1.5` if signal 1 > signal 2, else `sum/2`). Implementing it exposed two flaws: the
bands *overlapped* (a 0.6 score matched both "Certainly Human" and "Most likely AI"), and
the formula was *discontinuous* (nudging one signal 0.60 → 0.61 jumped the output 0.60 →
0.81). I overrode the spec with **three** non-overlapping bands and a continuous weighted
average. The divergence was justified because the original ranges were internally
contradictory — a scoring function that maps one input to two labels can't ship.

## AI usage

I used an AI coding assistant to generate scaffolding and first-draft implementations,
then reviewed and corrected its output. Two concrete instances where review mattered:

1. **Stylometry signal + scoring (Milestone 4).** I directed the AI to implement the
   stylometry function and combiner from my planning spec. It produced reasonable-looking
   code, but when I ran it against my four test inputs and printed each sub-metric, the
   **type-token-ratio sub-score was saturating at 1.0 for every input — including the
   clearly-human one.** Short texts always have high TTR (a length confound), so as
   written it was just adding a constant upward bias to every score. I overrode the
   anchors so typical short-text values land near neutral (~0.5) and dropped TTR's weight,
   documenting why. This is the "AI tools sometimes implement scoring that silently
   diverges from your spec" warning made concrete.

2. **Transparency label wording (Milestone 3→4).** The AI-generated placeholder label
   computed AI-likelihood as `100 − pct` in the human branch, so a `confidence = 0.2`
   result displayed *"80% AI-likelihood"* for a text it had just called human — backwards
   and actively misleading in a transparency feature. I caught it on the first curl test
   and fixed the label to report the same AI-likelihood figure consistently in every
   branch.

In both cases the generated code compiled and looked plausible; the errors only surfaced
by testing against inputs with known expected behavior — which is why every signal was
tested in isolation before being wired in.
