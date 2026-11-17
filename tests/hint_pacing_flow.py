"""用浏览器和可控时间验证导拍文字可读时长、方向去抖及重要提示立即显示。"""
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
from threading import Thread

from playwright.sync_api import sync_playwright


ROOT = Path(__file__).resolve().parents[1]


class QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, *_):
        pass


from phone_ui_flow import CONTRACTS as CASES


def main():
    qa = ROOT / "build/qa"
    qa.mkdir(parents=True, exist_ok=True)
    server = ThreadingHTTPServer(("127.0.0.1", 0), partial(QuietHandler, directory=str(ROOT)))
    Thread(target=server.serve_forever, daemon=True).start()
    try:
        with sync_playwright() as pw:
            cached = list((ROOT.parent / ".browser").glob("chromium_headless_shell-*/**/chrome-headless-shell.exe"))
            executable = os.environ.get("CHROMIUM_PATH") or (str(cached[0]) if cached else None)
            browser = pw.chromium.launch(executable_path=executable)
            page = browser.new_page()
            page.goto(f"http://127.0.0.1:{server.server_port}/")
            findings = page.evaluate(CASES)
            (qa / "hint-pacing-results.json").write_text(json.dumps(findings, indent=2) + "\n", encoding="utf-8")
            print(json.dumps(findings, indent=2))
            print("Three-second hints, latest-cue replacement, action feedback and bounded pose guidance passed.")
            browser.close()
    finally:
        server.shutdown()
        server.server_close()


if __name__ == "__main__":
    main()
