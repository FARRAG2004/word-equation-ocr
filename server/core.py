"""Image -> LaTeX -> MathML -> OMML pipeline, verified by the OCR model's own token confidence."""
import glob
import io
import json
import math
import os
import time

import numpy as np
from PIL import Image, ImageOps
from lxml import etree
from latex2mathml.converter import convert as latex_to_mathml

# Geometric-mean token probability needed to replace an image without review.
# Tuned with bench.py on the 100-equation benchmark: 0.99 converts 77 with 2 wrong (97% precision);
# 0.97 converts 88 with 9 wrong. A wrong silent replacement costs more than a flag.
THRESHOLD = float(os.environ.get("EQ_THRESHOLD", "0.99"))
LOG_PATH = os.environ.get("EQ_LOG", os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "ocr_log.jsonl"))

_model = None
_xslt = None
_step_probs = []  # top-token probability of each decoding step, filled by a forward hook


def find_xsl():
    """MML2OMML.XSL ships with Office; it is not redistributed here."""
    if os.environ.get("MML2OMML"):
        return os.environ["MML2OMML"]
    for base in (os.environ.get("ProgramFiles", r"C:\Program Files"),
                 os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")):
        hits = sorted(glob.glob(os.path.join(base, "Microsoft Office", "**", "MML2OMML.XSL"), recursive=True))
        if hits:
            return hits[-1]
    raise FileNotFoundError("MML2OMML.XSL not found: is Microsoft Office installed?")


def model():
    global _model
    if _model is None:
        import torch
        from pix2tex.cli import LatexOCR
        _model = LatexOCR()
        _model.args.temperature = 1e-8  # greedy decoding: same image -> same LaTeX
        # With greedy decoding the chosen token is the arg-max, so its probability is the max softmax.
        _model.model.decoder.net.register_forward_hook(
            lambda mod, inp, out: _step_probs.append(torch.softmax(out[:, -1, :].float(), -1).max().item()))
    return _model


def xslt():
    global _xslt
    if _xslt is None:
        _xslt = etree.XSLT(etree.parse(find_xsl()))
    return _xslt


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


def read(im):
    """OCR one image; return (latex, geometric-mean token probability)."""
    _step_probs.clear()
    latex = model()(im).replace(r"\!", "")  # negative space becomes an empty box in Word
    probs = _step_probs[:] or [0.0]
    return latex, math.exp(sum(math.log(max(p, 1e-9)) for p in probs) / len(probs))


def best_reading(rgb):
    """OCR at a few scales; keep the reading the model is most confident about."""
    best = ("", -1.0)
    for h in (None, 48, 64):
        im = rgb if h is None else ImageOps.expand(
            rgb.resize((max(8, round(rgb.width * h / rgb.height)), h), Image.LANCZOS), 8, "white")
        latex, conf = read(im)
        if conf > best[1]:
            best = (latex, conf)
        if best[1] >= 0.99:
            break
    return best


M = "{http://schemas.openxmlformats.org/officeDocument/2006/math}"
RELATIONS = "=<>≤≥≠≈≡"


def fill_empty_nary(root):
    """MML2OMML can leave an integral's body empty; pull the integrand in, up to the first relation."""
    for nary in root.iter(M + "nary"):
        body = nary.find(M + "e")
        if body is None or len(body):
            continue
        while nary.getnext() is not None:
            nxt = nary.getnext()
            t = nxt.find(M + "t") if nxt.tag == M + "r" else None
            if t is not None and t.text and any(c in t.text for c in RELATIONS):
                cut = min(t.text.find(c) for c in RELATIONS if c in t.text)
                if cut:  # e.g. "dx=": move "dx" into the body, keep "=" outside
                    head = etree.fromstring(etree.tostring(nxt))
                    head.find(M + "t").text = t.text[:cut]
                    body.append(head)
                    t.text = t.text[cut:]
                break
            body.append(nxt)
    return root


def latex_to_omml(latex):
    mml = etree.fromstring(latex_to_mathml(latex).encode())
    return etree.tostring(fill_empty_nary(xslt()(mml).getroot()), encoding="unicode")


def log(entry):
    try:
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError:
        pass  # logging must never break a conversion


def process(image_bytes, verify=True, source=""):
    """Return dict(status=converted|flagged|skipped, reason, latex, confidence, omml)."""
    entry = {"time": time.strftime("%Y-%m-%d %H:%M:%S"), "source": source, "verify": verify}
    try:
        img = Image.open(io.BytesIO(image_bytes))
        img.load()
    except Exception as e:
        r = {"status": "skipped", "reason": f"unreadable image: {e}", "latex": "", "confidence": 0.0, "omml": ""}
        log({**entry, **{k: v for k, v in r.items() if k != "omml"}})
        return r
    entry["prefilter"] = prefilter_stats(to_gray(img))
    latex, conf = best_reading(img.convert("RGB"))
    conf = round(conf, 3)
    try:
        omml = latex_to_omml(latex)
        if not verify:
            status, reason = "converted", "verification disabled"
        elif conf >= THRESHOLD:
            status, reason = "converted", f"token confidence {conf} >= {THRESHOLD}"
        else:
            status, reason = "flagged", f"token confidence {conf} < {THRESHOLD}"
    except Exception as e:
        omml, status, reason = "", "flagged", f"LaTeX could not be converted to a Word equation: {e}"
    r = {"status": status, "reason": reason, "latex": latex, "confidence": conf, "omml": omml}
    log({**entry, **{k: v for k, v in r.items() if k != "omml"}})
    return r
