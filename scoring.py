"""Confidence scoring for Provenance Guard.

Combines the two detection signals into a single calibrated confidence score
(P(AI) in 0..1) and maps that score to an attribution band + transparency label.

Design decisions (see planning.md, Milestone 2):
- The LLM signal reads meaning but has no ground truth; stylometry has objective
  math but is meaning-blind. The LLM is the more reliable holistic judge, so it
  carries more weight in the combine.
- The false-positive analysis argues for HUMILITY: a WIDE "uncertain" band, so
  that ambiguous inputs (formal human prose, lightly edited AI) are labeled
  inconclusive rather than confidently misattributed.
"""

# Combine weights (must sum to 1.0). LLM is weighted higher — it's the more
# reliable, meaning-aware signal; stylometry is a noisy, form-only corroborator.
W_LLM = 0.60
W_STY = 0.40

# Attribution bands over the combined confidence (P(AI)). Non-overlapping and
# exhaustive. The uncertain band is deliberately wide (0.35–0.65).
BAND_HUMAN_MAX = 0.35   # [0.00, 0.35)  -> likely_human
BAND_AI_MIN = 0.65      # [0.65, 1.00]  -> likely_ai
                        # [0.35, 0.65)  -> uncertain


def combine(llm_score: float, sty_score: float) -> float:
    """Weighted combination of the two signals into P(AI) in 0..1."""
    confidence = W_LLM * llm_score + W_STY * sty_score
    return round(max(0.0, min(1.0, confidence)), 3)


def attribution_for(confidence: float) -> str:
    """Map a combined confidence score to one of three attribution categories."""
    if confidence < BAND_HUMAN_MAX:
        return "likely_human"
    if confidence >= BAND_AI_MIN:
        return "likely_ai"
    return "uncertain"


_APPEAL_CTA = " If you believe this is mistaken, you can appeal."


def label_for(attribution: str, confidence: float) -> str:
    """Human-readable transparency label. Always an estimate, never a verdict.

    Three variants, one per attribution band. The likely_ai and uncertain
    variants carry an appeal call-to-action (those are the results a creator
    would dispute); the likely_human variant is the favorable outcome.
    """
    pct = round(confidence * 100)  # confidence == estimated AI-likelihood
    if attribution == "likely_ai":
        return (
            f"Likely AI-generated — our automated signals estimate a {pct}% "
            "AI-likelihood. This is an estimate, not a determination." + _APPEAL_CTA
        )
    if attribution == "likely_human":
        return (
            f"Likely human-written — our automated signals estimate a {pct}% "
            "AI-likelihood. This is an estimate, not a determination."
        )
    return (
        f"Uncertain — our signals could not confidently determine authorship "
        f"(estimated {pct}% AI-likelihood). No provenance label applied." + _APPEAL_CTA
    )


def score(llm_score: float, sty_score: float) -> dict:
    """Convenience: run the full combine -> attribution -> label pipeline."""
    confidence = combine(llm_score, sty_score)
    attribution = attribution_for(confidence)
    return {
        "confidence": confidence,
        "attribution": attribution,
        "label": label_for(attribution, confidence),
    }
