"""提取塔楼、桥梁和建筑主体的轮廓及少量结构线，全部固定在参考画幅。"""
from functools import lru_cache
from pathlib import Path

import cv2
import numpy as np
from inference import infer, ort, synchronous


# ADE20K 的类别编号。孤立的塔有时整体被分到普通建筑类。
BUILDING, SKY, PERSON, HOUSE, SIGN, HIGH_RISE, TOWER, CLOCK = 1, 2, 12, 25, 43, 48, 84, 148
WALL, WINDOW, RAILING, COLUMN, BRIDGE = 0, 8, 38, 42, 61


@lru_cache(maxsize=1)
def scene_model():
    options = ort.SessionOptions()
    options.intra_op_num_threads = 3
    options.inter_op_num_threads = 1
    options.add_session_config_entry("session.intra_op.allow_spinning", "0")
    path = Path(__file__).with_name("models") / "segformer-b0-ade-q8.onnx"
    return ort.InferenceSession(str(path), options, providers=["CPUExecutionProvider"])


async def reference_building_async(image, people, scene_hint=""):
    small = cv2.resize(image, (512, 512), interpolation=cv2.INTER_AREA)
    rgb = cv2.cvtColor(small, cv2.COLOR_BGR2RGB).astype(np.float32) / 255
    rgb = (rgb - np.float32([.485, .456, .406])) / np.float32([.229, .224, .225])
    outputs = await infer(scene_model(), ["logits"], {"pixel_values": rgb.transpose(2, 0, 1)[None]})
    logits = outputs[0][0]
    labels = logits.argmax(axis=0).astype(np.uint8)
    h, w = image.shape[:2]
    name = next((word for word in ("钟楼", "灯塔", "圆塔") if word in scene_hint), "")
    # 即使有地点标注，仍需在照片中找到足够清晰、分布合理的建筑边界。
    tower = tower_outline(image, labels, logits.max(axis=0) - logits[TOWER], w / h, people, name)
    return architectural_outline(image, labels, people, scene_hint, tower)


reference_building = synchronous(reference_building_async)


def architectural_outline(image, labels, people, hint="", tower=None):
    """语义区域限定主体，长边和外沿表达结构；不把全图砖纹都描出来。"""
    h, w = image.shape[:2]
    classes = [BUILDING, HOUSE, HIGH_RISE, TOWER, CLOCK, BRIDGE, COLUMN]
    bridge = bool(np.mean(labels == BRIDGE) > .025)
    if bridge or ("桥" in hint and "钟楼" not in hint):
        classes.append(RAILING)
    semantic = np.uint8(np.isin(labels, classes))
    # 一面无语义结构的墙或单独栏杆不足以作为标志建筑。
    if tower is None and (semantic.mean() < .045 or
                         np.mean(np.isin(labels, [BUILDING, HOUSE, HIGH_RISE, TOWER, BRIDGE, COLUMN])) < .025):
        return None
    if semantic.mean() > .06:
        semantic |= np.uint8(np.isin(labels, [WALL, WINDOW]))
    mask = cv2.resize(semantic, (w, h), interpolation=cv2.INTER_NEAREST)
    blocked = cv2.resize(np.uint8(labels == PERSON), (w, h), interpolation=cv2.INTER_NEAREST)
    for box in people:
        x, y, r, b = np.rint(np.asarray(box[:4]) * [w, h, w, h]).astype(int)
        cv2.rectangle(blocked, (max(0, x-3), max(0, y-3)), (min(w-1, r+3), min(h-1, b+3)), 1, -1)
    blocked = cv2.dilate(blocked, np.ones((7, 7), np.uint8))
    if tower:
        xy = np.vstack(tower["paths"])
        low, high = xy.min(0), xy.max(0)
        region = np.zeros((h, w), np.uint8)
        a, b = np.maximum(0, np.floor(low * [w, h] - 8)).astype(int)
        c, d = np.minimum([w, h], np.ceil(high * [w, h] + 8)).astype(int)
        region[b:d, a:c] = 1
        mask &= region
    else:
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((5, 5), np.uint8))
        count, components, stats, _ = cv2.connectedComponentsWithStats(mask)
        if count <= 1:
            return None
        idx = 1 + int(np.argmax(stats[1:, cv2.CC_STAT_AREA]))
        mask = np.uint8(components == idx)
    ys, xs = np.nonzero(mask)
    if not len(xs):
        return tower
    bounds = np.array([xs.min()/w, ys.min()/h, (xs.max()+1)/w, (ys.max()+1)/h])
    tower_name = next((name for name in ("钟楼", "灯塔", "圆塔") if name in hint), "")
    if tower is None and tower_name and (bounds[2]-bounds[0])*w > .85*(bounds[3]-bounds[1])*h:
        # 标注为钟楼机位的栏杆照可能只有桥板，不能据地点名制造塔楼。
        return None
    mask[blocked != 0] = 0
    paths = list(tower["paths"]) if tower else []
    if not tower:
        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
        for contour in sorted(contours, key=cv2.contourArea, reverse=True)[:4]:
            xy = contour.reshape(-1, 2)
            # 切断画幅边界和人物遮挡处，避免把裁切框或人物框画成建筑外沿。
            valid = (xy[:, 0] > 3) & (xy[:, 0] < w-4) & (xy[:, 1] > 3) & (xy[:, 1] < h-4)
            valid &= blocked[xy[:, 1], xy[:, 0]] == 0
            indices = np.flatnonzero(valid)
            for group in np.split(indices, np.flatnonzero(np.diff(indices) > 1) + 1):
                run = xy[group]
                if len(run) < .055 * min(w, h):
                    continue
                simple = cv2.approxPolyDP(run, max(1.2, .003*min(w, h)), False).reshape(-1, 2)
                if len(simple) >= 2:
                    paths.append(np.round(simple / [w, h], 5).tolist())
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    smooth = cv2.GaussianBlur(gray, (0, 0), 1.2)
    lines = cv2.createLineSegmentDetector().detect(smooth)[0]
    details = []
    if lines is not None:
        for line in sorted(np.asarray(lines).reshape(-1, 4), key=lambda p: np.hypot(p[2]-p[0], p[3]-p[1]), reverse=True):
            x, y, r, b = line
            if np.hypot(r-x, b-y) < .075 * min(w, h):
                break
            samples = np.rint(np.linspace([x, y], [r, b], 16)).astype(int)
            sx, sy = samples[:, 0].clip(0, w-1), samples[:, 1].clip(0, h-1)
            if np.mean(mask[sy, sx]) < .90 or blocked[sy, sx].any():
                continue
            normalized = np.array([[x/w, y/h], [r/w, b/h]])
            if any(np.linalg.norm(normalized.mean(0)-np.mean(old, 0)) < .018 for old in details):
                continue
            details.append(normalized.round(5).tolist())
            if len(details) == 18:
                break
    if not paths and len(details) < 3:
        return None
    name = tower["name"] if tower else next((s for s in ("钟楼", "灯塔", "圆塔", "廊桥", "拱廊") if s in hint),
                                             "桥梁" if bridge else "建筑")
    return {"name": name, "kind": "tower" if tower else "architecture", "paths": paths[:16],
            "details": details, "bounds": bounds.round(5).tolist()}


def _path(points, shape, closed=False):
    # 折线中的点是标签像素的中心，返回前映射回 [0,1] 的原图坐标。
    xy = np.float32(points).reshape(-1, 2)
    simple = cv2.approxPolyDP(xy, .7 if closed else 1., closed).reshape(-1, 2)
    if closed:
        simple = np.vstack((simple, simple[0]))
    return np.round((simple + .5) / np.array(shape[::-1]), 4).tolist()


def _clock_facade(labels, usable, aspect):
    """塔顶出画时，钟面与相邻的建筑外沿仍然能构成可靠的对齐目标。"""
    h, w = labels.shape
    wall = np.isin(labels, [BUILDING, HOUSE, HIGH_RISE, TOWER, CLOCK]) & usable
    sky = labels == SKY
    clocks, _ = cv2.findContours(np.uint8((labels == CLOCK) & usable), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    for clock in sorted(clocks, key=cv2.contourArea, reverse=True):
        x, y, cw, ch = cv2.boundingRect(clock)
        if min(cw, ch) < 6 or not .6 <= cw * aspect / ch <= 1.65:
            continue
        left, right = x, x + cw - 1
        line = wall[y + ch // 2]
        while left > 0 and line[left-1]:
            left -= 1
        while right < w-1 and line[right+1]:
            right += 1
        if not 1.4 * cw <= right-left <= .56 * w:
            continue
        paths = []
        for edge, side in ((left, -1), (right, 1)):
            rows = []
            for j in range(max(1, y - 2*ch), min(h, y + 6*ch)):
                xs = np.arange(max(1, edge-cw//2), min(w-1, edge+cw//2+1))
                hits = xs[wall[j, xs] & sky[j, xs+side]]
                if len(hits):
                    rows.append((int(hits[np.argmin(abs(hits-edge))]), j))
            # 连续的外墙边缘才可画。分别评估两侧，被遮挡的一侧可以缺失。
            for run in np.split(np.asarray(rows).reshape(-1, 2), np.where(np.diff([r[1] for r in rows]) > 2)[0]+1):
                if len(run) >= max(10, 2*ch):
                    paths.append(_path(run, labels.shape))
        if paths:
            paths.append(_path(clock, labels.shape, closed=True))
            return {"name": "钟楼", "paths": paths}
    return None


def _skyline(mask):
    """沿两侧和塔顶描一条开放折线，底部在树木或裙楼上方收笔。"""
    _, y, _, height = cv2.boundingRect(np.uint8(mask))
    # 从接近塔脚、仍有正常宽度的横截面开始，避免在孤立底部像素处闭合。
    widths = mask[y:y+height].sum(1)
    base = y + np.flatnonzero(widths >= .55*np.median(widths))[-1]
    visible = mask.copy()
    visible[base+1:] = False
    contours, _ = cv2.findContours(np.uint8(visible), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
    rim = max(contours, key=cv2.contourArea).reshape(-1, 2)
    bottom = np.flatnonzero(rim[:, 1] == rim[:, 1].max())
    left, right = bottom[np.argmin(rim[bottom, 0])], bottom[np.argmax(rim[bottom, 0])]
    if left > right:
        left, right = right, left
    across, around = rim[left:right+1], np.vstack((rim[right:], rim[:left+1]))
    stroke = across if across[:, 1].min() < around[:, 1].min() else around
    return _path(stroke, mask.shape)


def _refine_skyline(image, path):
    """在分割预测周围的窄带内沿原图颜色修正，保留细小的塔帽与屋檐。"""
    h, w = image.shape[:2]
    points = np.rint(np.array(path)*[w, h]).astype(np.int32)
    x, y, width, height = cv2.boundingRect(points)
    pad = max(4, round(.12*min(width, height)))
    x0, y0 = max(0, x-pad), max(0, y-pad)
    crop = image[y0:min(h, y+height), x0:min(w, x+width+pad)]
    mask = np.zeros(crop.shape[:2], np.uint8)
    cv2.fillPoly(mask, [points - [x0, y0]], 1)
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2*pad-3,)*2)
    inside, near = cv2.erode(mask, kernel), cv2.dilate(mask, kernel)
    if not inside.any() or near.all():
        return path
    seeds = np.where(near, cv2.GC_PR_BGD, cv2.GC_BGD).astype(np.uint8)
    seeds[mask != 0], seeds[inside != 0] = cv2.GC_PR_FGD, cv2.GC_FGD
    cv2.grabCut(crop, seeds, None, None, None, 3, cv2.GC_INIT_WITH_MASK)
    count, parts, _, _ = cv2.connectedComponentsWithStats(seeds & 1)
    votes = np.bincount(parts[inside != 0], minlength=count)
    refined = parts == (1 + np.argmax(votes[1:]))
    if (refined & mask).sum() < .70 * (refined | mask).sum():
        return path
    ch, cw = mask.shape
    xy = np.array(_skyline(refined))*[cw, ch] + [x0, y0]
    return np.round(xy / [w, h], 4).tolist()


def tower_outline(image, labels, tower_gap, aspect, people, name):
    h, w = labels.shape
    sky = labels == SKY
    if sky.mean() < .07:
        return None
    usable = np.ones((h, w), np.uint8)
    person_pixels = cv2.dilate(np.uint8(labels == PERSON), np.ones((3, 3), np.uint8))
    for box in people:
        x, y = np.clip(np.floor(box[:2] * [w, h]).astype(int), 0, [w, h])
        r, b = np.clip(np.ceil(box[2:4] * [w, h]).astype(int), 0, [w, h])
        person = person_pixels[y:b, x:r]
        if not person.size:
            continue
        # 半身框也可能框住主角肩膀旁的建筑，优先遮除实际的人像像素。
        if person.mean() >= .16:
            usable[y:b, x:r] &= 1 - person
        else:
            usable[y:b, x:r] = 0
    usable = usable.astype(bool)
    classes = [BUILDING, HOUSE, HIGH_RISE, TOWER, CLOCK]
    if not name or name == "钟楼":
        classes.append(SIGN)  # 远处狭长的钟面墙有时被分成标识牌。
    architecture = np.isin(labels, classes) & usable
    # 提取左右两侧都邻接天空的连续横截面，避免把相连的裙楼和树梢画进去。
    spans = cv2.morphologyEx(np.uint8(~sky), cv2.MORPH_CLOSE, np.ones((1, 5), np.uint8))
    body = np.zeros_like(spans)
    for y, row in enumerate(spans):
        bounds = np.flatnonzero(np.diff(np.r_[0, row, 0])).reshape(-1, 2)
        for x, r in bounds:
            if 2 <= x and r <= w-2 and .035*w <= r-x <= .44*w and architecture[y, x:r].mean() > .56:
                body[y, x:r] = (architecture[y, x:r] | sky[y, x:r]) & usable[y, x:r]
    _, regions, stats, _ = cv2.connectedComponentsWithStats(body)
    candidates = []
    for idx, (x, y, width, height, area) in enumerate(stats[1:], 1):
        if y < 2 or height < .095*h or area < .003*w*h:
            continue
        mask = regions == idx
        widths = mask[y:y+height].sum(1)
        typical = np.median(widths[1:max(2, round(.7*height))])
        # 塔脚接到更宽的屋顶时，仅画实际露出的上半截。
        flares = np.flatnonzero(widths > 1.43*typical + 1)
        if len(flares) and flares[0] > .45*height:
            mask[y+flares[0]:] = False
        # 有时天空分割把塔脚与一段细长屋檐粘在一起。限定在塔身的横向范围内，
        # 不让两三个像素厚的屋檐拖出一条很长的假轮廓。
        upper = mask[y:y+max(2, round(.55*height))]
        cols = np.flatnonzero(upper.sum(0) >= .22 * len(upper))
        if not len(cols):
            continue
        padding = max(2, round(.20*typical))
        mask[:, :max(0, cols[0]-padding)] = False
        mask[:, cols[-1]+padding+1:] = False
        x, y, width, height = cv2.boundingRect(np.uint8(mask))
        if (height < .095*h or typical*aspect/height > .72 or mask.sum() < .48*width*height
                or sky[max(0, y-4):y, x:x+width].mean() < .64):
            continue
        semantic = np.isin(labels[mask], [TOWER, CLOCK]).mean()
        # 缺少地点提示时，轮廓须有塔类语义证据；又细又高的独立塔可适当放宽。
        slender = height >= .18*h and typical*aspect/height <= .48
        if not name and semantic < .10 and np.median(tower_gap[mask]) > (3.6 if slender else 2.2):
            continue
        label = name or ("钟楼" if (labels[mask] == CLOCK).sum() >= 8 else "塔楼")
        paths = [_skyline(mask)]
        clock_faces, _ = cv2.findContours(np.uint8((labels == CLOCK) & mask), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        for face in clock_faces:
            if cv2.contourArea(face) >= 12:
                paths.append(_path(face, labels.shape, closed=True))
        candidates.append((mask.sum()*(1+semantic), {"name": label, "paths": paths}))
    if candidates:
        best = max(candidates, key=lambda item: item[0])[1]
        best["paths"][0] = _refine_skyline(image, best["paths"][0])
        return best
    return _clock_facade(labels, usable, aspect)
