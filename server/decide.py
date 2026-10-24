"""Choose one reading from several OCR engines, or flag the image for review.

Rules, in order (tuned on the benchmarks; tests/run_all.py checks they keep their scores):
1. Nothing convertible                      -> flag, with the reason.
2. A strict majority of engines agree        -> accept (300 agreements on 460 images, 3 wrong).
3. They differ only in an operator condition -> flag (a multi-line condition was half-read).
4. Otherwise the most trusted engine (config order) whose confidence reaches its threshold wins.
5. Otherwise flag.
"""
from collections import namedtuple

from compare import canon
from latex_cleanup import without_limits

Reading = namedtuple("Reading", "engine latex confidence omml error")


def decide(readings, thresholds):
    """readings: list of Reading in order of trust. Returns (status, reason, chosen Reading)."""
    usable = [r for r in readings if r.omml]
    if not usable:
        errors = [r.error for r in readings if r.error] or ["no usable reading"]
        best = max(readings, key=lambda r: r.confidence)
        return "flagged", "cannot be shown correctly as a Word equation: " + "; ".join(dict.fromkeys(errors)), best

    groups = {}
    for r in usable:
        groups.setdefault(canon(r.latex), []).append(r)
    largest = max(groups.values(), key=len)
    if len(largest) >= 2 and len(largest) * 2 > len(readings):
        names = [r.engine for r in largest]
        reason = "all models agree" if len(largest) == len(readings) else f"{len(largest)} of {len(readings)} models agree ({', '.join(names)})"
        return "converted", reason, max(largest, key=lambda r: r.confidence)

    if len(usable) >= 2 and len({canon(without_limits(r.latex)) for r in usable}) == 1:
        return "flagged", "the models read the condition under an operator differently (it may have several lines)", usable[0]

    for r in usable:
        if r.confidence >= thresholds.get(r.engine, 1.0):
            return "converted", f"models disagree; {r.engine} confident ({r.confidence})", r
    summary = ", ".join(f"{r.engine} {r.confidence}" for r in readings)
    return "flagged", f"models disagree and none is confident enough ({summary})", usable[0]
