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
import core  # noqa: E402
import engines  # noqa: E402
import settings  # noqa: E402
from bench import images_with_ids, load_truth  # noqa: E402
from compare import canon  # noqa: E402

NAMES = [n for n, _ in settings.ENGINES]

docx, truth_path, out = sys.argv[1:4]
truth = load_truth(truth_path)
data = json.loads(Path(out).read_text(encoding="utf-8")) if Path(out).exists() else {}
for (eq_id, cat), blob in images_with_ids(docx):
    if eq_id in data or eq_id not in truth:
        continue
    t0 = time.time()
    rgb = Image.open(io.BytesIO(blob)).convert("RGB")
    gt = canon(truth[eq_id])
    row = {"cat": cat, "readings": {}}
    for name in NAMES:  # every configured engine, one after another, timed separately
        t1 = time.time()
        raw, conf = engines.get(name).read(rgb)
        lx = core.tidy(raw)
        row["readings"][name] = {"raw": raw, "latex": lx, "conf": conf, "seconds": round(time.time() - t1, 2),
                                 "junk": bool(core.JUNK.search(raw)), "ok": bool(core.try_omml(lx)) and canon(lx) == gt}
    # legacy fields (pix2tex = A, pix2text_mfr = B) used by older analyses
    if {"pix2tex", "pix2text_mfr"} <= set(row["readings"]):
        a, b = row["readings"]["pix2tex"], row["readings"]["pix2text_mfr"]
        row.update(A=a["latex"], B=b["latex"], c1=a["conf"], c2=b["conf"], okA=a["ok"], okB=b["ok"],
                   agree=canon(a["latex"]) == canon(b["latex"]), junkB=b["junk"],
                   omA=bool(core.try_omml(a["latex"])), omB=bool(core.try_omml(b["latex"])),
                   t_pix2tex=a["seconds"], t_total=a["seconds"] + b["seconds"])
    data[eq_id] = row
    print(eq_id, cat, " ".join(f"{n}={'ok' if r['ok'] else '-'}" for n, r in row["readings"].items()), flush=True)
    if len(data) % 10 == 0:
        Path(out).write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
Path(out).write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
print("done", len(data))
