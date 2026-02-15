import os
import random
from datetime import datetime
from pathlib import Path
from typing import Literal, Optional
import httpx
import pandas as pd
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from rectools import Columns
from rectools.dataset import Dataset

SERVER_DIR = Path(__file__).resolve().parent
# Load .env from server dir so the key is found when running uvicorn from project root
load_dotenv(SERVER_DIR / ".env")
# TMDB: you can set either or both. We prefer the v3 API key (short) for ?api_key=; else use Read Access Token (JWT) as Bearer.
TMDB_API_KEY = os.getenv("TMDB_API_KEY") or os.getenv("MOVID_DB_KEY") or os.getenv("MOVIE_DB_KEY")
TMDB_READ_ACCESS_TOKEN = os.getenv("TMDB_READ_ACCESS_TOKEN") or os.getenv("MOVID_DB_READ_TOKEN")
TMDB_BASE = "https://api.themoviedb.org/3"
TMDB_IMAGE_BASE = "https://image.tmdb.org/t/p/w500"
if not TMDB_API_KEY and not TMDB_READ_ACCESS_TOKEN:
    print("[TMDB] No key found. Set TMDB_API_KEY (v3) and/or TMDB_READ_ACCESS_TOKEN in server/.env")

# Lazy-loaded model and catalog (loaded on first /recommend or when needed)
_recommend_model = None
_catalog_item_ids = None
_items_df = None
DATA_DIR = SERVER_DIR / "data" / "data_en"
CKPT_PATH = SERVER_DIR / "model" / "epoch=20-NDCG@10=0.03.ckpt"


def _get_model():
    global _recommend_model
    if _recommend_model is None:
        try:
            try:
                from server.model_loader import load_model
            except ImportError:
                from model_loader import load_model
            _recommend_model = load_model(CKPT_PATH)
        except Exception as e:
            raise HTTPException(status_code=503, detail=f"Model failed to load: {e}")
    return _recommend_model


def _get_catalog_item_ids():
    """Return list of valid item_ids from training catalog (for cold start)."""
    global _catalog_item_ids
    if _catalog_item_ids is None:
        items_path = DATA_DIR / "items_en.csv"
        if not items_path.exists():
            return []
        df = pd.read_csv(items_path)
        _catalog_item_ids = df["item_id"].astype(int).tolist()
    return _catalog_item_ids


def _get_items_df():
    """Return catalog DataFrame with movie metadata."""
    global _items_df
    if _items_df is None:
        items_path = DATA_DIR / "items_en.csv"
        if not items_path.exists():
            _items_df = pd.DataFrame()
        else:
            _items_df = pd.read_csv(items_path)
            _items_df["item_id"] = _items_df["item_id"].astype(int)
    return _items_df


def _tmdb_auth_options():
    """Build list of (headers, params) for TMDB. Tries v3 API key first, then Read Access Token (Bearer)."""
    options = []
    key = (TMDB_API_KEY or "").strip()
    if key and not ("." in key and len(key) > 40):
        options.append(({}, {"api_key": key}))
    token = (TMDB_READ_ACCESS_TOKEN or "").strip()
    if token:
        options.append(({"Authorization": f"Bearer {token}"}, {}))
    return options


def _tmdb_poster_url(title: str, year: Optional[float] = None, title_orig: Optional[str] = None) -> Optional[str]:
    """Search TMDB by title (+ optional year). Tries title_orig if title returns no poster. Tries both auth keys."""
    auth_options = _tmdb_auth_options()
    if not auth_options:
        return None
    for query in [title, title_orig]:
        if not query or (isinstance(query, float) and pd.isna(query)):
            continue
        query = str(query).strip()
        if not query:
            continue
        base_params = {"query": query, "language": "en-US"}
        if year and not pd.isna(year):
            base_params["year"] = int(year)
        for headers, auth_params in auth_options:
            params = {**auth_params, **base_params}
            try:
                with httpx.Client(timeout=10.0) as client:
                    r = client.get(f"{TMDB_BASE}/search/movie", params=params, headers=headers)
                    r.raise_for_status()
                    data = r.json()
                    results = data.get("results") or []
                    if not results:
                        break
                    poster_path = results[0].get("poster_path")
                    if not poster_path:
                        break
                    return f"{TMDB_IMAGE_BASE}{poster_path}"
            except httpx.HTTPStatusError as e:
                if e.response.status_code == 401:
                    continue
                if query == title:
                    print(f"[TMDB] search failed for {title!r}: {e}")
                break
            except Exception as e:
                if query == title:
                    print(f"[TMDB] search failed for {title!r}: {e}")
                break
    return None


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


# --- Recommendation API (frontend sends history, backend returns item_ids) ---
class InteractionItem(BaseModel):
    """One interaction: user saw/rated an item (e.g. swiped keep/toss)."""
    item_id: int = Field(..., description="Catalog item ID (from training catalog / items_en)")
    datetime: Optional[str] = Field(None, description="ISO datetime string; defaults to now if omitted")
    weight: Optional[float] = Field(1.0, description="Strength of interaction (e.g. 1=toss, 3=keep)")


class RecommendRequest(BaseModel):
    """Request body for POST /recommend. Matches rectools schema: user_id, item_id, datetime, weight."""
    user_id: int = Field(..., description="External user/session ID")
    interactions: list[InteractionItem] = Field(
        default_factory=list,
        description="User's interaction history (chronological). Empty = cold start.",
    )
    k: int = Field(10, ge=1, le=100, description="Number of recommendations to return")


class RecommendResponse(BaseModel):
    """Top-k recommendations in rank order (rank 1 = best)."""
    item_ids: list[int] = Field(..., description="Recommended catalog item_ids, best first")
    ranks: list[int] = Field(..., description="1-based rank for each item (1 = top recommendation)")
    k: int = Field(..., description="Requested k (number of top recommendations)")
    source: Literal["cold_start", "model"] = Field(
        ..., description="cold_start = random from catalog; model = SASRec predictions from your history"
    )


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


class MovieInfo(BaseModel):
    item_id: int
    title: str
    release_year: Optional[float] = None
    poster_url: Optional[str] = None


@app.get("/movies", response_model=list[MovieInfo])
def get_movies(item_ids: str):
    """
    Get movie details for given item_ids. Query: ?item_ids=10440,14488,512,3734
    Returns title, year, and poster_url from TMDB when API key is set.
    """
    try:
        ids = [int(x.strip()) for x in item_ids.split(",") if x.strip()]
    except ValueError:
        raise HTTPException(status_code=400, detail="item_ids must be comma-separated integers")
    if not ids:
        return []
    df = _get_items_df()
    if df.empty:
        return []
    out = []
    for iid in ids:
        row = df[df["item_id"] == iid]
        if row.empty:
            continue
        row = row.iloc[0]
        title = str(row["title"]) if pd.notna(row.get("title")) else ""
        title_orig = str(row["title_orig"]) if pd.notna(row.get("title_orig")) else None
        year = float(row["release_year"]) if pd.notna(row.get("release_year")) else None
        poster_url = _tmdb_poster_url(title, year, title_orig)
        out.append(MovieInfo(item_id=int(iid), title=title, release_year=year, poster_url=poster_url))
    return out


@app.post("/recommend", response_model=RecommendResponse)
def recommend(req: RecommendRequest) -> RecommendResponse:
    """
    Get movie recommendations for a user given their interaction history.
    Frontend should send: user_id (e.g. session id), interactions (item_id + optional datetime/weight).
    Returns ranked list of item_ids from the training catalog.
    """
    k = req.k
    catalog = _get_catalog_item_ids()

    if not req.interactions:
        # Cold start: return top k (random sample from catalog)
        if not catalog:
            print("[RECOMMEND] cold_start — no catalog, returning []")
            return RecommendResponse(item_ids=[], ranks=[], k=k, source="cold_start")
        n = min(k, len(catalog))
        item_ids = random.sample(catalog, n)
        ranks = list(range(1, n + 1))
        resp = RecommendResponse(item_ids=item_ids, ranks=ranks, k=k, source="cold_start")
        print("[RECOMMEND] cold_start top-{} (random, not model): {}".format(k, list(zip(ranks, item_ids))))
        return resp

    model = _get_model()
    now = datetime.utcnow()
    rows = []
    for i in req.interactions:
        dt = now
        if i.datetime:
            try:
                dt = datetime.fromisoformat(i.datetime.replace("Z", "+00:00"))
            except ValueError:
                pass
        weight = float(i.weight) if i.weight is not None else 1.0
        rows.append({
            "user_id": req.user_id,
            "item_id": i.item_id,
            "datetime": dt,
            "weight": weight,
        })

    # Same schema as training: user_id, item_id, datetime, weight
    df = pd.DataFrame(rows)
    dataset = Dataset.construct(df)

    recos_df = model.recommend(
        users=[req.user_id],
        dataset=dataset,
        filter_viewed=True,
        k=k,
    )
    # recos_df has user_id, item_id, rank (external ids) — already ordered by rank
    if recos_df is None or recos_df.empty:
        print("[RECOMMEND] model returned empty for user_id={}".format(req.user_id))
        return RecommendResponse(item_ids=[], ranks=[], k=k, source="model")
    item_col = "item_id" if "item_id" in recos_df.columns else Columns.Item
    rank_col = "rank" if "rank" in recos_df.columns else None
    item_ids = recos_df[item_col].astype(int).tolist()[:k]
    if rank_col and rank_col in recos_df.columns:
        ranks = recos_df[rank_col].astype(int).tolist()[:k]
    else:
        ranks = list(range(1, len(item_ids) + 1))
    resp = RecommendResponse(item_ids=item_ids, ranks=ranks, k=k, source="model")
    print("[RECOMMEND] model top-{} for user_id={}: {}".format(k, req.user_id, list(zip(ranks, item_ids))))
    return resp


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
