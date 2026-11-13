"""YOLOX 人体框和 RTMPose 的 COCO 17 个关节。对外坐标均相对于完整取景画面。"""
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np
from inference import infer, ort, synchronous


MODEL_DIR = Path(__file__).resolve().parent / "models"
MEAN = np.array([123.675, 116.28, 103.53], dtype=np.float32)
STD = np.array([58.395, 57.12, 57.375], dtype=np.float32)
EDGES = [(1, 2), (0, 1), (0, 2), (5, 6), (5, 7), (7, 9), (6, 8), (8, 10),
         (5, 11), (6, 12), (11, 12), (11, 13), (13, 15), (12, 14), (14, 16)]
NAMES = ("鼻尖", "左眼", "右眼", "左耳", "右耳", "左肩", "右肩", "左肘", "右肘",
         "左手腕", "右手腕", "左髋", "右髋", "左膝", "右膝", "左脚踝", "右脚踝")


def center(box):
    return (box[:2] + box[2:4]) / 2


def area(box):
    return float(np.prod(np.maximum(0, box[2:4] - box[:2])))


def overlap(a, b):
    intersection = float(np.prod(np.maximum(0, np.minimum(a[2:4], b[2:4]) - np.maximum(a[:2], b[:2]))))
    return intersection / max(area(a) + area(b) - intersection, 1e-8)


def contains(box, point):
    return np.all(box[:2] <= point) and np.all(point <= box[2:4])


def rotate_xy(xy, turns):
    """np.rot90 对归一化图像坐标的映射。逆变换用负的 turns。"""
    x, y = np.asarray(xy).T
    return (np.asarray(xy), np.stack((y, 1-x), axis=-1), 1-np.asarray(xy),
            np.stack((1-y, x), axis=-1))[turns % 4]


def rotate_box(box, turns):
    corners = rotate_xy(box[:4].reshape(2, 2), turns)
    return np.r_[corners.min(0), corners.max(0), box[4]]


def choose(boxes, *, tap=None, previous=None, target=None):
    """用户点选优先。跟踪时只接续空间上相邻的人，避免路人经过时切换主角。"""
    if tap is not None:
        hits = [box for box in boxes if contains(box, tap)]
        return min(hits, key=lambda b: np.linalg.norm(center(b) - tap)) if hits else None
    if not len(boxes):
        return None
    if previous is not None:
        size = np.linalg.norm(previous[2:4] - previous[:2])
        nearby = [b for b in boxes if overlap(b, previous) > .10 or
                  (np.linalg.norm(center(b) - center(previous)) < max(.08, .38 * size)
                   and .35 < area(b) / max(area(previous), 1e-8) < 3)]
        if not nearby:
            return None
        return max(nearby, key=lambda b: overlap(b, previous) - .4 * np.linalg.norm(center(b) - center(previous)))
    if target is None:
        return max(boxes, key=lambda b: np.sqrt(area(b)) * b[4])
    def distance(b):
        return (np.linalg.norm(center(b) - center(target))
                + .16 * abs(np.log(max(area(b), 1e-8) / max(area(target), 1e-8))))
    return min(boxes, key=distance)


@dataclass
class Person:
    box: np.ndarray
    xy: np.ndarray
    scores: np.ndarray

    def visible(self, threshold):
        return ((self.scores >= threshold) & (self.xy >= 0).all(axis=1)
                & (self.xy <= 1).all(axis=1))


class Models:
    def __init__(self, directory=MODEL_DIR):
        options = ort.SessionOptions()
        options.intra_op_num_threads = 3
        options.inter_op_num_threads = 1
        options.add_session_config_entry("session.intra_op.allow_spinning", "0")
        self.detector = ort.InferenceSession(str(directory / "yolox-tiny.onnx"), options,
                                             providers=["CPUExecutionProvider"])
        self.estimator = ort.InferenceSession(str(directory / "rtmpose-m.onnx"), options,
                                              providers=["CPUExecutionProvider"])
        self.segmenter = ort.InferenceSession(str(directory / "pphumanseg.onnx"), options,
                                              providers=["CPUExecutionProvider"])
        self.det_input = self.detector.get_inputs()[0].name
        self.pose_input = self.estimator.get_inputs()[0].name
        self.seg_input = self.segmenter.get_inputs()[0].name

    async def foreground_async(self, frame):
        """PP-HumanSeg 的前景概率，供近景人像保留头肩两侧真实可见的背景。"""
        blob = cv2.dnn.blobFromImage(frame, 1 / 127.5, (192, 192), (127.5,) * 3, swapRB=True)
        masks = await infer(self.segmenter, None, {self.seg_input: blob})
        return masks[0][0, 1]

    async def _boxes(self, frame, rect):
        h, w = frame.shape[:2]
        x, y, right, bottom = np.rint(rect * [w, h, w, h]).astype(int)
        crop = frame[y:bottom, x:right]
        ch, cw = crop.shape[:2]
        if min(ch, cw) < 8:
            return []
        ratio = 416 / max(ch, cw)
        rw, rh = max(1, round(cw * ratio)), max(1, round(ch * ratio))
        padded = np.full((416, 416, 3), 114, np.uint8)
        padded[:rh, :rw] = cv2.resize(crop, (rw, rh))
        dets, labels = await infer(self.detector, ["dets", "labels"], {self.det_input: cv2.dnn.blobFromImage(padded)})
        found = []
        for row, label in zip(dets.reshape(-1, 5), labels.ravel()):
            if label != 0 or row[4] < .34:
                continue
            box = row.copy()
            box[:4] = (box[:4] / np.array([rw / cw, rh / ch, rw / cw, rh / ch]) + [x, y, x, y]) / [w, h, w, h]
            box[:4] = np.clip(box[:4], [rect[0], rect[1], rect[0], rect[1]], [rect[2], rect[3], rect[2], rect[3]])
            if min((box[2:4] - box[:2]) * [w, h]) >= 7:
                found.append(box)
        return found

    async def boxes_async(self, frame, *, survey=False, focus=None):
        full = await self._boxes(frame, np.array([0., 0., 1., 1.]))
        regions = []
        if survey:
            # 参考照只分析一次。重叠切片保留校园远景里很小的人。
            regions = [[x, y, x + .62, y + .62] for x in (0., .38) for y in (0., .38)]
        found = list(full)
        async def search(region):
            rect = np.asarray(region)
            for box in await self._boxes(frame, rect):
                # 切片截断的人体不应挤掉完整人体框。
                cut = ((rect[:2] > 0) & (box[:2] < rect[:2] + .012)).any() or (
                    (rect[2:] < 1) & (box[2:4] > rect[2:] - .012)).any()
                if not cut and not any(overlap(box, known) > .40 for known in found):
                    found.append(box)
        for region in regions:
            await search(region)
        if survey and max((area(b) for b in found), default=0.) < .009:
            # 大景里远处的路人可能比坐姿主角更容易检出，近一档扫描寻找主体。
            for x in (.05, .30, .55):
                for y in (.05, .30, .55):
                    await search([x, y, x + .40, y + .40])
        if focus is not None and not any(contains(b, focus) for b in found):
            for side in (.36, .60):
                left, top = np.clip(np.array(focus) - side / 2, 0, 1 - side)
                await search([left, top, left + side, top + side])
                if any(contains(b, focus) for b in found):
                    break
            if not any(contains(b, focus) for b in found):
                grid = np.clip(np.rint(np.asarray(focus) * 4) / 4, .25, .75) - .20
                await search([*grid, *(grid + .40)])
            if not any(contains(b, focus) for b in found):
                left, top = np.where(np.asarray(focus) > .5, .38, 0.)
                await search([left, top, left + .62, top + .62])
        return found

    async def observe_async(self, frame, turns=0, *, survey=False, tap=None, focus=None, previous=None, target=None):
        view = np.ascontiguousarray(np.rot90(frame, turns))
        aimed = rotate_xy(focus, turns) if focus is not None else None
        raw = await self.boxes_async(view, survey=survey, focus=aimed)
        boxes = [rotate_box(b, -turns) for b in raw] if turns else raw
        box = choose(boxes, tap=tap, previous=previous, target=target)
        if box is None:
            return boxes, None
        measured = await self.pose_async(view, rotate_box(box, turns) if turns else box)
        person = Person(box, rotate_xy(measured.xy, -turns), measured.scores) if turns else measured
        return boxes, person

    async def pose_async(self, frame, box):
        h, w = frame.shape[:2]
        x, y = center(box) * [w, h]
        bw, bh = (box[2:4] - box[:2]) * [w, h]
        height = 1.25 * max(bh, bw * 256 / 192)
        scale = 256 / height
        matrix = np.float32([[scale, 0, 96 - x * scale], [0, scale, 128 - y * scale]])
        crop = cv2.warpAffine(frame, matrix, (192, 256))
        rgb = cv2.cvtColor(crop, cv2.COLOR_BGR2RGB)
        blob = cv2.dnn.blobFromImage((rgb.astype(np.float32) - MEAN) / STD)
        sx, sy = await infer(self.estimator, ["simcc_x", "simcc_y"], {self.pose_input: blob})
        coords = np.column_stack((sx[0].argmax(1), sy[0].argmax(1))) / 2.
        xy = ((coords - [96, 128]) / scale + [x, y]) / [w, h]
        scores = np.minimum(sx[0].max(1), sy[0].max(1))
        # 裁剪边缘外的峰值常来自补零或相邻人物。
        margin = .075 * (box[2:4] - box[:2])
        inside = ((xy >= box[:2] - margin) & (xy <= box[2:4] + margin)).all(axis=1)
        return Person(box, xy, np.where(inside, scores, 0.))

    boxes = synchronous(boxes_async)
    observe = synchronous(observe_async)
    foreground = synchronous(foreground_async)
    pose = synchronous(pose_async)

