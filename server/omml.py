"""LaTeX -> MathML (latex2mathml) -> OMML (Microsoft's MML2OMML.XSL) -> repaired native Word objects.

latex_to_omml() refuses (Unexpressible) anything it cannot show correctly; the image is then flagged.
"""
import glob
import os

from lxml import etree
from latex2mathml.converter import convert as latex_to_mathml

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


def xslt():
    global _xslt
    if _xslt is None:
        _xslt = etree.XSLT(etree.parse(find_xsl()))
    return _xslt


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
    issues = omml_fix.problems(root, latex)
    if issues:
        raise Unexpressible("; ".join(dict.fromkeys(issues)))
    return etree.tostring(root, encoding="unicode")


def try_omml(latex, errors=None):
    try:
        return latex_to_omml(latex)
    except Exception as e:
        if errors is not None:
            errors.append(str(e) if isinstance(e, Unexpressible) else f"LaTeX not convertible ({type(e).__name__})")
        return ""
