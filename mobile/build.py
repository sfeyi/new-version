"""python mobile/build.py produces a self-contained GitHub Pages site in build/mobile."""
import base64
import hashlib
import io
import json
import re
import shutil
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

from vendor import CACHE, ROOT, digest, fetch


OUT = ROOT / "build" / "mobile"
MOBILE = ROOT / "mobile"
SHARED = ("inference", "guide", "people", "posture", "background", "background_shapes", "outline", "building", "landmarks")


def build():
    assets = []

    def write(name, data, shell=False):
        target = OUT / name
        target.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(data, Path):
            shutil.copyfile(data, target)
        elif isinstance(data, bytes):
            target.write_bytes(data)
        else:
            target.write_text(data, encoding="utf-8", newline="\n")
        sha = bytes.fromhex(digest(target))
        assets.append(dict(path=name, size=target.stat().st_size,
                           integrity=base64.b64encode(sha).decode(), shell=shell))

    html = (ROOT / "static/index.html").read_text("utf-8").replace("</head>", """
  <meta name="guide-runtime" content="device">
  <meta name="apple-mobile-web-app-capable" content="yes">
  <link rel="manifest" href="./manifest.webmanifest">
  <link rel="apple-touch-icon" href="./icons/180.png">
  <link rel="icon" href="./icons/192.png">
</head>""")
    write("index.html", html, True)
    write("manifest.webmanifest", MOBILE / "manifest.webmanifest", True)
    for path in sorted((ROOT / "static").glob("*")):
        if path.suffix in (".css", ".js"):
            write("static/" + path.name, path, True)
    for name in ("client.js", "offline.js", "worker.js"):
        write("mobile/" + name, MOBILE / name, True)
    for path in sorted((MOBILE / "icons").glob("*.png")):
        write("icons/" + path.name, path, True)
    write("campus-viewpoints.json", ROOT / "campus-viewpoints.json")
    guide = io.BytesIO()
    with ZipFile(guide, "w") as archive:
        sources = [ROOT / (name + ".py") for name in SHARED] + [MOBILE / "browser_ort.py", MOBILE / "device.py"]
        for path in sources:
            archive.writestr(ZipInfo(path.name), path.read_text("utf-8").encode(), compress_type=ZIP_DEFLATED)
    write("guide.zip", guide.getvalue())
    documented = dict(re.findall(r"\| ([\w.-]+\.onnx) \| `([a-f0-9]{64})`",
                                 (ROOT / "README.md").read_text("utf-8")))
    actual = {path.name: digest(path) for path in (ROOT / "models").glob("*.onnx")}
    if len(actual) != 5 or actual != documented:
        raise ValueError("The five model files must match the SHA-256 values in README.md.")
    for path in sorted((ROOT / "models").glob("*")):
        if path.is_file():
            write("models/" + path.name, path)
    for path in fetch():
        write("vendor/" + path.relative_to(CACHE).as_posix(), path)
    for path in sorted((MOBILE / "licenses").glob("*.txt")):
        write("vendor/licenses/" + path.name, path)
    manifest = json.dumps(assets, separators=(",", ":"))
    sw = (MOBILE / "sw.js").read_text("utf-8")
    version = hashlib.sha256((manifest + sw).encode()).hexdigest()[:16]
    (OUT / "sw.js").write_text(sw.replace("__BUILD_ID__", version).replace("__BUILD_ASSETS__", manifest),
                               encoding="utf-8", newline="\n")
    (OUT / ".nojekyll").touch()
    print(f"{len(assets)} assets, {sum(item['size'] for item in assets)/1e6:.1f} MB, build {version}")


if __name__ == "__main__":
    build()
