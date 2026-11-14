"""参考图的稀疏背景线稿；只提取一次，用于景别预览，不参与匹配判定。"""
import cv2
import numpy as np


MAX_PATHS = 35
MAX_POINTS = 800


def background_outline(image, people):
    """保留背景中较长的外沿，去掉人物和画幅边缘，不把掩膜边界画成轮廓。"""
    original_h, original_w = image.shape[:2]
    scale = min(1., 640 / max(original_w, original_h))
    w, h = max(1, round(original_w*scale)), max(1, round(original_h*scale))
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    if scale < 1:
        gray = cv2.resize(gray, (w, h), interpolation=cv2.INTER_AREA)
    gray = cv2.GaussianBlur(gray, (0, 0), 1.5)
    allowed = np.zeros((h, w), np.uint8)
    border = max(3, round(.022 * min(w, h)))
    allowed[border:-border, border:-border] = 255
    for box in people:
        bounds = np.asarray(box[:4], dtype=float)
        padding = .05*(bounds[2:] - bounds[:2]) + .012
        low = np.floor((bounds[:2] - padding)*[w, h]).astype(int)
        high = np.ceil((bounds[2:] + padding)*[w, h]).astype(int)
        cv2.rectangle(allowed, tuple(low), tuple(high), 0, -1)
    # 先从原灰度图提边再屏蔽人物，避免黑色遮罩制造虚假的人物矩形。
    edges = cv2.Canny(gray, 35, 90, L2gradient=True)
    edges[allowed == 0] = 0
    contours, _ = cv2.findContours(edges, cv2.RETR_LIST, cv2.CHAIN_APPROX_NONE)
    minimum = max(24., .22 * min(w, h))
    candidates = []
    for contour in contours:
        if cv2.arcLength(contour, False) < minimum:
            continue
        points = cv2.approxPolyDP(contour, max(1.8, .0045*min(w, h)), False).reshape(-1, 2)
        for path in _visible_runs(points, allowed):
            length = np.linalg.norm(np.diff(path, axis=0), axis=1).sum()
            span = np.ptp(path, axis=0).max()
            if length >= minimum and span >= .10*min(w, h):
                candidates.append((length, path))
    # 长线优先，附近重复的双边缘不重画；数量和顶点数都受限，避免满屏砖纹。
    occupied = np.zeros_like(allowed)
    paths, count = [], 0
    for _, path in sorted(candidates, key=lambda item: item[0], reverse=True):
        if count + len(path) > MAX_POINTS:
            continue
        trace = np.zeros_like(allowed)
        cv2.polylines(trace, [path.astype(np.int32)], False, 255, 1)
        pixels = trace != 0
        if not pixels.any() or np.mean(occupied[pixels] != 0) > .65:
            continue
        normalized = path.astype(float) / [w, h]
        paths.append(normalized.round(5).tolist())
        count += len(path)
        cv2.polylines(occupied, [path.astype(np.int32)], False, 255, 5)
        if len(paths) >= MAX_PATHS:
            break
    return {"kind": "background", "paths": paths} if paths else None


def _visible_runs(points, allowed):
    """简化后的线段也不能跨越人物遮挡区域或被排除的边框。"""
    if len(points) < 2:
        return
    run = [points[0]]
    for start, end in zip(points, points[1:]):
        steps = max(2, int(np.ceil(np.linalg.norm(end-start))) + 1)
        samples = np.rint(np.linspace(start, end, steps)).astype(int)
        visible = np.all(allowed[samples[:, 1], samples[:, 0]] != 0)
        if visible:
            run.append(end)
        else:
            if len(run) >= 2:
                yield np.asarray(run)
            run = [end]
    if len(run) >= 2:
        yield np.asarray(run)
