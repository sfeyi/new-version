"""Bundle the camera UI only; the Android app runs all inference on the user's PC."""
from pathlib import Path
import shutil

ROOT = Path(__file__).resolve().parents[1]


def main():
    target = (ROOT / "build/android-web").resolve()
    # Exclude the old on-device AI engine; the image-encoding worker is UI-only.
    if target.parent != (ROOT / "build").resolve():
        raise ValueError("Android staging must stay in this project's build directory")
    if target.exists():
        shutil.rmtree(target)
    (target / "static").mkdir(parents=True)
    for path in (ROOT / "static").iterdir():
        if path.suffix in (".css", ".js"):
            shutil.copyfile(path, target / "static" / path.name)
    html = (ROOT / "static/index.html").read_text(encoding="utf-8")
    html = html.replace("</head>", '<meta name="guide-native" content="android">\n</head>')
    (target / "index.html").write_text(html, encoding="utf-8")
    size = sum(p.stat().st_size for p in target.rglob("*") if p.is_file())
    print(f"Android camera client: {size / 1024:.0f} KB (models run on PC), {target}")


if __name__ == "__main__":
    main()
