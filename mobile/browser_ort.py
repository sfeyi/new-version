"""Share the native guide's model inputs and outputs with ONNX Runtime Web."""
import json
from pathlib import Path
from types import SimpleNamespace

import numpy as np
from js import runGuideModel


class SessionOptions:
    def add_session_config_entry(self, key, value):
        pass  # Native thread settings are configured separately in the web worker.


class InferenceSession:
    def __init__(self, path, options, providers):
        self.name = Path(path).name

    def get_inputs(self):
        # Each guide model has one input. The worker looks up its actual ONNX name.
        return [SimpleNamespace(name="input")]

    async def run(self, names, feeds):
        tensor = np.ascontiguousarray(next(iter(feeds.values())), dtype=np.float32)
        outputs = await runGuideModel(self.name, tensor, json.dumps(tensor.shape), json.dumps(names))
        return [np.frombuffer(out.data.to_py(), dtype=str(out.dtype)).reshape(out.dims.to_py())
                for out in outputs]
