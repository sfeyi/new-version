"""在参考画面选固定地标，并从分散的对应点测量相对景别。"""
from functools import lru_cache
from pathlib import Path

import cv2
import numpy as np
from inference import infer, ort, synchronous


def reference_landmarks(gray, mask):
    h, w = gray.shape
    short = min(w, h)
    points = cv2.goodFeaturesToTrack(gray, 240, .025, max(3., .025 * short), mask=mask)
    if points is None:
        return np.empty((0, 2), np.float32)
    points = points.reshape(-1, 2)
    selected = []
    size = np.float32([w, h])
    for location in ((.24, .30), (.76, .30), (.5, .22), (.5, .74)):
        distances = np.linalg.norm(points - size * location, axis=1)
        for earlier in selected:
            distances[np.linalg.norm(points - earlier, axis=1) < .22 * short] = np.inf
        index = np.argmin(distances)
        if distances[index] < .40 * short:
            selected.append(points[index])
    return np.asarray(selected, np.float32).reshape(-1, 2) / size


def landmark_scale(reference, current, size):
    """用相隔足够远的内点对量比例，局促或透视不一致的点只用于指方向。"""
    if len(reference) > 64:
        # 同一扇窗的几十个角点只占一个格子，避免密集纹理支配尺度。
        _, indices = np.unique(np.int32(reference * 8), axis=0, return_index=True)
        reference, current = reference[indices], current[indices]
    i, j = np.triu_indices(len(reference), 1)
    scale = size / min(size)
    before = np.linalg.norm((reference[i] - reference[j]) * scale, axis=1)
    after = np.linalg.norm((current[i] - current[j]) * scale, axis=1)
    far = (before >= .16) & (after >= .12)
    if far.sum() < 5 or len(np.unique(np.r_[i[far], j[far]])) < 4:
        return None
    ratios = after[far] / before[far]
    low, median, high = np.quantile(ratios, [.15, .5, .85])
    return float(median) if high / low <= 1.18 else None


@lru_cache(maxsize=1)
def xfeat_model():
    options = ort.SessionOptions()
    options.intra_op_num_threads = 2
    options.inter_op_num_threads = 1
    options.add_session_config_entry("session.intra_op.allow_spinning", "0")
    model = Path(__file__).with_name("models") / "xfeat.onnx"
    return ort.InferenceSession(str(model), options, providers=["CPUExecutionProvider"])


async def xfeat_features_async(gray, mask):
    """遮开人物和无纹理区域，只保留背景中可靠的 XFeat 局部描述子。"""
    h, w = gray.shape
    margin = max(5, round(.021 * min(h, w)))
    safe = cv2.erode(mask, np.ones((2 * margin + 1,) * 2, np.uint8))
    gradient = np.abs(cv2.Sobel(gray, cv2.CV_32F, 1, 0)) + np.abs(cv2.Sobel(gray, cv2.CV_32F, 0, 1))
    safe[cv2.boxFilter(gradient, -1, (9, 9)) < 12] = 0
    if cv2.countNonZero(safe) < 64:
        return np.empty((0, 2), np.float32), None
    # V1.0.0 的描述子采样网格是按 800 × 600 导出的。用同一尺寸等比补边，
    # 否则竖屏图的关键点会取到别处的描述子，转动镜头时方向和距离都会偏。
    ratio = min(800 / w, 600 / h)
    rw, rh = max(1, round(w * ratio)), max(1, round(h * ratio))
    left, top = (800 - rw) // 2, (600 - rh) // 2
    scaled = cv2.resize(gray, (rw, rh))
    padded = cv2.copyMakeBorder(scaled, top, 600-rh-top, left, 800-rw-left, cv2.BORDER_REPLICATE)
    inputs = cv2.dnn.blobFromImage(cv2.cvtColor(padded, cv2.COLOR_GRAY2BGR), 1 / 255.)
    xy, descriptors = await infer(xfeat_model(), ["keypoints", "descriptors"], {"images": inputs})
    xy = (xy - np.float32([left, top])) / np.float32([rw, rh])
    inside = ((xy >= 0) & (xy < 1)).all(axis=1)
    x, y = np.minimum(np.maximum(xy * [w, h], 0).astype(int), [w - 1, h - 1]).T
    keep = np.flatnonzero(inside & (safe[y, x] != 0))[:1000]
    return xy[keep], descriptors[keep]


xfeat_features = synchronous(xfeat_features_async)
