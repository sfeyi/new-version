"""固定关键帧跟踪：真实运动跟随，静止不换点，失配不凭旧坐标过关。"""
from pathlib import Path
import sys

import cv2
import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from background import Background, CENTER_TOLERANCE, SCALE_TOLERANCE


@pytest.fixture
def scenery():
    rng = np.random.default_rng(913)
    gray = cv2.GaussianBlur(rng.integers(20, 235, (360, 480), dtype=np.uint8), (3, 3), .7)
    for _ in range(36):
        x, y = rng.integers(20, 320, 2)
        cv2.rectangle(gray, (int(x), int(y)), (int(x+24), int(y+12)), int(rng.integers(40, 230)), 2)
    return cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR)


def move(image, x=0., y=0., scale=1.):
    h, w = image.shape[:2]
    matrix = np.float32([[scale, 0, x+(1-scale)*w/2], [0, scale, y+(1-scale)*h/2]])
    return cv2.warpAffine(image, matrix, (w, h), borderMode=cv2.BORDER_CONSTANT)


def test_static_anchor_ignores_new_descriptor_estimates_and_pixel_noise(scenery, monkeypatch):
    background = Background(scenery, [])
    first = background.measure(scenery, [])
    assert first["sceneLocked"] and first["centerOk"]
    original = np.array(first["current"])
    monkeypatch.setattr(background, "features", lambda *args: pytest.fail("locked anchor rematched reference"))
    rng = np.random.default_rng(7)
    for _ in range(12):
        noisy = np.clip(scenery.astype(float)+rng.normal(0, .8, scenery.shape), 0, 255).astype(np.uint8)
        result = background.measure(noisy, [])
        assert result["sceneLocked"] and result["anchorId"] == first["anchorId"]
        assert np.linalg.norm(np.array(result["current"])-original) < .001


def test_same_anchor_follows_pan_and_zoom_without_relocalization(scenery, monkeypatch):
    background = Background(scenery, [])
    first = background.measure(scenery, [])
    monkeypatch.setattr(background, "features", lambda *args: pytest.fail("valid motion rematched reference"))
    for x, y, scale in [(8, -3, 1), (18, -7, 1), (32, -9, 1), (40, -9, 1.04), (40, -9, 1.09)]:
        result = background.measure(move(scenery, x, y, scale), [])
        assert result["sceneLocked"] and result["anchorId"] == first["anchorId"]
        assert np.linalg.norm(np.array(result["current"])-[.5+x/480, .5+y/360]) < .006
        assert abs(result["zoom"]-scale) < .025
    assert not result["centerOk"]  # 镜头确实移出中心阈值，不能因定锚而虚假通过。


def test_occlusion_and_missing_texture_do_not_reuse_old_alignment(scenery):
    background = Background(scenery, [])
    assert background.measure(scenery, [])["sceneLocked"]
    tracker = background._scene
    gray = cv2.cvtColor(scenery, cv2.COLOR_BGR2GRAY)
    mask = np.full(gray.shape, 255, np.uint8)
    assert tracker.measure(np.zeros_like(gray), mask, CENTER_TOLERANCE, SCALE_TOLERANCE) is None
    assert tracker.measure(gray, np.zeros_like(mask), CENTER_TOLERANCE, SCALE_TOLERANCE) is None
    mirrored = cv2.flip(gray, 1)
    assert tracker.measure(mirrored, mask, CENTER_TOLERANCE, SCALE_TOLERANCE) is None


def test_reacquisition_requires_repeat_evidence_and_reset_changes_anchor(scenery):
    background = Background(scenery, [])
    first = background.measure(scenery, [])
    gray, mask = background.background_image(scenery, [])
    background._scene = None  # 连续跟踪丢失后的跨图重定位。
    candidate = {**first, "current": [.65, .5]}
    waiting = background.lock_scene(gray, mask, candidate)
    assert not waiting["tracking"] and not waiting["centerOk"]
    other = background.lock_scene(gray, mask, {**first, "current": [.35, .5]})
    assert not other["tracking"]
    confirmed = background.lock_scene(gray, mask, {**first, "current": [.352, .5]})
    assert confirmed["sceneLocked"] and confirmed["anchorId"] > first["anchorId"]
    background.reset_tracking()
    assert background._scene is None and not background._had_anchor and background._reacquire is None
    fresh = background.measure(scenery, [])
    assert fresh["sceneLocked"] and fresh["anchorId"] > confirmed["anchorId"]


def test_local_affine_match_also_keeps_a_scene_anchor(scenery, monkeypatch):
    background = Background(scenery, [])
    first = background.measure(scenery, [])
    gray, mask = background.background_image(scenery, [])
    background.reset_tracking()
    local = {**first, "quad": None}
    locked = background.lock_scene(gray, mask, local)
    assert locked["sceneLocked"]
    monkeypatch.setattr(background, "features", lambda *args: pytest.fail("local anchor rematched reference"))
    shifted = background.measure(move(scenery, x=12), [])
    assert shifted["sceneLocked"] and shifted["quad"] is None
    assert abs(shifted["current"][0]-.525) < .004


def test_unknown_scale_can_recover_without_moving_the_locked_center(scenery, monkeypatch):
    background = Background(scenery, [])
    first = background.measure(scenery, [])
    # 初次跨图只给出中心，没有足够一致的地标间距。
    background._scene.base = background._scene.base | {"zoom": None}
    recovered = background.measure(scenery, [])
    assert recovered["sceneLocked"] and recovered["anchorId"] == first["anchorId"]
    assert recovered["current"] == first["current"] and recovered["zoom"] == pytest.approx(1, abs=.005)
    monkeypatch.setattr(background, "features", lambda *args: pytest.fail("known scale was rematched again"))
    assert background.measure(move(scenery, scale=1.04), [])["zoom"] == pytest.approx(1.04, abs=.015)


def test_scale_recovery_rejects_a_different_reference_projection(scenery):
    background = Background(scenery, [])
    first = background.measure(scenery, [])
    scene = background._scene
    scene.base = scene.base | {"zoom": None}
    gray, mask = background.background_image(scenery, [])
    tracked = scene.measure(gray, mask, CENTER_TOLERANCE, SCALE_TOLERANCE)
    foreign = {**first, "current": [.7, .5], "zoom": 1.1}
    result = scene.accept_scale(foreign, tracked, CENTER_TOLERANCE, SCALE_TOLERANCE)
    assert result["current"] == tracked["current"] and result["zoom"] is None
