"""The desktop entry points are synchronous. The device worker awaits the same pipeline."""
import inspect
import sys
from functools import wraps


if sys.platform == "emscripten":
    import browser_ort as ort

    async def infer(session, names, feeds):
        return await session.run(names, feeds)
else:
    import onnxruntime as ort

    async def infer(session, names, feeds):
        return session.run(names, feeds)


def synchronous(function):
    @wraps(function)
    def call(*args, **kwargs):
        # Native ONNX returns immediately to Python, so the entire coroutine runs
        # without suspending, including when a caller supplies a native mask callback.
        coroutine = function(*args, **kwargs)
        try:
            coroutine.send(None)
        except StopIteration as done:
            return done.value
        finally:
            coroutine.close()
        raise RuntimeError("Use the asynchronous entry point for a web inference session.")
    return call


class Prepared:
    """Reference preparation includes model calls, which are asynchronous on the web."""
    @classmethod
    async def create(cls, *args, **kwargs):
        obj = cls.__new__(cls)
        await obj.prepare(*args, **kwargs)
        return obj


async def foreground_mask(segmenter, image):
    mask = segmenter(image)
    return await mask if inspect.isawaitable(mask) else mask
