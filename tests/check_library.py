"""在本机用每张参考照自身作为输入，检查建模和四个阈值，不需要上传图片。"""
import argparse
import hashlib
from collections import Counter
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import campus_notes, decode, get_models
from guide import Guide


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    args = parser.parse_args()
    counts = Counter()
    for path in sorted(args.directory.rglob("*")):
        if path.suffix.lower() not in {".jpg", ".jpeg", ".webp", ".png"}:
            continue
        counts["photos"] += 1
        try:
            data = path.read_bytes()
            frame = decode(data)
            guide = Guide(get_models(), frame, campus_notes().get(hashlib.sha256(data).hexdigest(), ""))
            result = guide.analyze(frame, 0, 0.)
        except ValueError as exc:
            print(path.stem, str(exc))
            continue
        counts["targets"] += guide.target is not None
        counts["building_outlines" if guide.reference["buildingOutline"] else "background_rings"] += 1
        outline = guide.reference["outline"]
        if outline:
            counts[outline["kind"] + "_outlines"] += 1
        aim, bg, position, pose = result["checks"]
        counts["aim"] += aim
        counts["background"] += bg
        counts["position"] += position
        counts["pose"] += pose
        counts["all_four"] += aim and bg and position and pose
        if not all(result["checks"]):
            print(path.stem, dict(zip(("background", "framing", "position", "pose"), result["checks"])))
    print(dict(counts))


if __name__ == "__main__":
    main()

