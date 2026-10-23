"""Repair OMML structures that MML2OMML.XSL gets wrong, and detect anything still unexpressible.

latex2mathml writes most accents as <mover> without accent="true", so the XSL turns them into
m:limUpp (a limit above), braces into a raw character in a limit, and single-column matrices into
m:eqArr. These are rewritten into the native Word objects: m:acc, m:bar, m:groupChr, m:d, m:m.
problems() lists what could still not be expressed; the caller flags such images instead of
inserting broken equations.
"""
from lxml import etree

NS = "http://schemas.openxmlformats.org/officeDocument/2006/math"
M = "{%s}" % NS

ACCENTS = {  # character the converter leaves in the limit -> combining accent Word expects
    "→": "⃗", "⃗": "⃗", "←": "⃖", "↔": "⃡",
    "˙": "̇", "̇": "̇", "¨": "̈", "̈": "̈",
    "~": "̃", "˜": "̃", "∼": "̃", "̃": "̃",
    "^": "̂", "ˆ": "̂", "̂": "̂", "ˇ": "̌", "̌": "̌",
    "´": "́", "`": "̀", "˘": "̆", "⃛": "⃛",
}
BARS = {"¯", "―", "‾", "_", "̅", "─"}
GROUPS = {"⏟": "bot", "⏞": "top", "⏝": "bot", "⏜": "top", "⎵": "bot", "⎴": "top"}
OPEN_CLOSE = {"(": ")", "[": "]", "{": "}", "|": "|", "‖": "‖", "⟨": "⟩"}


def el(tag, *children, **attrs):
    e = etree.Element(M + tag)
    for k, v in attrs.items():
        e.set(M + k, v)
    e.extend(children)
    return e


def text_of(e):
    """Text of an element that contains only runs, else None."""
    if e is None or any(c.tag != M + "r" for c in e):
        return None
    return "".join(t.text or "" for t in e.iter(M + "t"))


def replace(old, new):
    old.getparent().replace(old, new)


def fix_accents(root):
    for tag in ("limUpp", "limLow"):
        for lim_el in list(root.iter(M + tag)):
            ch = text_of(lim_el.find(M + "lim"))
            base = lim_el.find(M + "e")
            if not ch or len(ch) != 1 or base is None:
                continue
            if ch in BARS:
                replace(lim_el, el("bar", el("barPr", el("pos", val="top" if tag == "limUpp" else "bot")), base))
            elif ch in ACCENTS and tag == "limUpp":
                replace(lim_el, el("acc", el("accPr", el("chr", val=ACCENTS[ch])), base))
            elif ch in GROUPS:
                pos = GROUPS[ch]
                replace(lim_el, el("groupChr", el("groupChrPr", el("chr", val=ch), el("pos", val=pos),
                                                  el("vertJc", val="top" if pos == "bot" else "bot")), base))
    for acc in list(root.iter(M + "acc")):  # bars written as an accent character -> real bar
        chr_ = acc.find(f"{M}accPr/{M}chr")
        ch = chr_.get(M + "val") if chr_ is not None else "̂"
        if ch in BARS:
            replace(acc, el("bar", el("barPr", el("pos", val="top")), acc.find(M + "e")))
        elif ch in ACCENTS and ACCENTS[ch] != ch:
            chr_.set(M + "val", ACCENTS[ch])


def column_matrix(rows):
    mcs = el("mcs", el("mc", el("mcPr", el("count", val="1"), el("mcJc", val="center"))))
    return el("m", el("mPr", mcs), *[el("mr", el("e", *list(r))) for r in rows])


def no_bar(f):
    t = f.find(f"{M}fPr/{M}type")
    return t is not None and t.get(M + "val") == "noBar"


def fix_delimited(root):
    """'(' + stack + ')' written as plain characters -> m:d (which stretches), with matrices as m:m (never m:f).
    A lone '{' before a stack (cases) becomes a one-sided m:d."""
    for node in list(root.iter(M + "f", M + "eqArr", M + "m")):
        prev, nxt = node.getprevious(), node.getnext()
        if prev is None or prev.tag != M + "r":
            continue
        pt = prev.find(M + "t")
        nt = nxt.find(M + "t") if nxt is not None and nxt.tag == M + "r" else None
        if pt is None or not pt.text:
            continue
        beg = pt.text[-1]
        end = nt.text[0] if nt is not None and nt.text else ""
        if OPEN_CLOSE.get(beg) != end:
            if not (beg == "{" and node.tag in (M + "eqArr", M + "m") and end != "}"):
                continue
            end, nt = "", None  # cases: brace on the left only
        if node.tag == M + "eqArr" and end:
            inner = column_matrix(node.findall(M + "e"))
        elif node.tag == M + "f" and no_bar(node) and beg != "(":  # a noBar stack in braces/brackets is a vector
            inner = column_matrix([node.find(M + "num"), node.find(M + "den")])
        else:
            inner = node
        d = el("d", el("dPr", el("begChr", val=beg), el("endChr", val=end)), el("e"))
        node.addprevious(d)
        d.find(M + "e").append(inner)
        if node.getparent() is not None and inner is not node:
            node.getparent().remove(node)
        trims = [(prev, pt, pt.text[:-1])] + ([(nxt, nt, nt.text[1:])] if nt is not None else [])
        for run, t, keep in trims:
            if keep:
                t.text = keep
            else:
                run.getparent().remove(run)


LIMIT_OPS = {"lim", "max", "min", "sup", "inf", "limsup", "liminf"}


def fix_limits(root):
    """latex2mathml writes \\lim_{x\\to0} as a subscript beside "lim"; Word puts it underneath."""
    for s in list(root.iter(M + "sSub")):
        base = s.find(M + "e")
        if (text_of(base) or "").strip() in LIMIT_OPS:
            for r in base.iter(M + "r"):  # operator names are upright, not italic variables
                if r.find(M + "rPr") is None:
                    r.insert(0, el("rPr", el("sty", val="p")))
            replace(s, el("limLow", base, el("lim", *list(s.find(M + "sub")))))


def fix_slash(root):
    """\\not is drawn with the big solidus U+29F8, which runs far below the letter; a normal "/"
    overlaid on the letter matches it."""
    for ph in root.iter(M + "phant"):
        for t in ph.iter(M + "t"):
            if t.text and "⧸" in t.text:
                t.text = t.text.replace("⧸", "/")


XML_SPACE = "{http://www.w3.org/XML/1998/namespace}space"


def fix_spaces(root):
    """A run holding only a space (from "\\ " or "~" before a unit) is turned into a row of tabs by
    Word's InsertXML, which then breaks the equation across lines. Use a preserved non-breaking space."""
    for t in root.iter(M + "t"):
        s = t.text or ""
        if s and (s[0].isspace() or s[-1].isspace()):  # e.g. " " alone, or " kW" from \text{ kW}
            core = s.strip()
            lead, trail = len(s) - len(s.lstrip()), len(s) - len(s.rstrip())
            t.text = " " * lead + core + " " * (trail if core else 0)
            t.set(XML_SPACE, "preserve")


def fix(root):
    fix_accents(root)
    fix_delimited(root)
    fix_limits(root)
    fix_slash(root)
    fix_spaces(root)
    return root


def problems(root):
    """What this OMML still cannot show correctly; empty list means it is safe to insert."""
    found = []
    for t in root.iter(M + "t"):
        s = t.text or ""
        if "\\" in s:
            found.append(f"unconverted LaTeX command {s.strip()!r}")
        for g in GROUPS:
            if g in s:
                found.append(f"brace character {g!r} left as text")
    for tag in ("sSup", "sSub", "limUpp", "limLow"):
        part = "sup" if tag == "sSup" else "sub" if tag == "sSub" else "lim"
        for e in root.iter(M + tag):
            ch = text_of(e.find(M + part))
            if ch and len(ch) == 1 and (ch in ACCENTS or ch in BARS):
                found.append(f"accent {ch!r} placed as a {part} instead of above the symbol")
    return found
