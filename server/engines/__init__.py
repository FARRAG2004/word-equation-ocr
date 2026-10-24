"""OCR engines. Each module in this package is one model and provides:

    NAME          name used in config.json
    load()        load the model (called once, lazily)
    read(rgb)     -> (latex, confidence in [0, 1])   rgb is a PIL RGB image

Optional: MULTI_PASS = True and read(rgb, good_enough) for engines that try several passes and can
stop early once good_enough(latex) is true (e.g. another engine already agrees).
Adding a model = adding a module here and its name to config.json; nothing else changes.
"""
import importlib

_loaded = {}


def get(name):
    if name not in _loaded:
        _loaded[name] = importlib.import_module(f"engines.{name}")
    return _loaded[name]
