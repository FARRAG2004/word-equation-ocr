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
# Cross-check: both models read every image (~30% slower, more accurate); 0 = second model only as fallback.
CROSSCHECK = os.environ.get("EQ_CROSSCHECK", "1") != "0"
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
        import types
        import torch
        import pix2tex.cli
        from pix2tex.cli import LatexOCR
        # pix2tex copies every reading to the Windows clipboard, overwriting what the user copied
        # and flooding the clipboard history. Never touch the user's clipboard.
        pix2tex.cli.clipboard = types.SimpleNamespace(copy=lambda *args, **kwargs: None)
        if os.environ.get("EQ_TORCH_THREADS"):
            torch.set_num_threads(int(os.environ["EQ_TORCH_THREADS"]))
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


def best_reading(rgb, good_enough=lambda latex: False):
    """OCR at a few scales; keep the reading the model is most confident about.
    Stops early when confident, or when good_enough(latex) (e.g. it matches the other model)."""
    best = ("", -1.0)
    for h in (None, 48, 64):
        im = rgb if h is None else ImageOps.expand(
            rgb.resize((max(8, round(rgb.width * h / rgb.height)), h), Image.LANCZOS), 8, "white")
        latex, conf = read(im)
        if conf > best[1]:
            best = (latex, conf)
        if best[1] >= 0.99 or good_enough(latex):
            break
    return best


_pool = None


def second_in_background(rgb):
    """Start pix2text-mfr (CPU) now, so it runs while pix2tex uses the GPU."""
    global _pool
    import second
    from concurrent.futures import ThreadPoolExecutor
    if _pool is None:
        _pool = ThreadPoolExecutor(max_workers=1)
    return _pool.submit(second.read, rgb)


FUNCS = "lim|det|sin|cos|tan|cot|sec|csc|sinh|cosh|tanh|ln|log|exp|arctan|arcsin|arccos|max|min|sup|inf|deg|dim|ker|gcd"
# Tokenised models write "\operatorname { s i n }", "\cfrac", "\root 3 \of {27}", "\underset{x}{\lim}".
G = r"(?:[^{}]|\{(?:[^{}]|\{[^{}]*\})*\})*"   # brace group content, two levels deep
BIG = r"sum|prod|int|oint|iint|iiint|bigcup|bigcap"
TIDY = [
    (r"\\protect\b\s*", ""),
    (r"\\!", ""),                                   # negative space becomes an empty box in Word
    (r"\\\\(?=[-+])", r"\\\\ "),                    # latex2mathml misreads a row break followed by a sign
    (r"\\(operatorname\*?|mathrm)\s*\{\s*((?:[A-Za-z]\s*)+)\}", lambda m: "\\" + m.group(1) + "{" + re.sub(r"\s+", "", m.group(2)) + "}"),
    # keep a space after the name: "\operatorname{sin}x" must become "\sin x", not "\sinx"
    (rf"\\(?:operatorname\*?|mathrm|mathsf|textsf|textrm|text)\{{\s*({FUNCS})\s*(?:\\,)?\s*\}}\s*(?=(.?))",
     lambda m: "\\" + m.group(1) + (" " if m.group(2).isalpha() else "")),
    # other operator names (rank, adj, sgn, Tr, ...) are upright text with a thin space, not italic variables
    (r"\\operatorname\*?\{([A-Za-z]+)\}\s*", r"\\mathrm{\1}\\,"),
    # limits written as \underset/\overset around \sum: Word needs a real n-ary operator
    (rf"\\underset\{{({G})\}}\{{\\overset\{{({G})\}}\{{?\\({BIG})\}}?\}}", r"\\\3_{\1}^{\2}"),
    (rf"\\overset\{{({G})\}}\{{\\underset\{{({G})\}}\{{?\\({BIG})\}}?\}}", r"\\\3_{\2}^{\1}"),
    (rf"\\underset\{{({G})\}}\{{?\\({BIG})\}}?", r"\\\2_{\1}"),
    (r"\\mathop\s*=", "="),
    # Feynman slash: neither model knows \not, both write "A/" with nothing to divide by -> A with a slash through it
    (r"(\\[A-Za-z]+|[A-Za-z])/(?=\s*(?:[)\]},;=+\-|]|\\right|\\\}|$))", r"\\not{\1}"),
    (r"([_^])\{\\ +", r"\1{"),                      # "^{\ 2}": a leading space inside a script
    (r"(_\{[^{}]*\})\{\}\^", r"\1^"),                # "\partial_{5}{}^{2}" -> "\partial_{5}^{2}"
    (r"[_^]\{\s*(?:\\mathrm\{\s*\})?\s*\}", ""),     # empty sub/superscripts
    (r"\\mathbf\s*\{?\s*\\nabla\s*\}?", r"\\nabla"),
    (r"\\(mathbf|mathrm|mathcal|mathit|boldsymbol)\s+([A-Za-z0-9])", r"\\\1{\2}"),
    (r"\{\\(?:it|rm)\s+([^{}]*)\}", r"\1"),
    # both models misread "%" after a number as one of these meaningless sequences (benchmark: 7 of 7)
    (r"(\d)\s*(?:\\mathcal\{[YV]\}(?:_\{?0\}?)?|\\lnot\s*_\{?0\}?|\\rangle_\{\\mathrm\{c\}\})\s*$", r"\1\\%"),
    (r"\\cfrac", r"\\frac"),
    (r"\\root\s*(\S+)\s*\\of\s*\{", r"\\sqrt[\1]{"),
    (r"\\underset\s*\{((?:[^{}]|\{[^{}]*\})*)\}\s*\{\s*\\lim\s*\}", r"\\lim_{\1}"),
    (r"\\mathrm\s*\{\s*~\s*((?:[A-Za-z]\s*)+)\}", lambda m: r"\text{ " + re.sub(r"\s+", "", m.group(1)) + "}"),
    (r"\\left\\Vert|\\right\\Vert", r"\\|"),
    (r"\\overset\s*\{(?:\s|\\ )*\}\s*", ""),     # empty accents around matrices
    (r"_\s*\{(?:\s|\\ |_|\{|\})*\}\s*$", ""),
]
JUNK = re.compile(r"\\(?:newcommand|renewcommand|def|unitlength|begin\{picture\}|put|usepackage)\b")


def compact(latex):
    """Drop the spaces tokenised models put between every token. Math mode ignores them, but
    latex2mathml does not: "\\mathbf { F }" loses its bold. Keep a space only where it ends a
    command before a letter ("\\cdot d"), and leave \\text{...} untouched."""
    out, last = [], ""
    for text, tok in re.findall(r"(\\text\s*\{[^{}]*\})|(\\[A-Za-z]+\*?|\\.|\S)", latex):
        tok = text or tok
        if re.fullmatch(r"\\[A-Za-z]+\*?", last) and re.match(r"[A-Za-z]", tok):
            out.append(" ")
        out.append(tok)
        last = tok
    return "".join(out)


def tidy(latex):
    latex = compact(latex)
    for pat, rep in TIDY:
        latex = re.sub(pat, rep, latex)
    # a single line wrapped in \begin{aligned} ... \end{aligned} is just that line
    m = re.fullmatch(r"\s*\\begin\{aligned\}\s*(.*?)\s*(?:\\\\)?\s*\\end\{aligned\}\s*", latex, re.S)
    if m and "\\\\" not in m.group(1):
        latex = m.group(1).replace("&", "")
    # latex2mathml emits the "&" of an aligned environment unescaped (invalid XML): keep the rows, drop the alignment marks
    latex = re.sub(r"\\begin\{aligned\}(.*?)\\end\{aligned\}",
                   lambda m: r"\begin{array}{l}" + m.group(1).replace("&", "") + r"\end{array}", latex, flags=re.S)
    # a one-cell matrix (no "&", no row break) is just its content
    latex = re.sub(r"\\begin\{matrix\}(.*?)\\end\{matrix\}",
                   lambda m: m.group(0) if "&" in m.group(1) or "\\\\" in m.group(1).strip().removesuffix("\\\\")
                   else m.group(1).strip().removesuffix("\\\\"), latex, flags=re.S)
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


class Unexpressible(ValueError):
    """The equation cannot be shown correctly in Word; the image must stay and be flagged."""


def latex_to_omml(latex):
    import omml_fix
    mml = etree.fromstring(latex_to_mathml(latex).encode())
    root = omml_fix.fix(fill_empty_nary(xslt()(mml).getroot()))
    issues = omml_fix.problems(root)
    if issues:
        raise Unexpressible("; ".join(dict.fromkeys(issues)))
    return etree.tostring(root, encoding="unicode")


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
    other = second_in_background(rgb) if USE_SECOND and CROSSCHECK else None
    if other is not None:
        from compare import canon

        def same_as_other(lx):  # the second model agrees: no need for pix2tex's extra scales
            return canon(tidy(lx)) == canon(tidy(other.result()[0]))
        raw1, conf = best_reading(rgb, same_as_other)
    else:
        raw1, conf = best_reading(rgb)
    latex, conf = tidy(raw1), round(conf, 3)
    errors = []
    omml = try_omml(latex, errors)
    engine = "pix2tex"
    if other is not None:
        status, reason, latex, conf, omml, engine = cross_check(other.result(), latex, conf, omml, entry, errors)
    elif omml and conf >= THRESHOLD:
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


def cross_check(second_reading, latex1, conf1, omml1, entry, errors):
    """Both models read every image. When they agree the reading has been right every time on the
    benchmarks; when they disagree, pix2text-mfr is right far more often, so it wins if confident."""
    from compare import canon
    raw2, conf2 = second_reading
    latex2, conf2 = tidy(raw2), round(conf2, 3)
    omml2 = "" if JUNK.search(raw2) else try_omml(latex2, errors)
    entry["candidates"] = {"pix2tex": [latex1, conf1], "pix2text-mfr": [latex2, conf2]}
    if not omml1 and not omml2:
        return "flagged", "cannot be shown correctly as a Word equation: " + "; ".join(dict.fromkeys(errors) or ["no usable reading"]), \
            latex2, max(conf1, conf2), "", "pix2text-mfr"
    if omml1 and omml2 and canon(latex1) == canon(latex2):
        return "converted", "both models agree", latex1, max(conf1, conf2), omml1, "both"
    if omml2 and conf2 >= SECOND_THRESHOLD:
        return "converted", f"models disagree; pix2text-mfr confident ({conf2})", latex2, conf2, omml2, "pix2text-mfr"
    if omml1 and conf1 >= THRESHOLD:
        return "converted", f"models disagree; pix2tex confident ({conf1})", latex1, conf1, omml1, "pix2tex"
    return "flagged", f"models disagree and neither is confident (pix2tex {conf1}, pix2text-mfr {conf2})", \
        latex2 if omml2 else latex1, max(conf1, conf2), omml2 or omml1, "pix2text-mfr" if omml2 else "pix2tex"


def try_omml(latex, errors=None):
    try:
        return latex_to_omml(latex)
    except Exception as e:
        if errors is not None:
            errors.append(str(e) if isinstance(e, Unexpressible) else f"LaTeX not convertible ({type(e).__name__})")
        return ""
