"""Collect both models' readings and confidences for every benchmark image (for tuning decisions).

python samples/collect_scores.py BENCH.docx TRUTH.json OUT.json      (resumes if OUT.json exists)
"""
import json
import sys
import time
from pathlib import Path

from PIL import Image
import io

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "server"))
import core, second  # noqa: E402
from bench import images_with_ids, load_truth  # noqa: E402
from compare import canon  # noqa: E402

docx, truth_path, out = sys.argv[1:4]
truth = load_truth(truth_path)
data = json.loads(Path(out).read_text(encoding="utf-8")) if Path(out).exists() else {}
for (eq_id, cat), blob in images_with_ids(docx):
    if eq_id in data or eq_id not in truth:
        continue
    t0 = time.time()
    rgb = Image.open(io.BytesIO(blob)).convert("RGB")
    raw1, c1 = core.best_reading(rgb)
    t1 = time.time() - t0
    raw2, c2 = second.read(rgb)
    A, B = core.tidy(raw1), core.tidy(raw2)
    gt = canon(truth[eq_id])
    row = {"cat": cat, "A": A, "B": B, "c1": c1, "c2": c2,
           "okA": bool(core.try_omml(A)) and canon(A) == gt, "okB": bool(core.try_omml(B)) and canon(B) == gt,
           "agree": canon(A) == canon(B), "junkB": bool(core.JUNK.search(raw2)),
           "omA": bool(core.try_omml(A)), "omB": bool(core.try_omml(B)),
           "t_pix2tex": round(t1, 2), "t_total": round(time.time() - t0, 2)}
    data[eq_id] = row
    print(eq_id, cat, "A" if row["okA"] else "-", "B" if row["okB"] else "-", f"{row['t_total']}s", flush=True)
    if len(data) % 10 == 0:
        Path(out).write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
Path(out).write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
print("done", len(data))
