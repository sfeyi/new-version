"""背景线稿应表达真实长边，排除人物，且保持移动端可接受的大小。"""
from pathlib import Path
import sys

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from background_shapes import background_outline, MAX_PATHS, MAX_POINTS


def samples(outline):
    for path in outline["paths"]:
        for start, end in zip(path, path[1:]):
            yield np.linspace(start, end, max(2, int(np.linalg.norm(np.array(end)-start)*1000)))


def test_salient_building_and_horizon_remain_in_reference_coordinates():
    scene = np.full((480, 640, 3), 205, np.uint8)
    cv2.rectangle(scene, (100, 70), (420, 320), (55, 55, 55), -1)
    cv2.line(scene, (0, 370), (639, 370), (20, 20, 20), 5)
    outline = background_outline(scene, [])
    assert outline and outline["kind"] == "background"
    points = np.vstack(outline["paths"])
    assert np.isfinite(points).all() and points.min() > 0 and points.max() < 1
    assert np.any(np.abs(points[:, 0] - 100/640) < .015)
    assert np.any(np.abs(points[:, 1] - 370/480) < .02)
    assert len(outline["paths"]) <= MAX_PATHS and len(points) <= MAX_POINTS


def test_detected_people_and_border_are_excluded_even_after_path_simplification():
    scene = np.full((480, 640, 3), 205, np.uint8)
    cv2.rectangle(scene, (0, 0), (639, 479), (0, 0, 0), 8)
    cv2.line(scene, (0, 240), (639, 240), (0, 0, 0), 5)
    cv2.rectangle(scene, (250, 100), (390, 420), (0, 0, 0), -1)
    person = [250/640, 100/480, 390/640, 420/480]
    outline = background_outline(scene, [person])
    assert outline  # horizon outside the person remains available
    for segment in samples(outline):
        pixels = segment * [640, 480]
        assert np.all(pixels > 9) and np.all(pixels < [631, 471])
        inside = (segment[:, 0] >= person[0]) & (segment[:, 0] <= person[2]) & (segment[:, 1] >= person[1]) & (segment[:, 1] <= person[3])
        assert not inside.any()


def test_blank_or_person_only_image_does_not_invent_background_geometry():
    scene = np.full((320, 240, 3), 145, np.uint8)
    assert background_outline(scene, []) is None
    cv2.rectangle(scene, (70, 60), (180, 290), (20, 20, 20), -1)
    assert background_outline(scene, [[70/240, 60/320, 180/240, 290/320]]) is None


def test_dense_texture_is_bounded_and_output_is_scale_independent():
    scene = np.full((960, 1280, 3), 205, np.uint8)
    for y in range(60, 900, 26):
        cv2.line(scene, (45, y), (1235, y), (20, 20, 20), 4)
    large = background_outline(scene, [])
    small = background_outline(cv2.resize(scene, (640, 480)), [])
    assert large and small
    assert len(large["paths"]) <= MAX_PATHS
    assert sum(len(path) for path in large["paths"]) <= MAX_POINTS
    np.testing.assert_allclose(np.vstack(large["paths"]), np.vstack(small["paths"]), atol=.005)
