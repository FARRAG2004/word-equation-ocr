"""Compare engine line-ups with the real decision rule, from stored readings (no OCR run).

python samples/analyse_engines.py [ENGINE=readings.json ...]
  stored readings for pix2tex and pix2text_mfr come from tests/data; extra engines are given as
  NAME=file.json with {id: {latex, confidence}} per benchmark, e.g. texteller=tt_s100.json,tt_phys.json
"""
import itertools
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "server")); sys.path.insert(0, str(ROOT / "tests"))
import core  # noqa: E402
from compare import canon  # noqa: E402
from decide import decide  # noqa: E402
from run_all import SETS, engine_readings, readings  # noqa: E402

extra = {}  # engine -> {set: {id: (raw, conf)}}
for arg in sys.argv[1:]:
    name, files = arg.split("=")
    extra[name] = {}
    for set_name, f in zip(SETS, files.split(",")):
        extra[name][set_name] = {k: (v["latex"], v["confidence"]) for k, v in json.load(open(f, encoding="utf-8")).items()}

THRESH = {"pix2tex": 0.99, "pix2text_mfr": 0.95, "texteller": 0.95}
cache = {}


def score(order, set_name, thresholds):
    data, truth = readings(set_name)
    out = {"correct": 0, "wrong": 0, "flagged": 0, "oracle": 0}
    for k, row in data.items():
        raws = {**engine_readings(row), **{n: e[set_name][k] for n, e in extra.items() if k in e.get(set_name, {})}}
        rs = []
        for n in order:
            key = (set_name, k, n)
            if key not in cache:
                cache[key] = core.reading(n, *raws[n])
            rs.append(cache[key])
        gt = canon(truth[k])
        out["oracle"] += any(r.omml and canon(r.latex) == gt for r in rs)
        status, _, chosen = decide(rs, thresholds)
        if status != "converted":
            out["flagged"] += 1
        elif canon(chosen.latex) == gt:
            out["correct"] += 1
        else:
            out["wrong"] += 1
    return out


engines_all = ["pix2text_mfr", "pix2tex"] + list(extra)
lineups = [("pix2text_mfr", "pix2tex")]
for n in extra:
    lineups += [(n,)] + [p for p in itertools.permutations(engines_all, 3) if n in p][:6]
print(f"{'line-up (trust order)':<44}" + "".join(f"{s:>34}" for s in SETS))
for order in lineups:
    cells = []
    for s in SETS:
        r = score(order, s, THRESH)
        cells.append(f"{r['correct']:>4} ok {r['wrong']:>3} wrong {r['flagged']:>3} flag (best {r['oracle']})")
    print(f"{' > '.join(order):<44}" + "".join(f"{c:>34}" for c in cells))
