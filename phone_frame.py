"""Crop a phone reference to the visible camera aspect, preserving its main person."""
import base64
import math

import cv2
import numpy as np

from inference import synchronous
from people import choose, center


def crop_phone_reference(frame, aspect, models):
    if not math.isfinite(aspect) or not .3 <= aspect <= 3:
        raise ValueError("手机画幅无效")
    h,w=frame.shape[:2]
    if abs(w/h-aspect)<.005:
        return frame
    boxes=synchronous(models.boxes_async)(frame,survey=True)
    subject=choose(boxes)
    p=center(subject) if subject is not None else np.array([.5,.5])
    cw=min(w,round(h*aspect));ch=min(h,round(w/aspect))
    x=int(np.clip(round(p[0]*(w-cw)),0,w-cw));y=int(np.clip(round(p[1]*(h-ch)),0,h-ch))
    if subject is not None:
        for axis,size,length in ((0,cw,w),(1,ch,h)):
            low=math.ceil(subject[axis+2]*length-size)
            high=math.floor(subject[axis]*length)
            if low<=high:
                value=int(np.clip((x,y)[axis],max(0,low),min(length-size,high)))
                if axis==0:x=value
                else:y=value
    return frame[y:y+ch,x:x+cw].copy()


def reference_preview(frame):
    h,w=frame.shape[:2];scale=min(1,720/max(h,w))
    small=cv2.resize(frame,(max(1,round(w*scale)),max(1,round(h*scale))))
    ok,jpeg=cv2.imencode('.jpg',small,[cv2.IMWRITE_JPEG_QUALITY,84])
    if not ok:raise ValueError("无法生成参考预览")
    return 'data:image/jpeg;base64,'+base64.b64encode(jpeg).decode('ascii')
