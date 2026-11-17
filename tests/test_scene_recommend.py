"""Run using the teammate recommendation environment and PYTHONPATH."""
import base64
import io
from pathlib import Path
import sys

import numpy as np
from PIL import Image
import pytest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
pytest.importorskip('photo_recommend', reason='Run in the separate recommendation environment with its PYTHONPATH')
from scene_recommend import rank_scene, decode_scene, Request


def test_relevance_gate_then_aesthetic_order():
    scene=np.array([1.,0.,0.]);text=np.array([0.,1.,0.])
    candidates=[]
    for i,(similarity,style,aesthetic) in enumerate([(.8,.3,6),(.78,.35,8),(.73,.4,7),(-.6,.1,10)]):
        vector=np.array([similarity,style,np.sqrt(1-similarity**2-style**2)])
        candidates.append(({'photo_id':str(i)},vector,aesthetic))
    rows=rank_scene(candidates,scene,text,5)
    assert rows[0]['photo_id']=='1'
    assert '3' not in [row['photo_id'] for row in rows],'Unrelated high-aesthetic image entered the shortlist'
    assert [row['aesthetic_score'] for row in rows]==sorted([row['aesthetic_score'] for row in rows],reverse=True)


def test_scene_changes_the_shortlist():
    candidates=[({'photo_id':str(i)},np.eye(7)[i],7+i/10) for i in range(7)]
    assert rank_scene(candidates,np.eye(7)[0],None,5)[0]['photo_id']=='0'
    assert rank_scene(candidates,np.eye(7)[6],None,5)[0]['photo_id']=='6'


def test_five_results_and_duplicate_removal():
    vectors=np.eye(6)
    candidates=[({'photo_id':str(i)},vector,9-i/10) for i,vector in enumerate(vectors)]
    candidates.insert(1,({'photo_id':'duplicate'},vectors[0],8.95))
    rows=rank_scene(candidates,None,None,5)
    assert len(rows)==5 and 'duplicate' not in [row['photo_id'] for row in rows]
    assert Request(query='校园',limit=5).limit==5


def test_scene_decoder_limits_and_invalid_payload():
    buf=io.BytesIO();Image.new('RGB',(1080,1920)).save(buf,'JPEG')
    assert max(decode_scene(base64.b64encode(buf.getvalue()).decode()).size)==768
    for value in ['bad!',base64.b64encode(b'not an image').decode()]:
        with pytest.raises(ValueError):decode_scene(value)
