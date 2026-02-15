from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional

app = FastAPI(title="Movie Recs API")


class CardMetrics(BaseModel):
    decision_time_ms: int
    velocity_px_per_sec: float
    initial_latency_ms: Optional[int] = None
    x_flips: int
    peak_velocity_px_per_sec: float
    direction: int  # 1 = right (good/keep), 0 = left (bad/toss)


class SessionMetricsPayload(BaseModel):
    metrics: list[CardMetrics]

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.post("/choice")
def on_choice():
    """Called when a user makes a choice. Keeps the Cloud Run instance warm."""
    return {"ok": True}


@app.get("/get-prediction")
def get_prediction():
    """Returns a prediction (stub for now)."""
    return {"prediction": "This is a prediction"}


def _calculate_total_data_score(m: CardMetrics) -> float:
    # 1. LATENCY (The "Think" Phase)
    # 0ms = 1.0, 3000ms+ = 0.0
    latency_score = 1.0 - min(1.0, (m.initial_latency_ms or 0) / 3000.0)

    # 2. X-FLIPS (The "Conflict" Phase)
    # 0 flips = 1.0, 3+ flips = 0.0
    flips_score = 1.0 - min(1.0, m.x_flips / 3.0)

    # 3. VELOCITY DYNAMICS (The "Execution" Phase)
    # Peak tells us their max burst of intent
    peak_norm = min(1.0, m.peak_velocity_px_per_sec / 5000.0)
    # Avg velocity vs Peak velocity tells us how 'smooth' the drag was
    # If smoothness is 1.0, they maintained peak speed. If 0.1, they were jerky.
    smoothness = min(1.0, m.velocity_px_per_sec / (m.peak_velocity_px_per_sec + 1))

    # 4. DECISION TIME (The "Effort" Phase)
    # Even if they move fast, staying on the screen for a long time suggests lingering.
    # 0ms = 1.0, 5000ms+ = 0.0
    time_score = 1.0 - min(1.0, m.decision_time_ms / 5000.0)

    # --- THE WEIGHTED CONVICTION ---
    # We now use all 5 variables. Initial latency weighted lightly.
    conviction = (
        (latency_score * 0.20) +  # Did they start fast?
        (flips_score * 0.30) +    # Did they stay the course?
        (peak_norm * 0.20) +      # Was it aggressive?
        (smoothness * 0.15) +     # Was it a smooth movement?
        (time_score * 0.15)       # Was it a quick total interaction?
    )

    # --- ASYMMETRIC MAPPING (Direction + Forgiveness) ---
    if m.direction == 1:
        # Keep: Scale 5.0 -> 10.0
        final_score = 5.0 + (conviction * 5.0)
    else:
        # Toss: Scale 5.0 -> 1.0 with 10% forgiveness
        forgiving_conviction = conviction * 0.9
        final_score = 5.0 - (forgiving_conviction * 4.0)

    return round(max(1.0, min(10.0, final_score)), 1)


@app.post("/session-metrics")
def session_metrics(payload: SessionMetricsPayload):
    """Receive drag metrics after user completes all 4 images. Compute drift scores and return them."""
    print("\n" + "=" * 60)
    print("SESSION METRICS (after 4 images)")
    print("=" * 60)
    drift_scores = []
    for i, m in enumerate(payload.metrics, 1):
        drift = _calculate_total_data_score(m)
        drift_scores.append(drift)
        print(f"\n--- Image {i} ---")
        print(f"  Decision time:        {m.decision_time_ms} ms")
        print(f"  Velocity (avg):       {m.velocity_px_per_sec} px/s")
        print(f"  Initial latency:      {m.initial_latency_ms} ms (time-to-first-movement)")
        print(f"  X-flips:              {m.x_flips} (direction changes)")
        print(f"  Peak velocity:        {m.peak_velocity_px_per_sec} px/s")
        print(f"  Direction:            {m.direction} (1=right/keep, 0=left/toss)")
        print(f"  Kinematic drift:      {drift}")
    overall = round(sum(drift_scores) / len(drift_scores), 1) if drift_scores else 0.0
    print(f"\n  Overall drift (avg):   {overall}")
    print("\n" + "=" * 60 + "\n")
    return {
        "ok": True,
        "drift_scores": drift_scores,
        "overall_drift": overall,
    }
