"""Image -> LaTeX -> MathML -> OMML pipeline with render-back verification."""
import glob
import io
import os

import numpy as np
from PIL import Image
from lxml import etree
from latex2mathml.converter import convert as latex_to_mathml

THRESHOLD = float(os.environ.get("EQ_THRESHOLD", "0.75"))

_model = None
_xslt = None


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
        from pix2tex.cli import LatexOCR
        _model = LatexOCR()
        _model.args.temperature = 1e-8  # greedy decoding: same image -> same LaTeX
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


def looks_like_equation(gray):
    """Cheap filter: mostly white background, dark ink, wide-ish, not photo-like."""
    h, w = gray.shape
    if h < 8 or w < 8 or h > 600:
        return False
    ink = (gray < 0.5).mean()
    mid = ((gray > 0.15) & (gray < 0.85)).mean()  # lots of mid-tones => photo/diagram
    return 0.005 < ink < 0.35 and mid < 0.15 and (gray > 0.9).mean() > 0.5


def crop_ink(gray):
    ys, xs = np.where(gray < 0.5)
    if len(ys) == 0:
        return gray
    return gray[ys.min():ys.max() + 1, xs.min():xs.max() + 1]


def render(latex):
    import matplotlib
    matplotlib.use("Agg")
    matplotlib.rcParams["mathtext.fontset"] = "cm"
    import matplotlib.pyplot as plt
    fig = plt.figure(figsize=(0.01, 0.01))
    fig.text(0, 0, f"${latex}$", fontsize=24)
    buf = io.BytesIO()
    fig.savefig(buf, dpi=150, bbox_inches="tight", pad_inches=0.05, facecolor="white")
    plt.close(fig)
    buf.seek(0)
    return to_gray(Image.open(buf))


def similarity(a, b):
    """Compare ink silhouettes after cropping and resizing to a common height."""
    from skimage.metrics import structural_similarity
    a, b = crop_ink(a), crop_ink(b)
    h = 64
    def fit(x, w):
        im = Image.fromarray((x * 255).astype(np.uint8))
        return np.asarray(im.resize((w, h), Image.LANCZOS), dtype=np.float32) / 255
    wa = max(8, round(a.shape[1] * h / a.shape[0]))
    wb = max(8, round(b.shape[1] * h / b.shape[0]))
    ratio = min(wa, wb) / max(wa, wb)  # penalise very different widths
    w = (wa + wb) // 2
    return float(structural_similarity(fit(a, w), fit(b, w), data_range=1.0)) * ratio


def best_reading(rgb, gray):
    """OCR at a few scales; keep the LaTeX whose render-back matches best."""
    from PIL import ImageOps
    best = ("", 0.0)
    for h in (None, 48, 64):
        im = rgb if h is None else ImageOps.expand(
            rgb.resize((max(8, round(rgb.width * h / rgb.height)), h), Image.LANCZOS), 8, "white")
        latex = model()(im).replace(r"\!", "")  # negative space becomes an empty box in Word
        try:
            conf = similarity(gray, render(latex))
        except Exception:
            conf = 0.0  # mathtext can't render it
        if conf > best[1] or not best[0]:
            best = (latex, conf)
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


def process(image_bytes):
    """Return dict(status=converted|skipped|flagged, latex, confidence, omml)."""
    img = Image.open(io.BytesIO(image_bytes))
    gray = to_gray(img)
    if not looks_like_equation(gray):
        return {"status": "skipped", "latex": "", "confidence": 0.0, "omml": ""}
    latex, conf = best_reading(img.convert("RGB"), gray)
    try:
        omml = latex_to_omml(latex)
    except Exception:
        return {"status": "flagged", "latex": latex, "confidence": conf, "omml": ""}
    status = "converted" if conf >= THRESHOLD else "flagged"
    return {"status": status, "latex": latex, "confidence": round(conf, 3), "omml": omml}
