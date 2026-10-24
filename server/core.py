"""Orchestrator: image -> every configured OCR engine (in parallel) -> cleanup -> Word equation -> decision.

Pieces, each replaceable on its own:
    engines/          one module per OCR model (config.json lists which run, in order of trust)
    latex_cleanup.py  model-quirk and Word-rendering fixes on the LaTeX
    omml.py           LaTeX -> OMML, refusing what cannot be shown correctly
    decide.py         agreement / majority / trust rules
"""
import io
import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import numpy as np
from PIL import Image

import engines
import settings
from compare import canon
from decide import Reading, decide
from latex_cleanup import JUNK, tidy, without_limits  # noqa: F401  (re-exported for scripts and tests)
from omml import Unexpressible, latex_to_omml, try_omml  # noqa: F401

LOG_PATH = settings.LOG_PATH
THRESHOLDS = dict(settings.ENGINES)
_pools = {}
_warm_lock = threading.Lock()
_warmed = False


def warm():
    """Load every configured engine, one after another. Loading them from parallel threads races on
    shared imports (two engines importing transformers at once fails), so this runs before any reading."""
    global _warmed
    with _warm_lock:
        if not _warmed:
            for name, _ in settings.ENGINES:
                engines.get(name).load()
            _warmed = True


def to_gray(img):
    """Flatten transparency onto white and return a float array in [0, 1]."""
    img = img.convert("RGBA")
    bg = Image.new("RGBA", img.size, "white")
    return np.asarray(Image.alpha_composite(bg, img).convert("L"), dtype=np.float32) / 255


def prefilter_stats(gray):
    """Old "is this an equation?" heuristic. Disabled: only logged, to rebuild it from real data."""
    h, w = gray.shape
    ink = float((gray < 0.5).mean())
    mid = float(((gray > 0.15) & (gray < 0.85)).mean())  # lots of mid-tones => photo/diagram
    white = float((gray > 0.9).mean())
    reasons = []
    if h < 8 or w < 8:
        reasons.append("too small")
    if h > 600:
        reasons.append("taller than 600px")
    if not 0.005 < ink < 0.35:
        reasons.append(f"ink {ink:.3f} outside 0.005-0.35")
    if mid >= 0.15:
        reasons.append(f"mid-tones {mid:.3f} >= 0.15")
    if white <= 0.5:
        reasons.append(f"white {white:.3f} <= 0.5")
    return {"w": w, "h": h, "ink": round(ink, 4), "mid": round(mid, 4), "white": round(white, 4),
            "would_skip": bool(reasons), "would_skip_reasons": reasons}


def pool(name):
    if name not in _pools:  # one thread per engine: a model is never used by two threads at once
        _pools[name] = ThreadPoolExecutor(max_workers=1)
    return _pools[name]


def read_all(rgb):
    """Every engine reads the image at the same time (GPU and CPU engines overlap).
    Multi-pass engines stop early once another engine already agrees with them."""
    warm()
    names = [n for n, _ in settings.ENGINES]
    single = {n: pool(n).submit(engines.get(n).read, rgb) for n in names if not getattr(engines.get(n), "MULTI_PASS", False)}

    def agrees(latex):
        target = canon(tidy(latex))
        return any(canon(tidy(f.result()[0])) == target for f in single.values())

    multi = {n: pool(n).submit(engines.get(n).read, rgb, agrees) for n in names if n not in single}
    return {n: (single.get(n) or multi[n]).result() for n in names}


def reading(name, raw, conf):
    latex, errors = tidy(raw), []
    omml = "" if JUNK.search(raw) else try_omml(latex, errors)
    if JUNK.search(raw):
        errors.append(f"{name} output is not an equation")
    return Reading(name, latex, round(conf, 3), omml, errors[0] if errors else "")


def process(image_bytes, verify=True, source=""):
    """Return dict(status=converted|flagged|skipped, reason, latex, confidence, omml, engine)."""
    entry = {"time": time.strftime("%Y-%m-%d %H:%M:%S"), "source": source, "verify": verify}
    try:
        img = Image.open(io.BytesIO(image_bytes))
        img.load()
    except Exception as e:
        r = {"status": "skipped", "reason": f"unreadable image: {e}", "latex": "", "confidence": 0.0, "omml": "", "engine": ""}
        log({**entry, **{k: v for k, v in r.items() if k != "omml"}})
        return r
    entry["prefilter"] = prefilter_stats(to_gray(img))
    rgb = img.convert("RGB")

    if settings.CROSSCHECK:
        readings = [reading(n, *raw) for n, raw in read_all(rgb).items()]
        status, reason, chosen = decide(readings, THRESHOLDS)
    else:  # fast mode: engines in order, stop at the first confident convertible reading
        readings, status = [], None
        for name, threshold in settings.ENGINES:
            readings.append(reading(name, *engines.get(name).read(rgb)))
            if readings[-1].omml and readings[-1].confidence >= threshold:
                status, reason, chosen = "converted", f"{name} confidence {readings[-1].confidence} >= {threshold}", readings[-1]
                break
        if status is None:
            status, reason, chosen = decide(readings, {})
    entry["candidates"] = {r.engine: [r.latex, r.confidence] for r in readings}
    if not verify and chosen.omml and status == "flagged":
        status, reason = "converted", f"verification disabled ({chosen.engine})"
    r = {"status": status, "reason": reason, "latex": chosen.latex, "confidence": chosen.confidence,
         "omml": chosen.omml, "engine": chosen.engine}
    log({**entry, **{k: v for k, v in r.items() if k != "omml"}})
    return r


def log(entry):
    try:
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError:
        pass  # logging must never break a conversion
