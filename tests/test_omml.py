"""Structure tests for LaTeX -> OMML.   python tests/test_omml.py   (exit code 1 on failure)

Each case states which OMML object Word must receive, so regressions in the conversion layer
show up without OCR or Word.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "server"))
from lxml import etree  # noqa: E402
import core  # noqa: E402

M = "{http://schemas.openxmlformats.org/officeDocument/2006/math}"


def omml(latex):
    return etree.fromstring(core.latex_to_omml(core.tidy(latex)))


def tags(root):
    return [e.tag[len(M):] for e in root.iter() if isinstance(e.tag, str) and e.tag.startswith(M)]


def chr_of(e):
    c = e.find(f".//{M}chr")
    return c.get(M + "val") if c is not None else None


def text(root):
    return "".join(t.text or "" for t in root.iter(M + "t"))


CASES = []


def case(latex, check, why):
    CASES.append((latex, check, why))


def has(tag, ch=None, absent=("limUpp", "limLow")):
    def check(root):
        found = [e for e in root.iter(M + tag) if ch is None or chr_of(e) == ch]
        return bool(found) and not any(t in tags(root) for t in absent)
    return check


# BUG 1 - accents must be m:acc above the symbol (bars: m:bar), never a limit or superscript
for lx, ch in [(r"\vec{a}", "⃗"), (r"\overrightarrow{AB}", "⃗"), (r"\dot{x}", "̇"),
               (r"\ddot{x}", "̈"), (r"\tilde{f}", "̃"), (r"\widehat{ABC}", "̂"), (r"\hat{x}", "̂"),
               (r"\vec{F}=m\vec{a}", "⃗"), (r"\dot{\omega}=\frac{M}{J}", "̇")]:
    case(lx, has("acc", ch), f"accent {ch!r} as m:acc")
for lx in (r"\bar{x}", r"\overline{AB}", r"\overline{AB}\parallel\overline{CD}"):
    case(lx, has("bar"), "bar as m:bar")

# BUG 2 - binomials: noBar fraction inside a real delimiter; matrices: m:m, never m:f
def binom(root):
    d = [e for e in root.iter(M + "d") if e.find(f".//{M}type") is not None and e.find(f".//{M}type").get(M + "val") == "noBar"]
    return bool(d)
for lx in (r"\binom{n}{k}", r"\binom{n}{k}=\frac{n!}{k!(n-k)!}", r"C_n^k=\binom{n}{k}", r"\binom{10}{3}=120"):
    case(lx, binom, "binomial = m:d around noBar m:f")
def matrix(rows):
    def check(root):
        ms = list(root.iter(M + "m"))
        return bool(ms) and len(ms[0].findall(M + "mr")) == rows and "eqArr" not in tags(root) and \
            all(f.find(f".//{M}type") is not None for f in root.iter(M + "f")) is not False
    return check
for lx, rows in [(r"\begin{Bmatrix}x\\y\end{Bmatrix}", 2), (r"\begin{pmatrix}x\\y\end{pmatrix}", 2),
                 (r"\begin{bmatrix}1\\0\\0\end{bmatrix}", 3), (r"\left\{ {x \atop y} \right\}", 2),
                 (r"\begin{pmatrix}a&b\\c&d\end{pmatrix}", 2), (r"\begin{vmatrix}a&b\\c&d\end{vmatrix}", 2)]:
    case(lx, matrix(rows), f"matrix with {rows} rows as m:m")

# BUG 3 - braces are m:groupChr; overset/stackrel are m:limUpp
for lx, ch in [(r"\underbrace{a+a+\cdots+a}_{n}=na", "⏟"), (r"\overbrace{x_1+\cdots+x_n}^{n\text{ terms}}", "⏞")]:
    case(lx, has("groupChr", ch, absent=()), f"{ch} as m:groupChr")
for lx in (r"a\overset{def}{=}b", r"a\stackrel{?}{=}b"):
    case(lx, lambda r: "limUpp" in tags(r), "overset/stackrel as m:limUpp")

# BUG 4 - "%" is a literal in every position
for lx in (r"\Delta U=5\%\cdot U_n", r"\eta=\frac{P_2}{P_1}\cdot 100\%", r"s=4\%", r"\%=5", r"\frac{5\%}{2}", r"x_{5\%}"):
    case(lx, lambda r: "%" in text(r) and "V" not in text(r), "percent kept as %")

# must not regress (classes the stress test passed)
case(r"\sqrt{\frac{1}{N}\sum_{i=1}^{N}(x_i-\mu)^2}", lambda r: "rad" in tags(r) and "nary" in tags(r), "radical containing a sum")
case(r"\lim_{x\to 0}\frac{\sin x}{x}=1", lambda r: "limLow" in tags(r) and "sSub" not in tags(r), "limit under lim")
case(r"\max_{i}x_i", lambda r: "limLow" in tags(r), "limit under max")
case(r"\left.\frac{df}{dx}\right|_{x=0}", lambda r: "f" in tags(r), "evaluation bar")
case(r"f''(x)+x_{i_j}^{2}", lambda r: "sSubSup" in tags(r) or "sSub" in tags(r), "primes and nested scripts")
case(r"\left(\frac{a}{b}\right)", lambda r: "d" in tags(r) and "f" in tags(r), "delimiters around a fraction stretch (m:d)")
case(r"\begin{cases}x^2&x\geq 0\\-x^2&x<0\end{cases}", lambda r: "d" in tags(r) and ("m" in tags(r) or "eqArr" in tags(r)),
     "cases: stretching brace around the stack")
case(r"\frac{a}{b}+\frac{c}{d}", lambda r: "d" not in tags(r), "plain fractions get no delimiters")
case(r"f(x)=\frac{1}{x}", lambda r: "d" not in tags(r), "f(x) is not turned into a delimiter")
def upright_lim(r):
    lims = [x for x in r.iter(M + "limLow") if text(x.find(M + "e")) == "lim"]
    return bool(lims) and all(x.find(f".//{M}sty") is not None for x in lims)
case(r"\lim_{n\to\infty}\left(1+\frac{1}{n}\right)^n=e", upright_lim, "lim is upright")
case(r"\begin{array}{c}x+2y=5\\3x-y=1\end{array}", lambda r: "eqArr" in tags(r) or "m" in tags(r), "system of equations")
case(r"\begin{aligned}{\dot{x}_{1}}&{{}=a_{11}x_{1}+b_{1}u}\\{\dot{x}_{2}}&{{}=a_{21}x_{1}+b_{2}u}\\\end{aligned}",
     lambda r: ("eqArr" in tags(r) or "m" in tags(r)) and "acc" in tags(r), "aligned system (second model's style)")
case(r"\begin{aligned}{\sum_{n=1}^{\infty}\frac{1}{n^{2}}}&{{}=\frac{\pi^{2}}{6}}\\\end{aligned}",
     lambda r: "nary" in tags(r) and "eqArr" not in tags(r), "one-line aligned is just the line")
case(r"\int_0^\infty e^{-x^2}\,dx=\frac{\sqrt\pi}{2}", lambda r: "nary" in tags(r), "integral as n-ary")
case(r"x^{-}+e^{-}", lambda r: "sSup" in tags(r), "a superscript minus is not an accent")

# fonts: bold vectors, script and blackboard letters are kept (Word shows the math-alphabet characters)
case(r"\mathbf{F}=m\mathbf{a}", lambda r: "\U0001d405" in text(r) and "\U0001d41a" in text(r), "bold vectors kept")
case(r"\mathcal{L}\{f(t)\}", lambda r: "ℒ" in text(r), "script L kept")
case(r"x\in\mathbb{R}", lambda r: "ℝ" in text(r), "blackboard R kept")
def upright(word):
    def check(r):
        runs = [x for x in r.iter(M + "r") if word in "".join(t.text or "" for t in x.iter(M + "t"))]
        return bool(runs) and all(x.find(f"{M}rPr/{M}sty") is not None for x in runs)
    return check
for lx, w in ((r"\operatorname{rank}A=2", "rank"), (r"\mathbf{A}^{-1}=\frac{1}{\det\mathbf{A}}\operatorname{adj}(\mathbf{A})", "adj"),
              (r"\operatorname{sgn}(x)", "sgn")):
    case(lx, upright(w), f"operator name {w} upright")

# Feynman slash notation: "A/" with nothing to divide becomes A with a slash through it; real division untouched
def slashed(r):
    ph = list(r.iter(M + "phant"))
    outside = text(r).replace("".join(text(p) for p in ph), "", 1)
    return bool(ph) and all(text(p) == "/" for p in ph) and "/" not in outside
case(r"\hat{D}^{2}=D^{2}+i\gamma^{5}(\partial_{5}A/)+i\{\varphi,\partial_{5}\}-\partial_{5}^{\ 2}.",
     slashed, "slashed A: normal-size slash overlaid, none left after A")
case(r"(\partial/ -m)\psi=0", lambda r: "phant" in tags(r), "slashed partial")
case(r"v=s/t", lambda r: "phant" not in tags(r) and "/" in text(r), "real division a/b is untouched")
case(r"x^{\ 2}+\partial_{5}{}^{2}", lambda r: " " not in text(r) and "sSubSup" in tags(r), "no stray space or empty base in scripts")

# a space-only run makes Word's InsertXML insert a row of tabs (equation breaks across lines)
def spaces_safe(r):
    edged = [t for t in r.iter(M + "t") if t.text and (t.text[0] in "  " or t.text[-1] in "  ")]
    return bool(edged) and all(t.text[0] != " " and t.text[-1] != " " and
                               t.get("{http://www.w3.org/XML/1998/namespace}space") == "preserve" for t in edged)
for lx in (r"=92.3\ \mathrm{N\cdot m}", r"=1.84~\mathrm{s}", r"=1.85\text{ kW}"):
    case(lx, spaces_safe, "space before a unit is a preserved non-breaking space")

# standing rule: what cannot be expressed is refused, never emitted
def refused(latex):
    try:
        core.latex_to_omml(latex)
        return False
    except core.Unexpressible:
        return True
REFUSE = [r"a^{\rightarrow}", r"\unknowncommand{x}"]

if __name__ == "__main__":
    failed = 0
    for latex, check, why in CASES:
        try:
            ok = check(omml(latex))
        except Exception as e:
            ok, why = False, f"{why} ({type(e).__name__}: {e})"
        failed += not ok
        print(f"{'ok  ' if ok else 'FAIL'} {why:<45} {latex}")
    for latex in REFUSE:
        ok = refused(latex)
        failed += not ok
        print(f"{'ok  ' if ok else 'FAIL'} {'refused (flag the image)':<45} {latex}")
    print(f"\n{len(CASES) + len(REFUSE) - failed}/{len(CASES) + len(REFUSE)} passed")
    sys.exit(1 if failed else 0)
