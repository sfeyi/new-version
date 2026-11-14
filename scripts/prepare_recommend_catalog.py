"""把共享 sources.json 转为成员 2 可读的图库；只复制原图，不猜测未确认标签。"""
import argparse
import json
from pathlib import Path
import shutil
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from library import index_path, read_library


def export(source, output):
    source, output = source.resolve(), output.resolve()
    photos = read_library(source, source.stat().st_mtime_ns)
    original = json.loads(source.read_text(encoding="utf-8-sig"))
    confirmed = {p["photo_id"]: p for p in original["photos"]} if isinstance(original, dict) else {}
    (output / "images").mkdir(parents=True, exist_ok=True)
    rows = []
    for photo in photos.values():
        image = photo["path"]
        relative = "images/" + photo["photo_id"] + image.suffix.lower()
        if image != output / relative:
            shutil.copy2(image, output / relative)
        row = {**{key: photo[key] for key in ("photo_id", "resource_id", "location_id", "viewpoint_group",
                     "person_count", "framing", "pose", "background_objects")}, "image_path": relative,
                     "viewpoint_status": "unavailable" if photo["viewpoint_status"] == "unavailable" else "unknown"}
        rows.append({**confirmed.get(photo["photo_id"], row), "image_path": relative})
    catalog = output / "catalog.json"
    meta = {k: original[k] for k in ("dataset_id", "version")} if isinstance(original, dict) else {"dataset_id": "campus_linked", "version": "1"}
    catalog.write_text(json.dumps({**meta, "photos": rows}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return catalog, len(rows)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=index_path())
    parser.add_argument("--output", type=Path, default=ROOT / "build/recommend-library")
    args = parser.parse_args()
    path, count = export(args.source, args.output)
    print(f"{count} photos -> {path}")
    print("Unknown person/pose/framing labels remain null; use the teammate's confirmed catalog for those filters.")
