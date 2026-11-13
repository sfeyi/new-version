"""按顺序引导机位、站位和姿势，在取景帧上核验用户保留的步骤。"""
import math
from threading import Lock

from background import Background, CENTER_RELEASE, CENTER_TOLERANCE, SCALE_RELEASE, SCALE_TOLERANCE
from building import reference_building_async
from inference import Prepared, synchronous
from outline import reference_outline_async
from people import EDGES, center, choose
from posture import POSITION_IOU, POSITION_RELEASE_IOU, PoseTarget, measure_position


STAGES = ("background", "position", "pose", "ready")
GUIDED_STAGES = STAGES[:-1]
HOLD_SECONDS = (.32, .35, .50)
HOLD_HINTS = ("地标中心和大小已对齐，请稳住相机", "人物已进入目标框，请保持站位", "动作已对齐，请保持片刻")


class Guide(Prepared):
    async def prepare(self, models, frame, scene_hint="", tap=None):
        h, w = frame.shape[:2]
        self.aspect = w / h
        self.models = models
        self.scene_hint = scene_hint
        # 部分参考照像素本身就是横躺/倒置的。仅常规方向无法建模时试转向，
        # 选定的方向也用于实时人体推理，所有 UI 和背景坐标仍在原画面中。
        reason = "参考照中未能定位主角，可点选照片里的人物。"
        candidates = []
        upright_boxes = []
        for turns in (0, 1, 3, 2):
            boxes, person = await models.observe_async(frame, turns, survey=True, focus=tap, tap=tap)
            if turns == 0:
                upright_boxes = boxes
            if person is None:
                continue
            try:
                target = PoseTarget(person, w, h)
            except ValueError as exc:
                reason = str(exc)
                continue
            quality = float(person.scores[target.visible].sum())
            candidates.append((quality, turns, boxes, target))
            if turns == 0:
                break
        self.turns, boxes, self.target = 0, upright_boxes, None
        if candidates:
            _, self.turns, boxes, self.target = max(candidates, key=lambda item: item[0])
        self.reference_box = self.target.reference.box if self.target else choose(boxes, tap=tap)
        self.background = await Background.create(frame, boxes, models.foreground_async)
        points = self.target.points if self.target else []
        self.reference = {"width": w, "height": h,
                          "centerTolerance": CENTER_TOLERANCE,
                          "landmarks": self.background.landmarks.tolist(),
                          "buildingOutline": await reference_building_async(frame, boxes, scene_hint),
                          "box": self.reference_box[:4].tolist() if self.reference_box is not None else None,
                          "anchor": center(self.reference_box).tolist() if self.reference_box is not None else None,
                          "boxes": [b[:4].tolist() for b in boxes],
                          "points": points,
                          "outline": await reference_outline_async(frame, self.reference_box, points, models.foreground_async, self.turns),
                          "subjectHint": "" if self.target else reason,
                          "edges": EDGES, "viewpoint": scene_hint, "canMatchBackground": self.background.usable}
        self.lock = Lock()
        self.stage = 0
        self.skipped = frozenset()
        self.since = None
        self.last_seq = -1
        self.last_time = None
        self.source_id = None
        self.aim_sample = None
        self.last_box = None
        self.last_seen = None
        self.pending_tap = None
        self.pinned = False

    __init__ = synchronous(prepare)

    def next_required(self, start):
        return next((i for i in range(start, len(GUIDED_STAGES)) if STAGES[i] not in self.skipped),
                    len(GUIDED_STAGES))

    def steady_background(self, bg, now):
        """只有中心、角度和景别都停止明显漂移才允许调倍率。"""
        if self.stage != 0 or not bg or not bg["centerOk"] or bg["zoom"] is None:
            self.aim_sample = None
            return
        x, y = bg["current"]
        zoom, roll = bg["zoom"], bg["roll"]
        if self.aim_sample:
            _, old_x, old_y, old_zoom, old_roll = self.aim_sample
            drift = math.hypot((x - old_x) * self.aspect, y - old_y) / min(self.aspect, 1.)
            if drift > .035 or abs(math.log(zoom / old_zoom)) > .06 or abs(roll - old_roll) > 3:
                self.aim_sample = None
        if self.aim_sample is None:
            self.aim_sample = now, x, y, zoom, roll
            self.since = None
        if bg["scaleOk"]:
            return
        elapsed = now - self.aim_sample[0]
        if elapsed >= HOLD_SECONDS[0]:
            bg["zoomFactor"] = round(1 / zoom, 4)
        else:
            bg.update(control="steady", mode="hold", message="圆心已重合，稳住镜头后会自动匹配景别")
            return elapsed / HOLD_SECONDS[0]

    async def analyze_async(self, frame, seq, now, tap=None, skipped=None, source_id=None):
        if seq <= self.last_seq:
            raise ValueError("画面已过期，请使用递增帧号。")
        h, w = frame.shape[:2]
        if abs(w / h / self.aspect - 1) > .018:
            raise ValueError("取景画幅与参考照不同，请按参考照的宽高比取景。")
        if source_id is not None and source_id != self.source_id:
            self.source_id = source_id
            self.stage, self.since, self.aim_sample = 0, None, None
            self.last_box, self.last_seen, self.pending_tap, self.pinned = None, None, None, False
        if skipped is not None:
            requested = frozenset(skipped)
            if not requested.issubset(GUIDED_STAGES):
                raise ValueError("可跳过的步骤为机位、站位和姿势。")
            if self.skipped - requested:
                # 恢复的步骤必须从新画面重新确认。
                self.stage, self.since, self.aim_sample = 0, None, None
            self.skipped = requested
        if self.last_time is not None and now - self.last_time > 1.6:
            self.stage, self.since, self.aim_sample = 0, None, None
        self.last_seq, self.last_time = seq, now
        if tap is not None:
            self.pinned = True
            self.pending_tap = tap
            self.last_box, self.since = None, None
            self.stage = min(self.stage, 1)
        if STAGES[self.stage] in self.skipped:
            self.stage, self.since = self.next_required(self.stage), None
        boxes, person = [], None
        if len(self.skipped) < len(GUIDED_STAGES):
            focus = self.pending_tap
            focus_box = self.last_box if self.last_box is not None else self.reference_box
            if focus is None and focus_box is not None:
                focus = center(focus_box)
            boxes, person = await self.models.observe_async(frame, self.turns, focus=focus, tap=self.pending_tap,
                                                            previous=self.last_box, target=self.reference_box)
        box = person.box if person is not None else None
        if box is not None:
            self.last_box, self.last_seen, self.pending_tap = box, now, None
        elif not self.pinned and self.last_seen is not None and now - self.last_seen > 1.2:
            self.last_box = None
        bg = None
        if "background" not in self.skipped:
            bg = await self.background.measure_async(frame, boxes, CENTER_RELEASE if self.stage > 0 else CENTER_TOLERANCE,
                                                     SCALE_RELEASE if self.stage > 0 else SCALE_TOLERANCE)
        min_overlap = POSITION_RELEASE_IOU if self.stage > 1 else POSITION_IOU
        if self.target:
            position, pose = self.target.compare(person, min_overlap)
        else:
            position, pose = measure_position(self.reference_box, box, min_overlap), None
        values = (bg, position, pose)
        checks = [None if stage in self.skipped else value is not None and bool(value["ok"])
                  for stage, value in zip(GUIDED_STAGES, values)]
        earliest = next((i for i, ok in enumerate(checks) if ok is False), 3)
        if earliest < self.stage:
            self.stage, self.since = earliest, None
        fraction = self.steady_background(bg, now) or 0.
        if self.stage < 3 and checks[self.stage]:
            if self.since is None:
                self.since = now
            fraction = min(1., (now - self.since) / HOLD_SECONDS[self.stage])
            if fraction >= 1:
                self.stage = self.next_required(self.stage + 1)
                self.since = now if self.stage < 3 and checks[self.stage] else None
                fraction = 0.
        else:
            self.since = None
        result = {"seq": seq, "stage": STAGES[self.stage], "checks": checks,
                  "skipped": [stage for stage in GUIDED_STAGES if stage in self.skipped], "hold": round(fraction, 2),
                  "ready": self.stage == 3, "boxes": [b[:4].tolist() for b in boxes],
                  "selected": box[:4].tolist() if box is not None else None,
                  "background": bg, "position": position if "position" not in self.skipped else None,
                  "pose": pose if self.stage >= 2 and "pose" not in self.skipped else None}
        if self.stage == 3:
            msg = "可以拍摄，保持现在的机位和动作"
            if len(self.skipped) == len(GUIDED_STAGES):
                msg = "已跳过全部引导，可以直接拍摄"
            return result | {"mode": "ready", "message": msg}
        active = values[self.stage]
        building = self.reference["buildingOutline"]
        if self.stage == 0 and building:
            name = building["name"]
            if not bg:
                return result | {"mode": "search", "message": f"转动镜头，让{name}的建筑边缘贴近金色参考轮廓"}
            if bg["control"] == "steady":
                return result | {"mode": "hold", "message": f"{name}已接近参考位置，稳住镜头以匹配景别"}
            if bg["control"] == "coverage":
                return result | {"mode": "fine", "message": f"保持{name}贴近轮廓，让两侧固定景物同时入镜以核对景别"}
            if checks[0]:
                return result | {"mode": "hold", "message": f"{name}的位置和大小已接近参考，请稳住相机"}
        if checks[self.stage]:
            return result | {"mode": "hold", "message": HOLD_HINTS[self.stage]}
        if active is not None:
            if self.stage == 0 and active["control"] == "angle" and self.scene_hint:
                return result | {"mode": active["mode"], "message": f"请从「{self.scene_hint}」一侧取景，调整拍摄角度"}
            return result | {"mode": active.get("mode", "adjust"), "message": active["message"]}
        if self.stage == 0:
            framing = self.target.framing_hint(person) if self.target else None
            if not self.background.usable:
                msg = "参考照中可辨认的背景太少，可以跳过机位，继续安排人物站位"
            elif framing:
                msg = framing
            elif self.scene_hint:
                msg = "请从「" + self.scene_hint + "」取景，让同一处地标入镜"
            else:
                msg = "先找到参考中的地标，从同一侧取景，让建筑边角或固定景物入镜"
        elif self.stage == 2 and self.target is None and self.reference["outline"]:
            msg = "可照白色轮廓摆动作。参考关节不足，可以跳过姿势后拍摄"
        elif self.reference_box is None or (self.stage == 2 and self.target is None):
            msg = "参考照中的人物特征不足，可点选参考主角，或跳过这一步"
        elif self.pending_tap is not None:
            msg = "点选处还未识别到人物，请让模特清楚入镜或重新点选"
        elif self.stage == 2:
            msg = "请模特照着白色参考姿势入镜，以便识别实时关节"
        else:
            msg = "请模特走进金色目标框，有路人时可点选主角"
        return result | {"mode": "search", "message": msg}

    analyze = synchronous(analyze_async)

