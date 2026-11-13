"""Fetch pinned browser runtimes at build time. The phone only contacts the Pages site."""
import hashlib
import json
import shutil
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CACHE = ROOT / ".mobile-cache"
PYODIDE = "https://cdn.jsdelivr.net/pyodide/v0.29.3/full/"
ORT = "https://cdn.jsdelivr.net/npm/onnxruntime-web@1.23.2/dist/"
LOCK_DIGEST = "3256ffc76388de0e37f4b34d42ab484268d1afc675179ff97b2a5bb14f84ccac"
PY_FILES = {
    "pyodide.mjs": "23213d27e9dacbda6f25f955fb38dabfc40ada082c04ee253047ea5dddcaa3b5",
    "pyodide.asm.js": "1263f02b5b26099b96112378156f242dd98b39a8201ba7765e5fe3d455c5ce91",
    "pyodide.asm.wasm": "e2f4ee75b325e35eb31bfb8c613d4dd5098f5502c156a97847686875b5025480",
    "python_stdlib.zip": "4298b6ee445cb724c3973437da47789752b9e6ff4e26619026b283ec801fc46b",
}
ORT_FILES = {
    "ort.wasm.min.mjs": "69751720f611e37d1ce2fa3c6ebaa80f949014752636c5f8887a63d40feadcc7",
    "ort-wasm-simd-threaded.mjs": "90a557d15c02bac4504d95b67f431d8594635ed2a0a62a7f2cd83d090ff91d3e",
    "ort-wasm-simd-threaded.wasm": "45eaee27761ad883742a8d4b8fce1538d60ce43b51adf1726fafccc59b8c1a15",
}


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def download(item):
    folder, prefix, name, expected = item
    path = CACHE / folder / name
    if path.is_file() and (not expected or digest(path) == expected):
        return path
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_suffix(path.suffix + ".part")
    for attempt in range(3):
        try:
            with urllib.request.urlopen(prefix + name, timeout=120) as source, partial.open("wb") as out:
                shutil.copyfileobj(source, out)
            if expected and digest(partial) != expected:
                raise ValueError(f"SHA-256 mismatch: {name}")
            partial.replace(path)
            return path
        except (OSError, ValueError):
            if attempt == 2:
                raise
            time.sleep(2 ** attempt)


def fetch():
    lock = download(("pyodide", PYODIDE, "pyodide-lock.json", LOCK_DIGEST))
    packages = json.loads(lock.read_bytes())["packages"]
    items = [("pyodide", PYODIDE, name, sha) for name, sha in PY_FILES.items()]
    for name in ("numpy", "opencv-python"):
        item = packages[name]
        items.append(("pyodide", PYODIDE, item["file_name"], item["sha256"]))
    items.extend(("ort", ORT, name, sha) for name, sha in ORT_FILES.items())
    with ThreadPoolExecutor(max_workers=4) as pool:
        return [lock, *pool.map(download, items)]


if __name__ == "__main__":
    for asset in fetch():
        print(asset.name, asset.stat().st_size, digest(asset))
