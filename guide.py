"""依次对准中心、核对景别、安排站位和姿势；快门核验所有未跳过的步骤。"""
import math
from threading import Lock

from background import Background, CENTER_RELEASE, CENTER_TOLERANCE, SCALE_RELEASE, SCALE_TOLERANCE
from background_shapes import background_outline
from building import reference_building_async
from inference import Prepared, synchronous
from outline import reference_outline_async
from people import EDGES, center, choose
from posture import POSITION_IOU, POSITION_RELEASE_IOU, PoseTarget, measure_position


STAGES = ("background", "framing", "position", "pose", "ready")
GUIDED_STAGES = STAGES[:-1]
HOLD_SECONDS = (.32, .32, .35, .50)
HOLD_HINTS = ("白点已进入中心环，请稳住镜头", "建筑位置和大小已接近参考，请稳住相机",
              "人物已进入目标框，请保持站位", "动作已对齐，请保持片刻")


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
        building = await reference_building_async(frame, boxes, scene_hint)
        self.background.set_subject(building)
        points = self.target.points if self.target else []
        self.reference = {"width": w, "height": h,
                          "centerTolerance": CENTER_TOLERANCE,
                          "aimPoint": [.5, .5],
                          "landmarks": self.background.landmarks.tolist(),
                          "buildingOutline": building,
                          "backgroundOutline": background_outline(frame, boxes),
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
        # 中心只确认一次；之后保留完成状态，不让手持漂移把用户送回第一关。
        self.completed = set()
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
        return next((i for i in range(start, len(GUIDED_STAGES))
                     if STAGES[i] not in self.skipped and STAGES[i] not in self.completed),
                    len(GUIDED_STAGES))

    def steady_background(self, bg, now):
        """景别只检查可靠跟踪、倍率和倾角稳定，已完成的中心不再要求重合。"""
        if self.stage > 1 or not bg or not bg["tracking"] or (self.stage == 0 and not bg["centerOk"]):
            self.aim_sample = None
            return
        x, y = bg["current"]
        zoom, roll = bg["zoom"], bg["roll"]
        if self.aim_sample:
            _, old_x, old_y, old_zoom, old_roll = self.aim_sample
            scale_drift = zoom is not None and old_zoom is not None and abs(math.log(zoom / old_zoom)) > .06
            if scale_drift or abs(roll - old_roll) > 3:
                self.aim_sample = None
        if self.aim_sample is None:
            self.aim_sample = now, x, y, zoom, roll
            # 第一关只确认中心。景别或轻微倾角的测量波动不能重置对准计时；
            # 第二关仍需稳定的原始几何关系，才能自动变焦或确认景别。
            if self.stage == 1:
                self.since = None
        if self.stage == 0 or bg["scaleOk"] or zoom is None:
            return
        elapsed = now - self.aim_sample[0]
        if elapsed >= HOLD_SECONDS[0]:
            bg["zoomFactor"] = round(1 / zoom, 4)
        else:
            bg.update(control="steady", mode="hold", message="中心已对准，稳住镜头后会自动匹配轮廓大小")
            return elapsed / HOLD_SECONDS[0]

    async def analyze_async(self, frame, seq, now, tap=None, skipped=None, source_id=None):
        if seq <= self.last_seq:
            raise ValueError("画面已过期，请使用递增帧号。")
        h, w = frame.shape[:2]
        if abs(w / h / self.aspect - 1) > .018:
            raise ValueError("取景画幅与参考照不同，请按参考照的宽高比取景。")
        if source_id is not None and source_id != self.source_id:
            self.source_id = source_id
            self.completed.clear()
            if hasattr(self.background, "reset_tracking"):
                self.background.reset_tracking()
            self.stage, self.since, self.aim_sample = 0, None, None
            self.last_box, self.last_seen, self.pending_tap, self.pinned = None, None, None, False
        if skipped is not None:
            requested = frozenset(skipped)
            if not requested.issubset(GUIDED_STAGES):
                raise ValueError("可跳过的步骤为对准、景别、站位和姿势。")
            if self.skipped - requested:
                # 恢复的步骤必须从新画面重新确认。
                self.stage, self.since, self.aim_sample = 0, None, None
                if hasattr(self.background, "reset_tracking"):
                    self.background.reset_tracking()
            self.skipped = requested
        if self.last_time is not None and now - self.last_time > getattr(self, "max_sample_gap", 1.6):
            self.stage, self.since, self.aim_sample = 0, None, None
            if hasattr(self.background, "reset_tracking"):
                self.background.reset_tracking()
        self.last_seq, self.last_time = seq, now
        if tap is not None:
            self.pinned = True
            self.pending_tap = tap
            self.last_box, self.since = None, None
            self.stage = min(self.stage, 2)
        if STAGES[self.stage] in self.skipped or STAGES[self.stage] in self.completed:
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
        if not {"background", "framing"}.issubset(self.skipped):
            # 进入容差后，在本次连续确认期间使用稍宽的退出阈值，避免边缘抖动
            # 反复归零；丢失、镜像或移出退出范围会使 since 清空，下帧重新进入。
            center_captured = self.stage > 0 or self.since is not None
            bg = await self.background.measure_async(frame, boxes, CENTER_RELEASE if center_captured else CENTER_TOLERANCE,
                                                     SCALE_RELEASE if self.stage > 1 else SCALE_TOLERANCE)
        min_overlap = POSITION_RELEASE_IOU if self.stage > 2 else POSITION_IOU
        if self.target:
            position, pose = self.target.compare(person, min_overlap)
        else:
            position, pose = measure_position(self.reference_box, box, min_overlap), None
        aim = dict(bg, ok=bg["centerOk"]) if bg else None
        # 第二关独立核验景别，不能偷偷重新加入已通过/已跳过的中心条件。
        framing = dict(bg, ok=bool(bg["tracking"] and bg["scaleOk"])) if bg else None
        values = (aim, framing, position, pose)
        checks = [None if stage in self.skipped else stage in self.completed or value is not None and bool(value["ok"])
                  for stage, value in zip(GUIDED_STAGES, values)]
        earliest = next((i for i, ok in enumerate(checks) if ok is False), 4)
        if earliest < self.stage:
            self.stage, self.since = earliest, None
        fraction = self.steady_background(bg, now) or 0.
        if self.stage < 4 and checks[self.stage]:
            if self.since is None:
                self.since = now
            fraction = min(1., (now - self.since) / HOLD_SECONDS[self.stage])
            if fraction >= 1:
                if self.stage == 0:
                    self.completed.add("background")
                self.stage = self.next_required(self.stage + 1)
                self.since = now if self.stage < 4 and checks[self.stage] else None
                fraction = 0.
        else:
            self.since = None
        result = {"seq": seq, "stage": STAGES[self.stage], "checks": checks,
                  "completed": [stage for stage in GUIDED_STAGES if stage in self.completed],
                  "skipped": [stage for stage in GUIDED_STAGES if stage in self.skipped], "hold": round(fraction, 2),
                  "ready": self.stage == 4, "boxes": [b[:4].tolist() for b in boxes],
                  "selected": box[:4].tolist() if box is not None else None,
                  "background": bg, "position": position if "position" not in self.skipped else None,
                  "pose": pose if self.stage >= 3 and "pose" not in self.skipped else None}
        if self.stage == 4:
            msg = "可以拍摄，保持现在的机位和动作"
            if len(self.skipped) == len(GUIDED_STAGES):
                msg = "已跳过全部引导，可以直接拍摄"
            return result | {"mode": "ready", "message": msg}
        active = values[self.stage]
        building = self.reference["buildingOutline"]
        if self.stage == 0 and checks[0]:
            return result | {"mode": "hold", "message": HOLD_HINTS[0]}
        if self.stage == 1:
            name = building["name"] if building else "地标"
            if not bg or not bg["tracking"]:
                return result | {"mode": "search", "message": f"让{name}保持入镜，等待匹配恢复后继续核对景别"}
            if bg["control"] == "steady":
                return result | {"mode": "hold", "message": "中心步骤已完成，稳住镜头以匹配景别"}
            if bg["zoom"] is None:
                return result | {"mode": "fine", "message": f"保持{name}贴近轮廓，让两侧固定景物同时入镜以核对景别"}
            if checks[1]:
                return result | {"mode": "hold", "message": f"{name}的大小已接近参考，请稳住相机"}
            return result | {"mode": "scale", "message": f"调整倍率，让{name}的大小接近参考轮廓"}
        if checks[self.stage]:
            return result | {"mode": "hold", "message": HOLD_HINTS[self.stage]}
        if active is not None:
            if self.stage < 2 and active["control"] == "angle" and self.scene_hint:
                return result | {"mode": active["mode"], "message": f"请从「{self.scene_hint}」一侧取景，调整拍摄角度"}
            return result | {"mode": active.get("mode", "adjust"), "message": active["message"]}
        if self.stage < 2:
            framing = self.target.framing_hint(person) if self.target else None
            if not self.background.usable:
                msg = "参考照中可辨认的背景太少，可以跳过对准和景别，继续安排人物站位"
            elif framing:
                msg = framing
            elif self.scene_hint:
                msg = "请从「" + self.scene_hint + "」取景，找到地标后将白点移入中心环"
            else:
                msg = "先让参考中的地标入镜，识别后转动镜头，将白点移入中心环"
        elif self.stage == 3 and self.target is None and self.reference["outline"]:
            msg = "可照白色轮廓摆动作。参考关节不足，可以跳过姿势后拍摄"
        elif self.reference_box is None or (self.stage == 3 and self.target is None):
            msg = "参考照中的人物特征不足，可点选参考主角，或跳过这一步"
        elif self.pending_tap is not None:
            msg = "点选处还未识别到人物，请让模特清楚入镜或重新点选"
        elif self.stage == 3:
            msg = "请模特照着白色参考姿势入镜，以便识别实时关节"
        else:
            msg = "请模特走进红色目标框，有路人时可点选主角"
        return result | {"mode": "search", "message": msg}

    analyze = synchronous(analyze_async)

