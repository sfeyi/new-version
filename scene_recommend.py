"""Scene + style retrieval, using member 2's SigLIP and cached NIMA features.

Run with the recommendation virtualenv; launch.py supplies its source directory.
The shortlist is relevance-gated, then ordered by aesthetic score.
"""
import base64
import io
import os
from functools import lru_cache
from pathlib import Path
from threading import Lock

from fastapi import FastAPI, HTTPException
from PIL import Image, ImageOps
from pydantic import Field

from photo_recommend.api import RecommendRequest as OriginalRequest
from photo_recommend.preprocess import Models, configure_model_cache, read_catalog
from photo_recommend.ranking import filter_photos
from photo_recommend.recommend import FORMAL_CATALOG, load_candidates

app = FastAPI(title="校园场景与风格推荐")
lock = Lock()


class Request(OriginalRequest):
    limit: int = Field(default=5, ge=1, le=5)
    scene_image: str | None = Field(default=None, max_length=1_400_000)


@lru_cache(maxsize=1)
def models():
    configure_model_cache()
    import torch
    torch.set_num_threads(4)
    return Models(torch, "cuda" if torch.cuda.is_available() else "cpu")


def decode_scene(encoded):
    try:
        data = base64.b64decode(encoded, validate=True)
        if len(data) > 1_000_000:
            raise ValueError("Scene image too large")
        with Image.open(io.BytesIO(data)) as source:
            if min(source.size) < 32 or source.width * source.height > 4_000_000:
                raise ValueError("Invalid scene dimensions")
            image = ImageOps.exif_transpose(source).convert("RGB")
            image.thumbnail((768, 768))
            return image
    except (ValueError, OSError) as exc:
        raise ValueError("当前画面无效，请重新取景") from exc


def rank_scene(candidates, scene_vector, text_vector, limit):
    """Keep the scene/style shortlist separate from the final aesthetic order."""
    scored = []
    for photo, vector, aesthetic in candidates:
        scene = float(vector @ scene_vector) if scene_vector is not None else None
        text = float(vector @ text_vector) if text_vector is not None else None
        relevance = .7 * scene + .3 * text if scene is not None and text is not None else scene if scene is not None else text
        scored.append((photo, vector, float(aesthetic), scene, text, relevance if relevance is not None else 0.))
    scored.sort(key=lambda row: (-row[5], row[0]["photo_id"]))
    if not scored:
        return []
    # Reject weak relative matches before aesthetics can promote unrelated scenes.
    floor = scored[0][5] - .12
    shortlist = [row for row in scored[:max(15, limit * 3)] if row[5] >= floor]
    shortlist.sort(key=lambda row: (-row[2], -row[5], row[0]["photo_id"]))
    selected, vectors = [], []
    for photo, vector, aesthetic, scene, text, relevance in shortlist:
        if any(float(vector @ old) >= .98 for old in vectors):
            continue
        selected.append({"photo_id": photo["photo_id"], "aesthetic_score": aesthetic,
                         "scene_similarity": scene, "text_similarity": text,
                         "relevance_score": relevance, "score": aesthetic})
        vectors.append(vector)
        if len(selected) >= limit:
            break
    return selected


def search(request):
    path = Path(os.environ.get("PHOTO_RECOMMEND_CATALOG", FORMAL_CATALOG)).resolve()
    catalog = read_catalog(path)
    filters = request.model_dump(exclude={"query", "limit", "aesthetic_weight", "scene_image"})
    matches, pending, excluded = filter_photos(catalog["photos"], **filters)
    candidates, missing = load_candidates(path, catalog, matches)
    image = decode_scene(request.scene_image) if request.scene_image else None
    scene_vector = text_vector = None
    if candidates and (image is not None or request.query.strip()):
        with lock:
            encoder = models()
            if image is not None:
                processor, model = encoder.siglip
                inputs = processor(images=image, return_tensors="pt").to(encoder.device)
                with encoder.torch.inference_mode():
                    embedding = model.get_image_features(**inputs).pooler_output
                    scene_vector = encoder.torch.nn.functional.normalize(embedding, dim=-1)[0].cpu().numpy()
            if request.query.strip():
                text_vector = encoder.text_vector(request.query.strip())
    result = rank_scene(candidates, scene_vector, text_vector, request.limit)
    return {"recommendations": result, "scene_used": image is not None,
            "ranking": "relevance_shortlist_then_aesthetic", "missing_features": missing,
            "filter_counts": {"matched": len(matches), "pending": len(pending), "excluded": len(excluded)},
            "reason": None if result else "features_unavailable" if matches else "no_confirmed_matches"}


@app.get("/health")
def health():
    return {"status": "ok", "scene_recommendation": 1}


@app.post("/recommend")
@app.post("/recommend-scene")
def recommend(request: Request):
    try:
        return search(request)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except (OSError, RuntimeError) as exc:
        raise HTTPException(status_code=503, detail="推荐模型或特征缓存未准备好") from exc
