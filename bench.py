"""Score OCR against a ground truth and sweep the confidence threshold.

python bench.py sample_100.docx ground_truth.json [--rerun]

Images are matched to ground-truth IDs via the "EQ-001 [category]" paragraph before each image.
ground_truth.json may be {"EQ-001": "latex", ...}, {"EQ-001": {"latex": ...}, ...} or [{"id": ..., "latex": ...}, ...].
OCR results are cached next to the document, so re-scoring is instant (--rerun to redo the OCR).
"""
import argparse
import json
import re
import sys
import time
from collections import defaultdict
from difflib import SequenceMatcher
from pathlib import Path

from docx import Document
from docx.oxml.ns import qn
from lxml import etree
from latex2mathml.converter import convert as latex_to_mathml

sys.path.insert(0, str(Path(__file__).parent / "server"))
import core  # noqa: E402

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


def load_truth(path):
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if isinstance(data, list):
        data = {d.get("id") or d.get("ID"): d for d in data}
    out = {}
    for k, v in data.items():
        if isinstance(v, dict):
            v = v.get("latex") or v.get("tex") or v.get("ground_truth")
        out[k] = v
    return out


def images_with_ids(docx_path):
    doc = Document(docx_path)
    label = None
    for p in doc.paragraphs:
        m = re.match(r"(EQ-\d+)\s*\[(\w+)\]", p.text.strip())
        if m:
            label = m.groups()
        blip = p._p.find(f".//{qn('wp:inline')}//{qn('a:blip')}")
        if blip is not None:
            part = doc.part.related_parts[blip.get(qn("r:embed"))]
            yield (label or (part.partname, "?")), part.blob
            label = None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("docx")
    ap.add_argument("truth")
    ap.add_argument("--rerun", action="store_true")
    ap.add_argument("--cache", help="score readings from another engine: JSON {id: {latex, confidence}}")
    args = ap.parse_args()

    cache_path = Path(args.cache) if args.cache else Path(args.docx).with_suffix(".ocr_cache.json")
    cache = {} if args.rerun or not cache_path.exists() else json.loads(cache_path.read_text(encoding="utf-8"))
    for v in cache.values():
        if "omml_ok" not in v:
            try:
                core.latex_to_omml(v["latex"]); v["omml_ok"] = True
            except Exception:
                v["omml_ok"] = False
    rows = []
    for (eq_id, cat), blob in images_with_ids(args.docx):
        if args.cache:
            cache.setdefault(eq_id, {"latex": "", "confidence": 0.0, "omml_ok": False})["cat"] = cat
        elif eq_id not in cache:
            t0 = time.time()
            r = core.process(blob, source=f"bench:{eq_id}")
            cache[eq_id] = {"cat": cat, "latex": r["latex"], "confidence": r["confidence"],
                            "omml_ok": bool(r["omml"]), "status": r["status"], "engine": r.get("engine", ""),
                            "seconds": round(time.time() - t0, 2)}
            print(f"OCR {eq_id} {r['status']:<9} {r.get('engine', ''):<12} {r['confidence']:.3f}", flush=True)
        rows.append({"id": eq_id, **cache[eq_id]})
    cache_path.write_text(json.dumps(cache, indent=1, ensure_ascii=False), encoding="utf-8")

    truth = load_truth(args.truth)
    missing = [r["id"] for r in rows if r["id"] not in truth]
    if missing:
        print(f"WARNING: {len(missing)} IDs not in ground truth, left out of the score: {missing[:5]}")
    rows = [r for r in rows if r["id"] in truth]
    for r in rows:
        gt = truth.get(r["id"], "")
        r["correct"] = bool(gt) and r["omml_ok"] and canon(r["latex"]) == canon(gt)
        r["sim"] = SequenceMatcher(None, canon(r["latex"]), canon(gt)).ratio() if gt else 0.0

    n = len(rows)
    print(f"\nRaw OCR accuracy (no verification): {sum(r['correct'] for r in rows)}/{n}")
    by_cat = defaultdict(list)
    for r in rows:
        by_cat[r["cat"]].append(r)
    for cat, rs in by_cat.items():
        print(f"  {cat:<11} {sum(r['correct'] for r in rs)}/{len(rs)}")

    if all("status" in r for r in rows):
        conv = [r for r in rows if r["status"] == "converted"]
        wrong = [r["id"] for r in conv if not r["correct"]]
        print(f"\nPipeline as shipped: converted {len(conv)}, flagged {sum(r['status'] == 'flagged' for r in rows)}, "
              f"skipped {sum(r['status'] == 'skipped' for r in rows)}; wrong among converted {len(wrong)} {wrong}; "
              f"correct but flagged {sum(r['correct'] and r['status'] != 'converted' for r in rows)}")
        for eng in sorted({r["engine"] for r in conv}):
            e = [r for r in conv if r["engine"] == eng]
            print(f"  by {eng:<12} {len(e):>3} converted, {sum(not r['correct'] for r in e)} wrong")
        if any("seconds" in r for r in rows):
            print(f"  OCR time: {sum(r.get('seconds', 0) for r in rows):.0f}s total")

    print("\nthreshold  converted  wrong-converted  precision  correct-but-flagged")
    for t in (0.90, 0.93, 0.95, 0.96, 0.97, 0.975, 0.98, 0.985, 0.99, 0.995):
        conv = [r for r in rows if r["omml_ok"] and r["confidence"] >= t]
        wrong = sum(not r["correct"] for r in conv)
        missed = sum(r["correct"] and r["confidence"] < t for r in rows)
        prec = (len(conv) - wrong) / len(conv) if conv else 0
        mark = "  <- current" if abs(t - core.THRESHOLD) < 1e-9 else ""
        print(f"  {t:<8} {len(conv):>9} {wrong:>16} {prec:>10.1%} {missed:>20}{mark}")

    print("\nWrong readings (id, confidence, OCR vs truth):")
    for r in sorted(rows, key=lambda r: -r["confidence"]):
        if not r["correct"]:
            print(f"  {r['id']} {r['confidence']:.3f} sim={r['sim']:.2f}\n    ocr:   {r['latex']}\n    truth: {truth.get(r['id'], '')}")


if __name__ == "__main__":
    main()
