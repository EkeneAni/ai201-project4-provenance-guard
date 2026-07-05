"""Provenance Guard — Flask application.

Full pipeline (Milestone 5):
  POST /submit  -> two signals -> calibrated confidence -> transparency label
                   -> submission store + audit log -> response
  POST /appeal  -> status update (under_review) -> audit log -> confirmation
  GET  /log     -> structured audit entries (submissions + appeals)
  GET  /health  -> liveness

Detection signals live in signals.py, scoring/labels in scoring.py, the
append-only audit log in audit.py, and current submission state in store.py.
"""

import uuid

from flask import Flask, jsonify, request
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

from audit import append_entry, get_log
from scoring import score
from signals import llm_signal, stylometry_signal
from store import get_submission, save_submission, update_submission

app = Flask(__name__)

# Per-IP rate limiting protects the Groq-backed /submit endpoint from abuse.
# In-memory storage is fine for local/dev; a shared store (e.g. redis://) would
# be used across multiple processes in production.
limiter = Limiter(
    get_remote_address,
    app=app,
    default_limits=[],
    storage_uri="memory://",
)

MAX_TEXT_LEN = 20_000  # guardrail against oversized payloads


@app.get("/health")
def health():
    return jsonify({"status": "ok"})


@app.post("/submit")
@limiter.limit("10 per minute;100 per day")
def submit():
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return jsonify({"error": "Request body must be a JSON object."}), 400

    text = body.get("text")
    creator_id = body.get("creator_id")

    if not isinstance(text, str) or not text.strip():
        return jsonify({"error": "Field 'text' is required and must be a non-empty string."}), 400
    if not isinstance(creator_id, str) or not creator_id.strip():
        return jsonify({"error": "Field 'creator_id' is required and must be a non-empty string."}), 400
    if len(text) > MAX_TEXT_LEN:
        return jsonify({"error": f"Field 'text' exceeds {MAX_TEXT_LEN} characters."}), 413

    content_id = str(uuid.uuid4())

    # --- Signal 1: Groq LLM classifier ---
    signal1 = llm_signal(text)
    llm_score = signal1["llm_score"]

    # --- Signal 2: stylometry (pure Python) ---
    signal2 = stylometry_signal(text)
    sty_score = signal2["sty_score"]

    # --- Confidence + attribution + label (real calibrated scoring) ---
    result = score(llm_score, sty_score)
    confidence = result["confidence"]
    attribution = result["attribution"]
    label = result["label"]

    # --- Persist current state so /appeal can look it up later ---
    save_submission(content_id, {
        "content_id": content_id,
        "creator_id": creator_id,
        "attribution": attribution,
        "confidence": confidence,
        "llm_score": llm_score,
        "sty_score": sty_score,
        "status": "classified",
        "appeal_reasoning": None,
    })

    # --- Audit log (structured; now records both signals + combined result) ---
    entry = append_entry({
        "event": "classification",
        "content_id": content_id,
        "creator_id": creator_id,
        "attribution": attribution,
        "confidence": confidence,
        "llm_score": llm_score,
        "sty_score": sty_score,
        "appeal_reasoning": None,
        "status": "classified",
    })

    return jsonify({
        "content_id": content_id,
        "creator_id": creator_id,
        "attribution": attribution,
        "confidence": confidence,
        "label": label,
        "signals": {"llm": signal1, "stylometry": signal2},
        "timestamp": entry["timestamp"],
    })


@app.post("/appeal")
@limiter.limit("20 per hour")
def appeal():
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return jsonify({"error": "Request body must be a JSON object."}), 400

    content_id = body.get("content_id")
    creator_reasoning = body.get("creator_reasoning")

    if not isinstance(content_id, str) or not content_id.strip():
        return jsonify({"error": "Field 'content_id' is required."}), 400
    if not isinstance(creator_reasoning, str) or not creator_reasoning.strip():
        return jsonify({"error": "Field 'creator_reasoning' is required."}), 400

    original = get_submission(content_id)
    if original is None:
        return jsonify({"error": f"Unknown content_id: {content_id}"}), 404

    appeal_id = str(uuid.uuid4())

    # --- Update the content's current status to under_review ---
    update_submission(
        content_id,
        status="under_review",
        appeal_id=appeal_id,
        appeal_reasoning=creator_reasoning,
    )

    # --- Log the appeal ALONGSIDE the original classification decision ---
    entry = append_entry({
        "event": "appeal",
        "appeal_id": appeal_id,
        "content_id": content_id,
        "creator_id": original.get("creator_id"),
        "status": "under_review",
        "appeal_reasoning": creator_reasoning,
        # original classification, carried for the reviewer's context
        "attribution": original.get("attribution"),
        "confidence": original.get("confidence"),
        "llm_score": original.get("llm_score"),
        "sty_score": original.get("sty_score"),
    })

    return jsonify({
        "message": "Appeal received. This submission is now under review.",
        "appeal_id": appeal_id,
        "content_id": content_id,
        "status": "under_review",
        "timestamp": entry["timestamp"],
    })


@app.get("/log")
def log():
    # No auth here by design — this endpoint exists for documentation and
    # grading visibility. A real system would require authentication.
    limit = request.args.get("limit", default=50, type=int)
    return jsonify({"entries": get_log(limit=limit)})


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=True)
