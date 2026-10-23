"""Compare decision rules on data from collect_scores.py.

python samples/analyse_scores.py XP.json=TRUTH.json [XP2.json=TRUTH2.json ...]
Readings are re-cleaned with the current core.tidy() and re-scored, so cleanup changes need no new OCR run.
"""
import json
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "server"))
import core  # noqa: E402
from bench import load_truth  # noqa: E402
from compare import canon  # noqa: E402

rows = {}
for arg in sys.argv[1:]:
    path, truth_path = arg.split("=")
    truth = load_truth(truth_path)
    for k, v in json.load(open(path, encoding="utf-8")).items():
        gt = canon(truth[k])
        v["A"], v["B"] = core.tidy(v["A"]), core.tidy(v["B"])
        v["omA"], v["omB"] = bool(core.try_omml(v["A"])), bool(core.try_omml(v["B"]))
        v["okA"] = v["omA"] and canon(v["A"]) == gt
        v["okB"] = v["omB"] and canon(v["B"]) == gt
        v["agree"] = canon(v["A"]) == canon(v["B"])
        rows[f"{path}:{k}"] = v


def cascade(r):  # v1.2: pix2tex if confident, else second model if confident, else flag
    if r["omA"] and r["c1"] >= 0.99:
        return "A", r["t_pix2tex"]
    if r["omB"] and not r["junkB"] and r["c2"] >= 0.95:
        return "B", r["t_total"]
    return None, r["t_total"]


def agree_then(prefer_b=0.95, accept_a=0.99):
    """Agreement -> accept. Disagreement -> pix2text-mfr if confident, else pix2tex if confident, else flag."""
    def rule(r):
        if r["agree"] and r["omA"]:
            return "A", r["t_total"]
        if r["omB"] and not r["junkB"] and r["c2"] >= prefer_b:
            return "B", r["t_total"]
        if r["omA"] and r["c1"] >= accept_a:
            return "A", r["t_total"]
        return None, r["t_total"]
    return rule


def report(name, rule):
    tot = defaultdict(lambda: [0, 0, 0, 0])  # correct, wrong, flagged, n
    t = 0.0
    for k, r in rows.items():
        pick, sec = rule(r)
        t += sec
        for key in ("all", r["cat"].split("-")[0] + ("/" + r["cat"].split("-")[1] if "-" in r["cat"] else "")):
            c = tot[key]
            c[3] += 1
            if pick is None:
                c[2] += 1
            elif r["ok" + pick]:
                c[0] += 1
            else:
                c[1] += 1
    a = tot["all"]
    print(f"{name:<26} correct {a[0]:>3}/{a[3]}  wrong {a[1]:>3}  flagged {a[2]:>3}  time {t / a[3]:.2f}s/img")
    return tot


oracle = sum(r["okA"] or r["okB"] for r in rows.values())
print(f"images: {len(rows)}; either model right (upper bound): {oracle}; pix2tex right: "
      f"{sum(r['okA'] for r in rows.values())}; pix2text-mfr right: {sum(r['okB'] for r in rows.values())}\n")
report("v1.2 cascade", cascade)
for b in (0.95, 0.98, 0.99):
    report(f"agree, else mfr>={b}", agree_then(b))
if "--why" in __import__("os").environ.get("EQ_ANALYSE", ""):
    for name, rule in (("v1.2", cascade), ("new", agree_then(0.95))):
        for k, r in rows.items():
            pick, _ = rule(r)
            if pick is None or not r["ok" + pick]:
                print(f"  {name:<4} {k.split(':')[-1]} picked {pick} c1={r['c1']:.3f} c2={r['c2']:.3f}\n"
                      f"       A: {r['A'][:100]}\n       B: {r['B'][:100]}")
print("\nper category, v1.2 cascade vs agree-then-mfr>=0.95:")
c12, new = report("", cascade), report("", agree_then(0.95))
for k in sorted(new):
    a, b = c12[k], new[k]
    print(f"  {k:<22} v1.2 {a[0]:>3}/{a[3]} ({a[1]} wrong, {a[2]} flagged)   new {b[0]:>3}/{b[3]} ({b[1]} wrong, {b[2]} flagged)")
