"""Image -> LaTeX -> MathML -> OMML pipeline.

Stage 1: pix2tex, accepted when its own token confidence >= THRESHOLD.
Stage 2: images pix2tex is unsure about go to a second model (second.py, pix2text-mfr-1.5),
accepted when its confidence >= SECOND_THRESHOLD; otherwise the image is flagged for review.
"""
import glob
import io
import json
import math
import os
import re
import time

import numpy as np
from PIL import Image, ImageOps
from lxml import etree
from latex2mathml.converter import convert as latex_to_mathml

# Geometric-mean token probability needed to replace an image without review.
# Tuned with bench.py on the 100-equation benchmark: 0.99 converts 77 with 2 wrong (97% precision);
# 0.97 converts 88 with 9 wrong. A wrong silent replacement costs more than a flag.
THRESHOLD = float(os.environ.get("EQ_THRESHOLD", "0.99"))
# Second model: every correct benchmark reading scored >= 0.954; non-equation images
# (photo, diagram, text line) scored 0.30-0.94, so they are flagged instead of converted.
SECOND_THRESHOLD = float(os.environ.get("EQ_SECOND_THRESHOLD", "0.95"))
USE_SECOND = os.environ.get("EQ_SECOND", "1") != "0"
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


FUNCS = "lim|det|sin|cos|tan|cot|sec|csc|sinh|cosh|tanh|ln|log|exp|arctan|arcsin|arccos|max|min|sup|inf|deg|dim|ker|gcd"
# Tokenised models write "\operatorname { s i n }", "\cfrac", "\root 3 \of {27}", "\underset{x}{\lim}".
TIDY = [
    (r"\\protect\b\s*", ""),
    (r"\\(operatorname\*?|mathrm)\s*\{\s*((?:[A-Za-z]\s*)+)\}", lambda m: "\\" + m.group(1) + "{" + re.sub(r"\s+", "", m.group(2)) + "}"),
    (rf"\\(?:operatorname\*?|mathrm)\{{({FUNCS})\}}", r"\\\1"),
    (r"\\cfrac", r"\\frac"),
    (r"\\root\s*(\S+)\s*\\of\s*\{", r"\\sqrt[\1]{"),
    (r"\\underset\s*\{((?:[^{}]|\{[^{}]*\})*)\}\s*\{\s*\\lim\s*\}", r"\\lim_{\1}"),
    (r"\\mathrm\s*\{\s*~\s*((?:[A-Za-z]\s*)+)\}", lambda m: r"\text{ " + re.sub(r"\s+", "", m.group(1)) + "}"),
    (r"\\left\\Vert|\\right\\Vert", r"\\|"),
    (r"\\overset\s*\{(?:\s|\\ )*\}\s*", ""),     # empty accents around matrices
    (r"_\s*\{(?:\s|\\ |_|\{|\})*\}\s*$", ""),
]
JUNK = re.compile(r"\\(?:newcommand|renewcommand|def|unitlength|begin\{picture\}|put|usepackage)\b")


def tidy(latex):
    for pat, rep in TIDY:
        latex = re.sub(pat, rep, latex)
    # a single line wrapped in \begin{aligned} ... \end{aligned} is just that line
    m = re.fullmatch(r"\s*\\begin\{aligned\}\s*(.*?)\s*(?:\\\\)?\s*\\end\{aligned\}\s*", latex, re.S)
    if m and "&" not in m.group(1) and "\\\\" not in m.group(1):
        latex = m.group(1)
    latex = latex.strip()
    while latex.startswith("{") and latex.endswith("}") and balanced(latex[1:-1]):  # "{ whole equation }"
        latex = latex[1:-1].strip()
    return latex


def balanced(s):
    depth = 0
    for i, ch in enumerate(s):
        if ch == "{" and (i == 0 or s[i - 1] != "\\"):
            depth += 1
        elif ch == "}" and (i == 0 or s[i - 1] != "\\"):
            depth -= 1
            if depth < 0:
                return False
    return depth == 0


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
    rgb = img.convert("RGB")
    latex, conf = best_reading(rgb)
    latex, conf = tidy(latex), round(conf, 3)
    omml = try_omml(latex)
    engine = "pix2tex"
    if omml and conf >= THRESHOLD:
        status, reason = "converted", f"pix2tex confidence {conf} >= {THRESHOLD}"
    elif USE_SECOND:
        import second
        l2, c2 = second.read(rgb)
        l2, c2 = tidy(l2), round(c2, 3)
        o2 = try_omml(l2)
        entry["pix2tex"] = {"latex": latex, "confidence": conf}
        latex, conf, omml, engine = l2, c2, o2, "pix2text-mfr"
        if not o2:
            status, reason = "flagged", "second model's LaTeX could not be converted to a Word equation"
        elif JUNK.search(l2):
            status, reason = "flagged", "second model output is not an equation"
        elif c2 >= SECOND_THRESHOLD:
            status, reason = "converted", f"pix2tex unsure ({entry['pix2tex']['confidence']}); second model confidence {c2} >= {SECOND_THRESHOLD}"
        else:
            status, reason = "flagged", f"both models unsure (pix2tex {entry['pix2tex']['confidence']}, second {c2} < {SECOND_THRESHOLD})"
    elif not omml:
        status, reason = "flagged", "LaTeX could not be converted to a Word equation"
    else:
        status, reason = "flagged", f"pix2tex confidence {conf} < {THRESHOLD}"
    if not verify and omml and status == "flagged":
        status, reason = "converted", f"verification disabled ({engine})"
    r = {"status": status, "reason": reason, "latex": latex, "confidence": conf, "omml": omml, "engine": engine}
    log({**entry, **{k: v for k, v in r.items() if k != "omml"}})
    return r


def try_omml(latex):
    try:
        return latex_to_omml(latex)
    except Exception:
        return ""
