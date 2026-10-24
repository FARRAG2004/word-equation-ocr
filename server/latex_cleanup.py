"""Cleanup of OCR LaTeX before conversion: tokenised spacing, model quirks, constructs Word renders badly.

Every rule here was added for an error seen on a benchmark or a user document. Each carries an example
reading and what the rule turns it into; tests/run_all.py checks every pair, and tests/test_omml.py
checks the Word result of the whole cleanup.
"""
import re
from typing import NamedTuple


FUNCS = "lim|det|sin|cos|tan|cot|sec|csc|sinh|cosh|tanh|ln|log|exp|arctan|arcsin|arccos|max|min|sup|inf|deg|dim|ker|gcd"
G = r"(?:[^{}]|\{(?:[^{}]|\{[^{}]*\})*\})*"   # brace group content, two levels deep
BIG = r"sum|prod|int|oint|iint|iiint|bigcup|bigcap"


class Rule(NamedTuple):
    name: str
    pattern: str
    repl: object      # replacement string or function, as for re.sub
    example: str      # an OCR reading this rule fixes ...
    expected: str     # ... and what the rule alone turns it into


# Applied in this order, group after group. A new rule goes in the group it belongs to, with its example.
GROUPS = {
    # LaTeX markup that latex2mathml or Word handles badly
    "markup": [
        Rule("protect", r"\\protect\b\s*", "", r"\protect x", "x"),
        Rule("negative space", r"\\!", "", r"a\!b", "ab"),   # becomes an empty box in Word
        Rule("row break before sign", r"\\\\(?=[-+])", r"\\\\ ", r"a\\-b", r"a\\ -b"),   # latex2mathml misreads it
    ],
    # function and operator names, limits written around big operators
    "operators": [
        # tokenised models write "\operatorname { s i n }"
        Rule("spaced operator name", r"\\(operatorname\*?|mathrm)\s*\{\s*((?:[A-Za-z]\s*)+)\}",
             lambda m: "\\" + m.group(1) + "{" + re.sub(r"\s+", "", m.group(2)) + "}",
             r"\operatorname { s i n }", r"\operatorname{sin}"),
        # keep a space after the name: "\operatorname{sin}x" must become "\sin x", not "\sinx"
        Rule("known function", rf"\\(?:operatorname\*?|mathrm|mathsf|textsf|textrm|text)\{{\s*({FUNCS})\s*(?:\\,)?\s*\}}\s*(?=(.?))",
             lambda m: "\\" + m.group(1) + (" " if m.group(2).isalpha() else ""),
             r"\operatorname{sin}x", r"\sin x"),
        # other names (rank, adj, sgn, Tr, ...) are upright text with a thin space, not italic variables
        Rule("other operator name", r"\\operatorname\*?\{([A-Za-z]+)\}\s*", r"\\mathrm{\1}\\,",
             r"\operatorname{rank}A", r"\mathrm{rank}\,A"),
        # Word needs a real n-ary operator, not \underset/\overset around \sum
        Rule("underset overset sum", rf"\\underset\{{({G})\}}\{{\\overset\{{({G})\}}\{{?\\({BIG})\}}?\}}", r"\\\3_{\1}^{\2}",
             r"\underset{k=1}{\overset{n}{\sum}}x", r"\sum_{k=1}^{n}x"),
        Rule("overset underset sum", rf"\\overset\{{({G})\}}\{{\\underset\{{({G})\}}\{{?\\({BIG})\}}?\}}", r"\\\3_{\2}^{\1}",
             r"\overset{n}{\underset{k=1}{\sum}}x", r"\sum_{k=1}^{n}x"),
        Rule("underset sum", rf"\\underset\{{({G})\}}\{{?\\({BIG})\}}?", r"\\\2_{\1}",
             r"\underset{i}{\sum}x", r"\sum_{i}x"),
        Rule("mathop equals", r"\\mathop\s*=", "=", r"k\mathop=1", "k=1"),
        # \substack exists for several rows; the second model writes "\," where the "\\" row break was
        Rule("substack row break", r"\\substack\{((?:[^{}\\]|\{[^{}]*\}|\\(?!\\)[A-Za-z]+|\\[^\\])*?)\\,((?:[^{}\\]|\{[^{}]*\}|\\[A-Za-z]+)*)\}",
             lambda m: r"\substack{" + m.group(1) + r"\\" + m.group(2) + "}" if "\\\\" not in m.group(0) else m.group(0),
             r"\substack{i=1\,i\neq j}", r"\substack{i=1\\i\neq j}"),
    ],
    # sub/superscripts on nothing, empty or with stray spaces, and the slash models write for \not
    "scripts": [
        # a script on an empty base shows as a dotted box: attach it to the group before
        Rule("script on empty base", rf"(\{{{G}\}})\{{\{{\}}([_^](?:\{{[^{{}}]*\}}|[A-Za-z0-9]))", r"\1\2{",
             r"{\dot{x}}{{}_{1}=a}", r"{\dot{x}}_{1}{=a}"),
        # neither model knows \not, both write "A/" with nothing to divide by -> A with a slash through it
        Rule("Feynman slash", r"(\\[A-Za-z]+|[A-Za-z])/(?=\s*(?:[)\]},;=+\-|]|\\right|\\\}|$))", r"\\not{\1}",
             r"(\partial_{5}A/)", r"(\partial_{5}\not{A})"),
        Rule("leading space in script", r"([_^])\{\\ +", r"\1{", r"x^{\ 2}", "x^{2}"),
        Rule("split sub and superscript", r"(_\{[^{}]*\})\{\}\^", r"\1^", r"\partial_{5}{}^{2}", r"\partial_{5}^{2}"),
        Rule("empty script", r"[_^]\{\s*(?:\\mathrm\{\s*\})?\s*\}", "", "P_{}=1", "P=1"),
    ],
    # font commands: old switches, explicit italics, bold nabla (TexTeller style)
    "fonts": [
        Rule("bold nabla", r"\\mathbf\s*\{?\s*\\nabla\s*\}?", r"\\nabla", r"\mathbf{\nabla}", r"\nabla"),
        Rule("font without braces", r"\\(mathbf|mathrm|mathcal|mathit|boldsymbol)\s+([A-Za-z0-9])", r"\\\1{\2}",
             r"\mathbf E", r"\mathbf{E}"),
        Rule("old italic switch", r"\{\\(?:it|rm)\s+([^{}]*)\}", r"\1", r"{\it R}", "R"),
        Rule("old bold switch", r"\{\\bf\s*([^{}]*)\}", r"\\mathbf{\1}", r"{\bf F}", r"\mathbf{F}"),
        Rule("explicit italic", r"\\(?:mathit|textit)\{([A-Za-z]+)\}", r"\1", r"R_{\textit{eq}}", "R_{eq}"),
        Rule("dotless i", r"\\dot\{\\imath\}", "i", r"\dot{\imath}", "i"),
        Rule("dotless j", r"\\dot\{\\jmath\}", "j", r"\dot{\jmath}", "j"),
    ],
    # misreads and old commands seen on benchmarks
    "misreads": [
        # both models misread "%" after a number as one of these meaningless sequences (benchmark: 7 of 7)
        Rule("percent", r"(\d)\s*(?:\\mathcal\{[YV]\}(?:_\{?0\}?)?|\\lnot\s*_\{?0\}?|\\rangle_\{\\mathrm\{c\}\})\s*$", r"\1\\%",
             r"100\lnot_{0}", r"100\%"),
        Rule("cfrac", r"\\cfrac", r"\\frac", r"\cfrac{a}{b}", r"\frac{a}{b}"),
        Rule("root of", r"\\root\s*(\S+)\s*\\of\s*\{", r"\\sqrt[\1]{", r"\root 3 \of {27}", r"\sqrt[3]{27}"),
        Rule("underset lim", r"\\underset\s*\{((?:[^{}]|\{[^{}]*\})*)\}\s*\{\s*\\lim\s*\}", r"\\lim_{\1}",
             r"\underset{x\to0}{\lim}f", r"\lim_{x\to0}f"),
        Rule("spaced text", r"\\mathrm\s*\{\s*~\s*((?:[A-Za-z]\s*)+)\}", lambda m: r"\text{ " + re.sub(r"\s+", "", m.group(1)) + "}",
             r"\mathrm{~times}", r"\text{ times}"),
        Rule("norm bars", r"\\left\\Vert|\\right\\Vert", r"\\|", r"\left\Vert v\right\Vert", r"\| v\|"),
        Rule("empty accent", r"\\overset\s*\{(?:\s|\\ )*\}\s*", "", r"\overset{ }{M}", "{M}"),   # around matrices
        Rule("trailing empty subscript", r"_\s*\{(?:\s|\\ |_|\{|\})*\}\s*$", "", r"x_{\ _{}}", "x"),
    ],
}
RULES = [r for group in GROUPS.values() for r in group]
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
    for r in RULES:
        latex = re.sub(r.pattern, r.repl, latex)
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
