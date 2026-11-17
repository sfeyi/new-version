import io
import json
from pathlib import Path
import sys
from urllib.error import URLError

from PIL import Image
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import app
import app as guide_app
import library


@pytest.fixture
def client(tmp_path, monkeypatch):
    image = tmp_path / "reference.jpg"
    Image.new("RGB", (100, 150), "blue").save(image)
    catalog = {"photos": [{"photo_id": "tower_01", "image_path": "reference.jpg", "location_id": "clock_tower",
                          "person_count": 1, "pose": None, "viewpoint_group": "桥头平台"}]}
    path = tmp_path / "catalog.json"
    path.write_text(json.dumps(catalog), encoding="utf-8")
    monkeypatch.setenv("CAMPUS_LIBRARY_INDEX", str(path))
    return app.test_client()


def test_phone_health_identifies_service_without_loading_models(client, monkeypatch, tmp_path):
    def unavailable(*args, **kwargs):
        raise AssertionError("The phone connection check must not load models or contact the recommender")

    monkeypatch.setattr(guide_app, "models", None)
    monkeypatch.setattr(guide_app, "Models", unavailable)
    monkeypatch.setattr(guide_app, "get_models", unavailable)
    monkeypatch.setattr(library, "urlopen", unavailable)
    monkeypatch.setenv("CAMPUS_LIBRARY_INDEX", str(tmp_path / "missing-catalog.json"))

    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.mimetype == "application/json"
    assert response.json == {"status": "ok", "service": "campus-photo-guide", "version": 1}
    assert guide_app.models is None


def test_catalog_image_and_unknown_labels_remain_explicit(client):
    response = client.get("/api/library")
    photo = response.json["photos"][0]
    assert response.json["count"] == 1 and photo["pose"] is None
    assert photo["location"] == "钟楼" and "桥头平台" in photo["viewpoint"]
    assert "path" not in photo
    image = client.get("/" + photo["image_url"])
    assert image.status_code == 200 and image.mimetype == "image/jpeg"
    assert client.get("/api/library/missing/image").status_code == 404


def test_recommendation_contract_preserves_scores_and_selects_only_catalog_images(client, monkeypatch):
    seen = []
    def upstream(request, timeout):
        seen.append(json.loads(request.data))
        return io.BytesIO(json.dumps({"recommendations": [
            {"photo_id": "tower_01", "score": .91, "image_path": "../../private.jpg"},
            {"photo_id": "absent", "score": .89}], "reason": None,
            "filter_counts": {"matched": 2, "pending": 1, "excluded": 5}}).encode())
    monkeypatch.setattr(library, "urlopen", upstream)
    payload = {"query": "钟楼人像", "person_count": 1, "location_id": "clock_tower", "limit": 3}
    response = client.post("/api/recommend", json=payload)
    assert response.status_code == 200 and seen == [payload]
    assert response.json["missing_images"] == ["absent"]
    photo, = response.json["recommendations"]
    assert photo["score"] == .91 and photo["image_url"] == "api/library/tower_01/image"
    assert response.json["filter_counts"]["pending"] == 1


def test_gallery_thumbnail_is_small_oriented_and_preserves_original(client, tmp_path):
    path = tmp_path / "reference.jpg"
    photo = Image.new("RGB", (1200, 800), "green")
    exif = photo.getexif()
    exif[274] = 6
    photo.save(path, exif=exif)
    original = path.read_bytes()
    item = client.get("/api/library").json["photos"][0]
    response = client.get("/" + item["thumbnail_url"])
    assert response.status_code == 200 and response.mimetype == "image/jpeg"
    with Image.open(io.BytesIO(response.data)) as thumb:
        assert thumb.size == (213, 320)
        assert thumb.getexif().get(274, 1) == 1
    assert len(response.data) < len(original)
    assert client.get("/" + item["image_url"]).data == original
    assert client.get("/" + item["thumbnail_url"], headers={"If-None-Match": response.headers["ETag"]}).status_code == 304
    assert client.get("/api/library/missing/thumbnail").status_code == 404


def test_scene_recommendation_forwards_snapshot_and_five_card_limit(client, monkeypatch):
    payload = {"query": "自然校园", "limit": 5, "scene_image": "a" * 80}
    def upstream(request, timeout):
        assert request.full_url.endswith("/recommend-scene")
        assert json.loads(request.data) == payload
        return io.BytesIO(json.dumps({"scene_used": True, "recommendations": [
            {"photo_id": "tower_01", "aesthetic_score": 6.3}]}).encode())
    monkeypatch.setattr(library, "urlopen", upstream)
    response = client.post("/api/recommend", json=payload)
    assert response.status_code == 200 and response.json["scene_used"]
    assert response.json["recommendations"][0]["aesthetic_score"] == 6.3


def test_thumbnail_never_enlarges_a_small_image(client):
    with Image.open(io.BytesIO(client.get("/api/library/tower_01/thumbnail").data)) as thumb:
        assert thumb.size == (100, 150)


def test_upstream_failure_is_not_reported_as_an_ai_recommendation(client, monkeypatch):
    def unavailable(*args, **kwargs):
        raise URLError("offline")
    monkeypatch.setattr(library, "urlopen", unavailable)
    response = client.post("/api/recommend", json={"query": "钟楼"})
    assert response.status_code == 503 and response.json["reason"] == "upstream_unavailable"
    assert "recommendations" not in response.json
    for bad in ({"limit": True}, {"limit": 6}, {"person_count": "1"}, {"query": ["tower"]}, {"url": "http://localhost"}):
        assert client.post("/api/recommend", json=bad).status_code == 400


def test_mobile_origin_and_catalog_traversal(client, monkeypatch, tmp_path):
    origin = "https://xuehaiwuya1214.github.io"
    assert client.get("/api/library", headers={"Origin": origin}).headers["Access-Control-Allow-Origin"] == origin
    assert "Access-Control-Allow-Origin" not in client.get("/api/library", headers={"Origin": "https://unrelated.invalid"}).headers
    assert client.options("/api/recommend", headers={"Origin": origin}).headers["Access-Control-Allow-Methods"] == "GET, POST, OPTIONS"
    native_origin = "https://localhost"
    assert client.get("/api/library", headers={"Origin": native_origin}).headers["Access-Control-Allow-Origin"] == native_origin
    assert client.options("/api/recommend", headers={"Origin": native_origin}).headers["Access-Control-Allow-Origin"] == native_origin
    path = tmp_path / "bad.json"
    path.write_text(json.dumps({"photos": [{"photo_id": "escape", "image_path": "../outside.jpg"}]}))
    monkeypatch.setenv("CAMPUS_LIBRARY_INDEX", str(path))
    assert client.get("/api/library").status_code == 400
