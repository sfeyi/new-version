from pathlib import Path
import sys

import cv2
import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import decode, get_models
from background import Background, CENTER_TOLERANCE, FRAME, frame_error
from building import reference_building
from guide import Guide
from landmarks import landmark_scale, xfeat_features
from outline import reference_outline
from people import Person, choose
from posture import PoseTarget


def portrait():
    points = np.array([[.51,.23], [.53,.22], [.49,.22], [.55,.24], [.47,.24],
                       [.59,.32], [.43,.32], [.64,.43], [.37,.44], [.70,.31], [.32,.52],
                       [.57,.55], [.45,.55], [.59,.72], [.43,.70], [.61,.91], [.41,.89]])
    return Person(np.array([.29,.16,.73,.95,.92]), points, np.full(17, .85))


def test_pose_goals_stay_in_the_reference_frame_when_the_subject_moves_and_scales():
    ref = portrait()
    target = PoseTarget(ref, 960, 720)
    current = portrait()
    current.xy += [.065, 0]
    current.box[:4] += [.065, 0, .065, 0]
    pos, pose = target.compare(current)
    assert pos["ok"] and not pose["ok"]
    assert pose["target"][9] == ref.xy[9].tolist() and pose["actual"][9] != pose["target"][9]
    current.xy = current.xy * .81 + [.17, .03]
    assert target.compare(current)[1]["target"] == pose["target"]
    current = portrait()
    current.xy[9] += [-.10, .22]
    pos, pose = target.compare(current)
    assert not pose["ok"] and pose["joint"] == 9 and "手腕" in pose["message"]
    current.scores[9] = .1
    _, pose = target.compare(current)
    assert not pose["ok"] and pose["actual"][9] is None and "入镜" in pose["message"]
    current.scores[:] = 0.
    pos, pose = target.compare(current)
    assert pos["ok"] and not pose["ok"] and all(p is None for p in pose["actual"])
    assert pose["target"] == target.points


def test_close_circles_use_screen_distance_in_portrait_and_landscape():
    for aspect in (.64, 1.6):
        for radius, ok in [(CENTER_TOLERANCE - .001, True), (CENTER_TOLERANCE + .001, False)]:
            offset = np.array([.6, .8]) * radius * min(aspect, 1.) / [aspect, 1.]
            point = .5 + offset
            quad = np.float32((FRAME - .5) * 1.22 + point)
            measured = frame_error(quad, point, aspect, 20, 1.22)
            assert measured["tracking"] and measured["centerOk"] == ok
            assert not measured["ok"] and not measured["scaleOk"]
            assert measured["centerDistance"] == pytest.approx(radius)
    angle = np.deg2rad(25)
    rotation = np.array([[np.cos(angle), np.sin(angle)], [-np.sin(angle), np.cos(angle)]])
    tilted = frame_error(np.float32((FRAME-.5) @ rotation + .5), np.array([.5, .5]), 1, 25, 1.)
    assert not tilted["tracking"] and not tilted["ok"] and tilted["control"] == "roll"


def test_outline_uses_only_the_selected_person_and_preserves_arm_gaps():
    frame = np.zeros((180, 300, 3), np.uint8)
    cv2.rectangle(frame, (115,38), (152,154), (240,240,240), -1)
    cv2.ellipse(frame, (115,80), (15,21), 0, 0, 360, (240,240,240), 5)
    cv2.rectangle(frame, (163,48), (188,144), (240,240,240), -1)
    box = np.array([98/300,38/180,153/300,155/180])
    mask = lambda square: np.float32(square[:, :, 0] > 200)
    result = reference_outline(frame, box, [[.45,.32], [.45,.61]], mask)
    assert result and len(result["contours"]) == 2
    outer = np.vstack(result["contours"])
    assert outer[:,0].max() <= 153/300 and outer[:,0].min() == pytest.approx(98/300, abs=.012)
    assert reference_outline(frame, box, [], lambda sq: np.zeros(sq.shape[:2], np.float32)) is None


def test_weak_segmentation_draws_a_simplified_outline_of_visible_limbs_only():
    ref = portrait()
    ref.scores[11:] = .1
    target = PoseTarget(ref, 640, 480)
    frame = np.zeros((480,640,3), np.uint8)
    shape = reference_outline(frame, ref.box, target.points, lambda crop: np.zeros(crop.shape[:2], np.float32))
    assert shape["kind"] == "joints" and shape["contours"] and not shape["details"]
    assert np.vstack(shape["contours"])[:,1].max() < .60


def test_half_body_and_head_angle_are_observable_independently():
    ref = portrait()
    ref.scores[11:] = .12
    target = PoseTarget(ref, 700, 950)
    assert target.basis == "shoulders"
    current = Person(ref.box, ref.xy.copy(), ref.scores.copy())
    assert target.compare(current)[1]["ok"]
    current.xy[1, 1] += .033
    _, pose = target.compare(current)
    assert not pose["ok"] and "头顶" in pose["message"]
    assert all(p is None for p in pose["target"][11:])


def test_passerby_does_not_steal_the_selected_subject():
    left = np.array([.02,.18,.24,.93,.86])
    right = np.array([.55,.31,.72,.8,.95])
    assert choose([left, right], tap=[.62,.52]) is right
    assert choose([left, right], previous=right) is right
    assert choose([left], previous=right) is None
    assert choose([left, right], target=right) is right


def test_only_the_background_can_confirm_the_landmark():
    rng = np.random.default_rng(27)
    bg = np.full((480,640,3), 95, np.uint8)
    bg[95:385,180:460] = rng.integers(0,255,(290,280,3),np.uint8)
    box = np.array([.25,.14,.76,.84,.95])
    tracker = Background(bg, [box])
    assert tracker.measure(bg, [box]) is None


def test_large_portrait_preserves_side_background_and_detects_reflected_only_in_background():
    rng = np.random.default_rng(12)
    shape = (480, 360)
    person_mask = np.zeros(shape, np.uint8)
    cv2.ellipse(person_mask, (180, 212), (68, 90), 0, 0, 360, 1, -1)
    cv2.ellipse(person_mask, (180, 460), (188, 145), 0, 0, 360, 1, -1)
    reference = np.full((*shape, 3), 205, np.uint8)
    background_texture = rng.integers(0, 255, (190, 360, 3), dtype=np.uint8)
    reference[126:316] = cv2.GaussianBlur(background_texture, (3, 3), .6)
    reference[person_mask != 0] = rng.integers(0, 255, (person_mask.sum(), 3), dtype=np.uint8)
    box = np.array([0., .25, 1., 1., .96])
    # 原版矩形正好同时盖住左右两段背景。
    assert Background(reference, [box]).measure(reference, [box]) is None
    tracker = Background(reference, [box], lambda _: person_mask.astype(np.float32))
    assert tracker.measure(reference, [box])["ok"]
    mirrored = tracker.measure(cv2.flip(reference, 1), [box])
    assert mirrored and mirrored["control"] == "mirror" and not mirrored["ok"]
    no_background = np.full_like(reference, 205)
    no_background[person_mask != 0] = reference[person_mask != 0]
    assert tracker.measure(no_background, [box]) is None


def test_close_framing_hint_cannot_unlock_the_background_stage():
    ref = portrait()
    current = portrait()
    current.xy = (current.xy - .5) * 1.6 + .5
    assert "拉开" in PoseTarget(ref, 640, 480).framing_hint(current)


def test_local_matches_on_either_side_project_the_same_reference_center():
    # 两帧看见不同侧的一小片地标，机位和投影却完全相同。
    offsets = np.float32([[x, y] for x in (0, .045, .09, .135) for y in (0, .05, .10)])
    ref_xy = np.vstack((offsets + [.10, .20], offsets + [.70, .61])).astype(np.float32)
    desc = np.eye(len(ref_xy), dtype=np.float32)
    size = np.float32([720, 960])
    tracker = Background(np.zeros((120, 90, 3), np.uint8), [])
    for visible in (slice(0, 12), slice(12, 24)):
        current = ((ref_xy[visible] - .5) * 1.12 + [.58, .46]).astype(np.float32)
        result = tracker.match((ref_xy, desc), (current, desc[visible]), size)
        assert result["control"] == "local" and not result["ok"]
        assert result["target"] == [.5, .5]
        np.testing.assert_allclose(result["current"], [.58, .46], atol=1e-5)
        assert "右转" in result["message"]
        current = ((ref_xy[visible] - .5) * 1.12 + [.55, .48]).astype(np.float32)
        close = tracker.match((ref_xy, desc), (current, desc[visible]), size)
        assert close["quad"] is None and close["tracking"] and close["ok"]


@pytest.fixture(scope="module")
def campus_photo():
    library = Path(__file__).resolve().parents[2] / "校园摄影参考库_队友分享_98张"
    photo = next(library.rglob("REF_011.webp"), None)
    if photo is None:
        pytest.skip("需要共享参考库中的 REF_011.webp")
    return decode(photo.read_bytes())


def test_real_campus_landmark_coarse_fine_and_loss(campus_photo):
    f = campus_photo
    h, w = f.shape[:2]
    guide = Guide(get_models(), f, "拱廊正面")
    shifts = [(.18, 0, "coarse", "向右转"), (.11, 0, "fine", "向右转"),
              (-.11, 0, "fine", "向左转"), (0, .10, "fine", "向下俯"), (0, -.10, "fine", "向上仰")]
    for seq, (dx, dy, mode, cue) in enumerate(shifts):
        mat = np.float32([[1,0,w*dx],[0,1,h*dy]])
        moved = cv2.warpAffine(f, mat, (w,h))
        res = guide.analyze(moved, seq, 1 + .2*seq)
        assert res["stage"] == "background" and res["mode"] == mode
        assert cue in res["message"]
        assert res["background"]["target"] == [.5, .5]
        np.testing.assert_allclose(res["background"]["current"], [.5+dx, .5+dy], atol=.006)
    res = guide.analyze(np.zeros_like(f), len(shifts), 2.)
    assert res["mode"] == "search" and "拱廊正面" in res["message"] and not res["ready"]


def test_near_alignment_advances_and_small_camera_jitter_keeps_standing(campus_photo):
    guide = Guide(get_models(), campus_photo)
    assert guide.reference["outline"]["contours"] and guide.reference["centerTolerance"] == CENTER_TOLERANCE
    h, w = campus_photo.shape[:2]
    for seq, dx in enumerate([.067, .067, .067, .091, .12]):
        live = cv2.warpAffine(campus_photo, np.float32([[1,0,dx*w],[0,1,0]]), (w,h))
        step = guide.analyze(live, seq, seq*.20)
        assert step["stage"] == ["background","background","position","position","background"][seq]


def test_landmark_spacing_needs_distant_pairs_in_agreement():
    points = np.float32([(x, y) for x in (.1, .3, .55, .85) for y in (.15, .45, .8)])
    size = np.float32([720, 960])
    assert landmark_scale(points, points * .71 + [.04, .2], size) == pytest.approx(.71)
    assert landmark_scale(points * .06, points * .04, size) is None
    assert landmark_scale(points, points * [1.4, .76], size) is None


def test_aim_before_zoom_and_verify_the_changed_framing(campus_photo):
    guide = Guide(get_models(), campus_photo)
    h, w = campus_photo.shape[:2]
    def view(scale, offset=0.):
        matrix = cv2.getRotationMatrix2D((w/2, h/2), 0, scale)
        matrix[0, 2] += w * offset
        return cv2.warpAffine(campus_photo, matrix, (w, h))
    for seq, time in enumerate((0., .4)):
        result = guide.analyze(view(.70, .16), seq, time)
        bg = result["background"]
        assert result["stage"] == "background" and not bg["centerOk"] and "zoomFactor" not in bg
        for anchor in bg["anchors"]:
            target = np.array(guide.reference["landmarks"][anchor["id"]])
            np.testing.assert_allclose(anchor["current"], .7*(target-.5)+[.66,.5], atol=.008)
        assert bg["anchors"]
    # 圆心一度进入金圈，但镜头在金圈里继续移动，需要重新稳定。
    for seq, (time, dx) in enumerate(((.55, 0.), (.79, .06), (.96, .06)), 2):
        bg = guide.analyze(view(.7, dx), seq, time)["background"]
        assert bg["centerOk"] and not bg["scaleOk"] and "zoomFactor" not in bg
    result = guide.analyze(view(.70, .06), 5, 1.16)
    assert result["background"]["zoomFactor"] == pytest.approx(1/.70, abs=.015)
    assert result["stage"] == "background" and not result["checks"][0]
    # 请求变焦之后收到旧景别，仍不能进入站位。真正变化后的帧再核验。
    assert guide.analyze(view(.70, .06), 6, 1.4)["stage"] == "background"
    assert guide.analyze(campus_photo, 7, 1.6)["stage"] == "background"
    assert guide.analyze(campus_photo, 8, 2.)["stage"] == "position"
    assert guide.analyze(view(1.15), 9, 2.2)["checks"][0]
    assert guide.analyze(view(1.28), 10, 2.4)["stage"] == "background"
    restarted = guide.analyze(view(1.28), 11, 2.75, source_id=2)
    assert "zoomFactor" not in restarted["background"]


def test_learned_landmarks_map_to_the_same_frame_coordinates(campus_photo):
    h, w = campus_photo.shape[:2]
    size = np.float32([w, h])
    dx, dy = -.09, .13
    live = cv2.warpAffine(campus_photo, np.float32([[1,0,w*dx],[0,1,h*dy]]), (w,h))
    models = get_models()
    tracker = Background(campus_photo, models.observe(campus_photo, 0)[0], models.foreground)
    observed = xfeat_features(*tracker.background_image(live, models.observe(live, 0)[0]))
    bg = tracker.learned_match(observed, size, CENTER_TOLERANCE, 1.13)
    assert bg and bg["tracking"] and bg["anchors"]
    np.testing.assert_allclose(bg["current"], [.5+dx, .5+dy], atol=.008)
    assert bg["zoom"] == pytest.approx(1., abs=.012)


def test_independent_arcade_view_and_unrelated_lawn(campus_photo):
    library = Path(__file__).resolve().parents[2] / "校园摄影参考库_队友分享_98张"
    files = [next(library.rglob(f"REF_{key}.webp"), None) for key in ("025", "026", "051", "046", "095")]
    if None in files:
        pytest.skip("需要共享参考库中的拱廊和草坪样例")
    reference, different_take, lawn, tower, second_tower = (decode(path.read_bytes()) for path in files)
    guide = Guide(get_models(), reference)
    # 试拍入口按参考照画幅居中取景，这张原图比参考宽一像素。
    different_take = different_take[:, :reference.shape[1]]
    result = guide.analyze(different_take, 0, 0.)
    bg = result["background"]
    assert bg and bg["inliers"] >= 24 and not bg["scaleOk"] and not bg["ok"]
    assert result["stage"] == "background" and "zoomFactor" not in bg
    other = Guide(get_models(), tower).analyze(second_tower, 0, 0.)["background"]
    assert other and other["anchors"] and other["control"] == "x"
    assert other["zoom"] == pytest.approx(.80, abs=.06) and not other["centerOk"]
    h,w = campus_photo.shape[:2]
    unrelated = Guide(get_models(), campus_photo).analyze(cv2.resize(lawn, (w,h)), 0, 0.)
    assert unrelated["background"] is None and not unrelated["checks"][0]


def test_building_outline_uses_visible_geometry_on_an_unlabelled_reference():
    library = Path(__file__).resolve().parents[2] / "校园摄影参考库_队友分享_98张"
    photo = next(library.rglob("REF_046.webp"), None)
    if photo is None:
        pytest.skip("需要共享参考库中的草坪圆塔照片")
    im = decode(photo.read_bytes())
    h, w = im.shape[:2]
    # 用重新构图并编码的图像，避免把文件名或原图坐标当作检测结果。
    dx = .13
    translated = cv2.warpAffine(im, np.float32([[1, 0, dx*w], [0, 1, 0]]), (w, h),
                                borderMode=cv2.BORDER_REPLICATE)
    ok, encoded = cv2.imencode(".jpg", translated, [cv2.IMWRITE_JPEG_QUALITY, 88])
    assert ok
    other = decode(encoded.tobytes())
    base = reference_building(im, [])
    shifted = reference_building(other, [])
    assert base and shifted
    for operation in (np.amin, np.amax):
        np.testing.assert_allclose(operation(np.vstack(shifted["paths"]), axis=0),
                                   operation(np.vstack(base["paths"]), axis=0) + [dx, 0], atol=.045)
    # 有同名地点提示但只拍到栏杆时，不能制造建筑轮廓。
    rail = decode(next(library.rglob("REF_062.webp")).read_bytes())
    assert reference_building(rail, get_models().observe(rail, 0)[0], "钟楼") is None



def test_each_stage_can_be_skipped_during_loss_and_restored(campus_photo):
    guide = Guide(get_models(), campus_photo)
    blank = np.zeros_like(campus_photo)
    step = guide.analyze(blank, 0, 0., skipped=["background"])
    assert step["stage"] == "position" and step["checks"] == [None, False, False]
    step = guide.analyze(blank, 1, .2, skipped=["background", "position"])
    assert step["stage"] == "pose" and "姿势" in step["message"]
    step = guide.analyze(blank, 2, .4, skipped=["position", "background", "pose"])
    assert step["ready"] and step["checks"] == [None, None, None]
    assert step["skipped"] == ["background", "position", "pose"]
    with pytest.raises(ValueError, match="过期"):
        guide.analyze(blank, 1, .5, skipped=[])
    assert guide.analyze(blank, 3, 3., tap=[.4, .5])["ready"]
    with pytest.raises(ValueError, match="可跳过"):
        guide.analyze(blank, 4, 3.2, skipped=["ready"])
    step = guide.analyze(blank, 4, 3.2, skipped=[])
    assert step["stage"] == "background" and step["skipped"] == [] and not step["ready"]


def test_skipping_only_position_keeps_background_and_pose_gated(campus_photo):
    guide = Guide(get_models(), campus_photo)
    for seq in range(7):
        step = guide.analyze(campus_photo, seq, seq*.22, skipped=["position"])
        assert step["stage"] != "position"
    assert step["ready"] and step["checks"] == [True, None, True]
    lost = guide.analyze(np.zeros_like(campus_photo), 7, 1.54)
    assert lost["stage"] == "background" and not lost["ready"]


def test_reference_without_a_detectable_subject_can_still_use_manual_capture():
    blank = np.zeros((160, 200, 3), np.uint8)
    guide = Guide(get_models(), blank)
    assert guide.reference["box"] is None and guide.reference["subjectHint"]
    assert guide.analyze(blank, 0, 0.)["stage"] == "background"
    result = guide.analyze(blank, 1, .2, skipped=["background"])
    assert result["stage"] == "position" and not result["ready"] and "参考" in result["message"]
    assert guide.analyze(blank, 2, .4, skipped=["background", "position", "pose"])["ready"]


def test_camera_ready_is_withdrawn_if_person_or_background_leaves(campus_photo):
    f = campus_photo
    guide = Guide(get_models(), f)
    stages = []
    for seq in range(9):
        result = guide.analyze(f, seq, seq*.22)
        stages.append(result["stage"])
    assert list(dict.fromkeys(stages)) == ["background","position","pose","ready"]
    assert result["ready"] and all(result["checks"])
    no_model = f.copy()
    h, w = f.shape[:2]
    x,y,r,b = np.rint(guide.target.reference.box[:4] * [w,h,w,h]).astype(int)
    cv2.rectangle(no_model, (x-5,y-5), (r+5,b+5), (92,95,80), -1)
    result = guide.analyze(no_model, 9, 2.)
    assert result["stage"] == "position" and not result["ready"] and result["checks"][0]
    result = guide.analyze(np.zeros_like(f), 10, 2.2)
    assert result["stage"] == "background" and not result["ready"]
    with pytest.raises(ValueError, match="过期"):
        guide.analyze(f, 10, 2.4)

def test_image_inference_finds_a_lowered_wrist_and_keeps_background_and_position():
    # 只改变手臂附近的原图像素。实际人体检测和姿态模型都走生产入口。
    sample = Path(__file__).resolve().parents[2] / "samples" / "pose.jpg"
    if not sample.exists():
        pytest.skip("需要已有的公开姿态样例 pose.jpg")
    frame = decode(sample.read_bytes())
    h, w = frame.shape[:2]
    yy, xx = np.mgrid[:h, :w].astype(np.float32)
    drop = .12 * np.clip((xx / w - .545) / .155, 0, 1) * np.clip((.86 - xx / w) / .08, 0, 1)
    weight = np.clip(np.minimum((yy / h - .30) / (.15 + drop), (.635 - yy / h) / (.145 - drop)), 0, 1)
    changed = cv2.remap(frame, xx, yy - h * drop * weight, cv2.INTER_LINEAR)
    guide = Guide(get_models(), frame)
    for seq in range(7):
        result = guide.analyze(changed, seq, 3 + .24 * seq)
    assert result["checks"] == [True, True, False] and result["stage"] == "pose"
    assert result["pose"]["joint"] == 9 and "左手腕向画面上移" in result["message"]
    assert result["pose"]["actual"][9][1] - result["pose"]["target"][9][1] > .08


def test_two_independent_lawn_photos_share_landmark_but_need_different_stances():
    library = Path(__file__).resolve().parents[2] / "校园摄影参考库_队友分享_98张"
    photos = [next(library.rglob(f"REF_{key}.webp"), None) for key in ("051", "052")]
    if None in photos:
        pytest.skip("需要共享参考库红椅机位的 REF_051、REF_052")
    reference, current = (decode(path.read_bytes()) for path in photos)
    guide = Guide(get_models(), reference)
    for seq in range(4):
        result = guide.analyze(current, seq, 5 + .2 * seq)
    assert result["stage"] == "position" and result["checks"][:2] == [True, False]


