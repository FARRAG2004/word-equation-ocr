"""Cleanup of OCR LaTeX before conversion: tokenised spacing, model quirks, constructs Word renders badly.

Every rule here was added for an error seen on a benchmark or a user document; tests/test_omml.py
and tests/run_all.py check them.
"""
import re


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
    # \substack exists for several rows; the second model writes "\," where the "\\" row break was
    (r"\\substack\{((?:[^{}\\]|\{[^{}]*\}|\\(?!\\)[A-Za-z]+|\\[^\\])*?)\\,((?:[^{}\\]|\{[^{}]*\}|\\[A-Za-z]+)*)\}",
     lambda m: r"\substack{" + m.group(1) + r"\\" + m.group(2) + "}" if "\\\\" not in m.group(0) else m.group(0)),
    # "{\dot{x}}{{}_{1}=...}": a script on an empty base (Word shows a dotted box) -> attach it to the group before
    (rf"(\{{{G}\}})\{{\{{\}}([_^](?:\{{[^{{}}]*\}}|[A-Za-z0-9]))", r"\1\2{"),
    # Feynman slash: neither model knows \not, both write "A/" with nothing to divide by -> A with a slash through it
    (r"(\\[A-Za-z]+|[A-Za-z])/(?=\s*(?:[)\]},;=+\-|]|\\right|\\\}|$))", r"\\not{\1}"),
    (r"([_^])\{\\ +", r"\1{"),                      # "^{\ 2}": a leading space inside a script
    (r"(_\{[^{}]*\})\{\}\^", r"\1^"),                # "\partial_{5}{}^{2}" -> "\partial_{5}^{2}"
    (r"[_^]\{\s*(?:\\mathrm\{\s*\})?\s*\}", ""),     # empty sub/superscripts
    (r"\\mathbf\s*\{?\s*\\nabla\s*\}?", r"\\nabla"),
    (r"\\(mathbf|mathrm|mathcal|mathit|boldsymbol)\s+([A-Za-z0-9])", r"\\\1{\2}"),
    (r"\{\\(?:it|rm)\s+([^{}]*)\}", r"\1"),
    # TexTeller style: old font switches and explicit italics, all the default look in math
    (r"\{\\bf\s*([^{}]*)\}", r"\\mathbf{\1}"),
    (r"\\(?:mathit|textit)\{([A-Za-z]+)\}", r"\1"),
    (r"\\dot\{\\imath\}", "i"), (r"\\dot\{\\jmath\}", "j"),
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


MATH_DELIMITERS = re.compile(r"^\s*(?:\\\[|\\\(|\$\$|\$)\s*(.*?)\s*(?:\\\]|\\\)|\$\$|\$)\s*$", re.S)


def tidy(latex):
    m = MATH_DELIMITERS.match(latex)  # some models wrap the equation in \[...\], \(...\) or $...$
    if m:
        latex = m.group(1)
    latex = re.sub(r"\\(begin|end)\{(?:split|align\*?|gathered)\}", r"\\\1{aligned}", latex)  # same rows, same handling
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


def without_limits(latex):
    """The LaTeX with the conditions under/after big operators removed, to see where two readings differ."""
    return re.sub(rf"(\\(?:lim|sum|prod|max|min|sup|inf|int|iint|iiint|oint|bigcup|bigcap))\s*_(?:\{{{G}\}}|\S)", r"\1", latex)
