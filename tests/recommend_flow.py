"""对运行中的本机双服务进行真实模型检索、浏览器选图和导拍验证。"""
import json
from pathlib import Path
from urllib.request import urlopen

from playwright.sync_api import expect, sync_playwright


ROOT = Path(__file__).resolve().parents[1]
URL = "http://127.0.0.1:7861/"


def main():
    qa = ROOT / "build/qa"
    qa.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as pw:
        cached = list((ROOT.parent / ".browser").glob("chromium_headless_shell-*/**/chrome-headless-shell.exe"))
        browser = pw.chromium.launch(executable_path=str(cached[0]) if cached else None)
        page = browser.new_page(viewport={"width":1220, "height":900}, accept_downloads=True)
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.goto(URL)
        expect(page.locator("#library-count")).to_contain_text("98")
        page.locator("#library-location").select_option("clock_tower")
        page.locator("#library-query").fill("从低处仰拍钟楼和弧形栏杆平台，人物在画面中占比较小")
        with page.expect_response(lambda r: r.url.endswith("/api/recommend"), timeout=120000) as response:
            page.locator("#recommend").click()
        assert response.value.status == 200, response.value.text()
        data = response.value.json()
        assert data["mode"] == "recommend" and len(data["recommendations"]) == 3
        assert not data["missing_images"] and not data["missing_features"]
        expect(page.locator("#library-status")).to_contain_text("AI 推荐")
        expect(page.locator(".photo-card")).to_have_count(3)
        page.locator("#library-panel").screenshot(path=str(qa / "05-ai-recommendation.png"))
        picked = data["recommendations"][0]
        page.locator(".photo-card").first.click()
        expect(page.locator("body")).to_have_attribute("data-reference", "loaded", timeout=30000)
        expect(page.locator("#filename")).to_contain_text(picked["photo_id"])
        expect(page.locator("#viewpoint")).to_contain_text("钟楼")
        with urlopen(URL + picked["image_url"]) as photo:
            page.locator("#live-file").set_input_files({"name":picked["photo_id"] + ".webp",
                "mimeType":photo.headers.get_content_type(), "buffer":photo.read()})
        expect(page.locator("#shutter")).to_be_enabled(timeout=30000)
        with page.expect_download() as download:
            page.locator("#shutter").click()
        download.value.save_as(qa / "recommendation-shot.jpg")
        assert not errors, errors
        data["verification"] = {"selected":picked["photo_id"], "guide":"four gates passed", "jpeg":True}
        (qa / "recommendation-result.json").write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        print("Actual AI recommendations:", [p["photo_id"] for p in data["recommendations"]])
        print("Selected", picked["photo_id"], "and passed all four guide gates; JPEG saved.")
        browser.close()


if __name__ == "__main__":
    main()
