"""Decide whether two LaTeX strings are the same equation (used by the pipeline and bench.py)."""
import re

from lxml import etree
from latex2mathml.converter import convert as latex_to_mathml

import core


# Spellings that render identically; applied before comparison, after core.tidy() (which the
# pipeline itself applies, so tokenised output like "\operatorname { s i n }" is already "\sin").
SPELLINGS = [
    (r"\{\\cal\s*([A-Za-z])\}", r"\\mathcal{\1}"),
    (r"\\cal\s*([A-Za-z])", r"\\mathcal{\1}"),
    (r"\\(?:,|;|:|!|quad|qquad|displaystyle|textstyle)", " "),
    (r"\\left\.|\\right\.", ""),
    (r"\\(?:left|right|bigl|bigr|Bigl|Bigr|big|Big)(?![A-Za-z])", ""),
    (r"\\rightarrow", r"\\to"), (r"\\leq", r"\\le"), (r"\\geq", r"\\ge"), (r"\\neq", r"\\ne"),
    (r"\\dfrac|\\tfrac", r"\\frac"),
    (r"\\cdot\s*\\cdot\s*\\cdot", r"\\cdots"),
    (r"\\mathrm\{~?([A-Za-z ]+)\}", r"\\text{\1}"),
    (r"\\left\\Vert|\\right\\Vert|\\Vert", r"\\|"),
    (r"\\mathbf\s*\{\s*\\nabla\s*\}", r"\\nabla"),
    # a two-row, one-column matrix in parentheses displays as a binomial
    (r"\(\s*\\begin\{matrix\}\s*\{?([^{}&\\]+)\}?\s*\\\\\s*\{?([^{}&\\]+)\}?\s*(?:\\\\)?\s*\\end\{matrix\}\s*\)", r"\\binom{\1}{\2}"),
    (r"\\begin\{pmatrix\}\s*\{?([^{}&\\]+)\}?\s*\\\\\s*\{?([^{}&\\]+)\}?\s*(?:\\\\)?\s*\\end\{pmatrix\}", r"\\binom{\1}{\2}"),
    (r"\{\\partial\s*([A-Za-z])\}\^", r"\\partial \1^"),
    (r"\{\\partial\}", r"\\partial"),
    (r"\{\s*\(((?:[^{}()]|\{[^{}]*\})*)\)\s*\}\s*\^", r"(\1)^"),   # "{(a-b)}^2" displays as "(a-b)^2"
    # aligned systems render like a one-column array; the "&" only sets the alignment point
    (r"\\begin\{aligned\}", r"\\begin{array}{c}"), (r"\\end\{aligned\}", r"\\end{array}"),
    (r"\\\\\s*\\end\{array\}", r"\\end{array}"),
]


def merge_numbers(root):
    """'1 7' tokenises to <mn>1</mn><mn>7</mn>; treat it as <mn>17</mn>."""
    for el in list(root.iter("mn")):
        nxt = el.getnext()
        while nxt is not None and nxt.tag == "mn":
            el.text = (el.text or "") + (nxt.text or "")
            el.getparent().remove(nxt)
            nxt = el.getnext()


def canon(latex):
    """Canonical MathML string: attributes, spacing and redundant mrows removed."""
    s = core.tidy(latex)
    for pat, rep in SPELLINGS:
        s = re.sub(pat, rep, s)
    if r"\begin{array}{c}" in s and "&" in s and r"\begin{matrix}" not in s:
        s = s.replace("&", "").replace("{ { } =", "{=")
    try:
        root = etree.fromstring(latex_to_mathml(s).encode())
    except Exception:
        return "PARSE_ERROR:" + re.sub(r"\s+", "", s)
    for el in list(root.iter()):
        if not isinstance(el.tag, str):
            continue
        el.tag = etree.QName(el).localname
        el.attrib.clear()
        if el.text:
            el.text = el.text.strip()
    for el in list(root.iter("mspace")):
        el.getparent().remove(el)
    for el in list(root.iter("mtext")):
        if not (el.text or "").strip():
            el.getparent().remove(el)
    merge_numbers(root)
    changed = True
    while changed:  # unwrap <mrow> that has a single child or sits in a container that is already a row
        changed = False
        for el in list(root.iter("mrow")):
            parent = el.getparent()
            if parent is not None and (len(el) == 1 or parent.tag in ("mrow", "math", "mtd", "msqrt", "mstyle")):
                i = parent.index(el)
                for child in reversed(list(el)):
                    parent.insert(i, child)
                parent.remove(el)
                changed = True
    merge_numbers(root)
    return etree.tostring(root, encoding="unicode")
