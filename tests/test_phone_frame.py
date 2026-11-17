import io
import base64
from pathlib import Path
import sys

import numpy as np
from PIL import Image
import pytest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from phone_frame import crop_phone_reference, reference_preview


class Detector:
    async def boxes_async(self,frame,**kwargs):
        return [np.array([.08,.2,.35,.9,.99])]


def test_phone_crop_preserves_person_and_matches_visible_aspect():
    frame=np.zeros((1000,1000,3),dtype=np.uint8)
    frame[200:900,80:350]=255
    crop=crop_phone_reference(frame,390/844,Detector())
    assert crop.shape[:2]==(1000,462)
    assert np.count_nonzero(crop)==np.count_nonzero(frame)
    with Image.open(io.BytesIO(base64.b64decode(reference_preview(crop).split(',')[1]))) as image:
        assert max(image.size)<=720
        assert abs(image.width/image.height-390/844)<.002


@pytest.mark.parametrize('aspect',[0,float('nan'),float('inf'),4])
def test_invalid_aspect_rejected(aspect):
    with pytest.raises(ValueError):crop_phone_reference(np.zeros((100,100,3),np.uint8),aspect,Detector())
