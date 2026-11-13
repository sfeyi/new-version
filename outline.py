"""从选中的参考人物提取外轮廓和少量内部线条，坐标相对于原始取景画幅。"""
import cv2
import numpy as np
from inference import foreground_mask, synchronous


def selected_component(confidence, bounds, joints):
    x, y, r, b = bounds
    span = np.array([r-x, b-y])
    count, labels, stats, _ = cv2.connectedComponentsWithStats(np.uint8(confidence >= .48))
    best, best_score = None, (0, 0)
    for label in range(1, count):
        _, _, mw, mh, pixels = stats[label]
        if pixels < .08 * np.prod(span) or mw < .40 * span[0] or mh < .55 * span[1]:
            continue
        component = np.uint8(labels == label)
        inside = cv2.countNonZero(component[y:b, x:r])
        if inside < .7 * pixels:
            continue
        near = cv2.dilate(component, np.ones((3, 3), np.uint8))
        hits = int(near[joints[:, 1], joints[:, 0]].sum())
        if hits >= .50 * len(joints) and (hits, inside) > best_score:
            best, best_score = component, (hits, inside)
    return best


def outline_from_joints(points, size, offset, shape, span):
    if len(points) != 17:
        return None
    local = [np.rint(np.asarray(p) * size - offset).astype(int) if p is not None else None for p in points]
    mask = np.zeros(shape, np.uint8)
    major = max(span)
    shoulders = [local[i] for i in (5, 6) if local[i] is not None]
    width = np.linalg.norm(shoulders[0] - shoulders[1]) if len(shoulders) == 2 else .24 * major
    width = float(np.clip(width, .14 * major, .50 * major))
    trunk = [local[i] for i in (5, 6, 11, 12) if local[i] is not None]
    if len(trunk) >= 3:
        cv2.fillConvexPoly(mask, cv2.convexHull(np.int32(trunk)), 1)
    for first, last, thickness in [(5,6,.24), (5,7,.27), (7,9,.19), (6,8,.27), (8,10,.19),
                                   (5,11,.30), (6,12,.30), (11,12,.30),
                                   (11,13,.37), (13,15,.24), (12,14,.37), (14,16,.24)]:
        if local[first] is not None and local[last] is not None:
            cv2.line(mask, local[first], local[last], 1, max(2, round(width*thickness)))
    eyes = local[1:3]
    eye_line = eyes[1]-eyes[0] if all(p is not None for p in eyes) else None
    face = local[0]
    if face is None and eye_line is not None:
        face = np.rint(np.mean(eyes, axis=0)).astype(int)
    if face is not None:
        eye_span = np.linalg.norm(eye_line) if eye_line is not None else 0
        angle = float(np.degrees(np.arctan2(eye_line[1], eye_line[0]))) if eye_line is not None else 0
        radius = max(2, round(max(.23*width, 1.1*eye_span)))
        cv2.ellipse(mask, face, (radius, round(1.3*radius)), angle, 0, 360, 1, -1)
        if len(shoulders) == 2:
            neck = np.rint(np.mean(shoulders, axis=0)).astype(int)
            cv2.line(mask, face, neck, 1, max(2, round(.22*width)))
    return mask if mask.any() else None


async def reference_outline_async(frame, box, points, foreground, turns=0):
    if box is None:
        return None
    height, width = frame.shape[:2]
    size = np.array([width, height])
    low, high = box[:2] * size, box[2:4] * size
    span = high - low
    if min(span) < 7:
        return None
    margin = np.maximum(3, span * .14)
    left, top = np.maximum(0, np.floor(low - margin)).astype(int)
    right, bottom = np.minimum(size, np.ceil(high + margin)).astype(int)
    crop = frame[top:bottom, left:right]
    h, w = crop.shape[:2]
    side = max(h, w)
    async def segment(image):
        view = np.ascontiguousarray(np.rot90(image, turns))
        return np.rot90(await foreground_mask(foreground, view), -turns)

    xpad, ypad = (side - w) // 2, (side - h) // 2
    square = cv2.copyMakeBorder(crop, ypad, side-h-ypad, xpad, side-w-xpad,
                                cv2.BORDER_CONSTANT, value=(127, 127, 127))
    offset = np.array([left, top])
    joints = np.array([p for p in points if p is not None]).reshape(-1, 2)
    joints = np.clip(np.rint(joints * size - offset).astype(int), 0, [w-1, h-1])
    bounds = np.r_[np.floor(low - offset), np.ceil(high - offset)].astype(int)
    confidence = cv2.resize(await segment(square), (side, side))[ypad:ypad+h, xpad:xpad+w]
    best = selected_component(confidence, bounds, joints)
    if best is None and h != w:
        # 狭长的远景人像可再铺满模型输入，给四肢更多像素。
        best = selected_component(cv2.resize(await segment(crop), (w, h)), bounds, joints)
    kind = "person" if best is not None else "joints"
    if best is None:
        # 只画有观测关节的身体部位，弱分割时不把背景纹理描成人物。
        best = outline_from_joints(points, size, offset, (h, w), span)
    if best is None:
        return None

    epsilon = max(.65, .0025 * max(span))

    def coordinates(path, closed):
        xy = cv2.approxPolyDP(path, epsilon, closed).reshape(-1, 2)
        return ((xy + offset) / size).round(5).tolist()

    contours, _ = cv2.findContours(best, cv2.RETR_CCOMP, cv2.CHAIN_APPROX_SIMPLE)
    # 保留弯臂、双腿之间的空隙，小孔洞和衣物纹理无需环环描边。
    min_area = max(4, .002 * cv2.countNonZero(best))
    outline = [coordinates(c, True) for c in contours if cv2.contourArea(c) >= min_area]
    if not outline:
        return None
    if kind == "joints":
        return {"kind": kind, "contours": outline, "details": []}
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    smoothed = cv2.GaussianBlur(gray, (0, 0), max(.85, .0035 * max(span)))
    edges = cv2.Canny(smoothed, 45, 105)
    radius = max(1, round(.012 * max(span)))
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2*radius+1, 2*radius+1))
    edges[cv2.erode(best, kernel) == 0] = 0
    strokes, _ = cv2.findContours(edges, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    strokes = [s for s in strokes if max(cv2.boundingRect(s)[2:]) >= .12 * max(span)]
    strokes.sort(key=lambda s: cv2.arcLength(s, False), reverse=True)
    return {"kind": kind, "contours": outline, "details": [coordinates(s, False) for s in strokes[:8]]}


reference_outline = synchronous(reference_outline_async)
