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

sys.path.insert(0, str(Path(__file__).parent / "server"))
import core  # noqa: E402

from compare import canon  # noqa: E402


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
        m = re.match(r"(EQ-\d+)\s*\[([\w-]+)\]", p.text.strip())
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
