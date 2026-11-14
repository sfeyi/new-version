"""共享图库与成员 2 推荐服务的适配层，不加载其 PyTorch 模型。"""
import json
import io
import os
import re
from functools import lru_cache
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from flask import Blueprint, jsonify, request, send_file
from PIL import Image, ImageOps


ROOT = Path(__file__).resolve().parent
api = Blueprint("library", __name__)
LOCATIONS = {
    "情人坡": "qingrenpo_lawn", "科学与艺术大楼": "science_arts_building", "国科大桥": "ucas_bridge",
    "钟楼": "clock_tower", "圆塔": "round_tower", "中丹学院": "sino_danish_college",
    "三四食堂": "cafeterias_3_4",
}
FILTERS = ("location_id", "person_count", "framing", "pose")


def index_path():
    configured = os.environ.get("CAMPUS_LIBRARY_INDEX") or os.environ.get("PHOTO_RECOMMEND_CATALOG")
    if configured:
        return Path(configured).resolve()
    candidates = [ROOT.parent / "campus-photo-recommend/data/formal/catalog.json",
                  ROOT.parent / "校园摄影参考库_队友分享_98张/统一参考库/sources.json",
                  ROOT.parent / "campus-photo-recommend/data/sample/catalog.json"]
    return next((p for p in candidates if p.is_file()), candidates[1])


@lru_cache(maxsize=4)
def read_library(path, modified):
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    source_rows = data if isinstance(data, list) else data["photos"]
    photos = {}
    for row in source_rows:
        key = row.get("photo_id") or row.get("image_id")
        if not isinstance(key, str) or not re.fullmatch(r"[A-Za-z0-9_-]+", key) or key in photos:
            raise ValueError("图库照片编号无效或重复")
        relative = Path(row.get("image_path") or row["file"])
        image = (path.parent / relative).resolve()
        if relative.is_absolute() or not image.is_relative_to(path.parent) or image.suffix.lower() not in {".jpg", ".jpeg", ".png", ".webp"}:
            raise ValueError("图库图片路径无效")
        if not image.is_file() and row.get("source", {}).get("archive_path"):
            shared = (ROOT.parent / "校园摄影参考库_队友分享_98张").resolve()
            archived = (shared / row["source"]["archive_path"]).resolve()
            if archived.is_relative_to(shared) and archived.is_file():
                image = archived
        if not image.is_file():
            continue
        location = row.get("location") or row.get("location_name", "")
        location_id = row.get("location_id") or LOCATIONS.get(location)
        if not location:
            location = next((label for label, code in LOCATIONS.items() if code == location_id), location_id or "待确认地点")
        viewpoint = row.get("viewpoint_group") or ""
        # 不从描述猜测人数、姿势等硬条件，未知标签继续保持未知。
        photos[key] = {"photo_id": key, "resource_id": row.get("resource_id", key), "path": image,
                       "location": location, "location_id": location_id, "viewpoint_group": viewpoint,
                       "viewpoint": " · ".join(s.replace("_", " / ") for s in (location, viewpoint) if s),
                       "description": row.get("description") or row.get("visible_scene") or "",
                       "person_count": row.get("person_count"), "framing": row.get("framing"), "pose": row.get("pose"),
                       "background_objects": row.get("background_objects"),
                       "viewpoint_status": "unavailable" if row.get("is_collage") else row.get("viewpoint_status", "unknown"),
                       "image_url": "api/library/" + key + "/image",
                       "thumbnail_url": "api/library/" + key + "/thumbnail"}
    return photos


def library():
    path = index_path()
    if not path.is_file():
        return {}
    return read_library(path, path.stat().st_mtime_ns)


def public(photo):
    return {k: v for k, v in photo.items() if k != "path"}


def options(data):
    if not isinstance(data, dict):
        raise ValueError("请提供检索条件")
    allowed = {*FILTERS, "query", "limit", "aesthetic_weight", "required_objects", "forbidden_poses"}
    if set(data) - allowed:
        raise ValueError("检索条件包含未知字段")
    for key in ("query", "location_id", "framing", "pose"):
        if key in data and (not isinstance(data[key], str) or len(data[key]) > (256 if key == "query" else 100)):
            raise ValueError("检索条件格式不正确")
    for key in ("person_count", "limit"):
        if key in data and (type(data[key]) is not int or not (0 <= data[key] <= 100 if key == "person_count" else 1 <= data[key] <= 3)):
            raise ValueError("人数或推荐数量无效")
    if "aesthetic_weight" in data and (type(data["aesthetic_weight"]) not in (int, float) or not 0 <= data["aesthetic_weight"] <= 1):
        raise ValueError("美学权重应为 0 到 1")
    for key in ("required_objects", "forbidden_poses"):
        if key in data and (not isinstance(data[key], list) or len(data[key]) > 20 or
                            any(not isinstance(v, str) or not 1 <= len(v) <= 100 for v in data[key])):
            raise ValueError("物体或姿态条件无效")
    return data


@api.get("/api/library")
def catalog():
    photos = library()
    locations = {p["location_id"]: p["location"] for p in photos.values() if p["location_id"]}
    return jsonify(photos=[public(p) for p in photos.values()],
                   locations=[{"id": code, "name": name} for code, name in locations.items()],
                   count=len(photos), mode="catalog")


@api.get("/api/library/<photo_id>/image")
def image(photo_id):
    photo = library().get(photo_id)
    if photo is None:
        return jsonify(error="图库中没有这张照片"), 404
    # 仅按目录中已校验的编号取文件，不信任推荐结果里的相对路径或 URL。
    return send_file(photo["path"], conditional=True)


@lru_cache(maxsize=128)
def thumbnail_bytes(path, modified, size):
    with Image.open(path) as source:
        source = ImageOps.exif_transpose(source).convert("RGB")
        source.thumbnail((320, 320), Image.Resampling.LANCZOS)
        output = io.BytesIO()
        source.save(output, format="JPEG", quality=72)
        return output.getvalue()


@api.get("/api/library/<photo_id>/thumbnail")
def thumbnail(photo_id):
    photo = library().get(photo_id)
    if photo is None:
        return jsonify(error="图库中没有这张照片"), 404
    path = photo["path"]
    stat = path.stat()
    data = thumbnail_bytes(path, stat.st_mtime_ns, stat.st_size)
    return send_file(io.BytesIO(data), mimetype="image/jpeg", max_age=3600,
                     etag=f"{photo_id}-{stat.st_mtime_ns}-{stat.st_size}")


@api.post("/api/recommend")
def recommend():
    payload = options(request.get_json(silent=True))
    base = os.environ.get("PHOTO_RECOMMEND_URL", "http://127.0.0.1:8000").rstrip("/")
    if urlparse(base).scheme not in {"http", "https"}:
        return jsonify(error="推荐服务地址配置无效"), 503
    try:
        call = Request(base + "/recommend", data=json.dumps(payload, ensure_ascii=False).encode(),
                       headers={"Content-Type": "application/json"}, method="POST")
        with urlopen(call, timeout=90) as response:
            result = json.loads(response.read(2_000_001))
    except HTTPError as exc:
        return jsonify(error="推荐条件未通过校验" if exc.code == 422 else "推荐模型或特征缓存尚未准备，可先筛选图库",
                       reason="upstream_unavailable"), 422 if exc.code == 422 else 503
    except (URLError, TimeoutError, OSError, ValueError):
        return jsonify(error="暂时连接不到 AI 推荐服务，可先按地点和文字筛选图库", reason="upstream_unavailable"), 503
    if not isinstance(result, dict) or not isinstance(result.get("recommendations"), list):
        return jsonify(error="推荐服务返回格式不正确"), 502
    photos = library()
    enriched, missing = [], []
    for item in result["recommendations"][:payload.get("limit", 3)]:
        if not isinstance(item, dict) or not isinstance(item.get("photo_id"), str):
            return jsonify(error="推荐服务返回照片编号无效"), 502
        photo = photos.get(item["photo_id"])
        if photo is None:
            missing.append(item["photo_id"])
        else:
            enriched.append({**item, **public(photo)})
    return jsonify({**result, "recommendations": enriched, "missing_images": missing, "mode": "recommend"})


@api.after_app_request
def allow_mobile(response):
    if request.path.startswith(("/api/library", "/api/recommend")):
        allowed = os.environ.get("CAMPUS_RECOMMEND_ORIGINS", "https://xuehaiwuya1214.github.io,https://localhost").split(",")
        origin = request.headers.get("Origin")
        if origin and origin in [value.strip() for value in allowed]:
            response.headers["Access-Control-Allow-Origin"] = origin
            response.headers["Access-Control-Allow-Headers"] = "Content-Type"
            response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
            response.vary.add("Origin")
    return response
