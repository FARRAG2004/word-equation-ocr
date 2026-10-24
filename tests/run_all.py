"""Strict test suite. Exit code 0 only if everything passes; build.ps1 refuses to package otherwise.

    python tests/run_all.py            fast stages (about 1 minute, no OCR models needed)
    python tests/run_all.py --full     + real OCR on both benchmarks (about 15 minutes, GPU)
    python tests/run_all.py --word     + every structure case rendered by Word (needs Word)
    python tests/run_all.py --update-baseline   accept current scores as the new minimum

Stages
 1 structure   tests/test_omml.py: each LaTeX construct reaches Word as the right OMML object
 2 stability   tidy() is idempotent on every stored reading (a second pass changes nothing)
 3 refusals    no correct benchmark reading is refused by the output check
 4 decisions   stored readings replayed through decide(): correct >= baseline, wrong <= baseline
"""
import json
import subprocess
import sys
import time
from pathlib import Path

TESTS = Path(__file__).resolve().parent
ROOT = TESTS.parent
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "server")); sys.path.insert(0, str(TESTS))

import core  # noqa: E402
import test_omml  # noqa: E402
from compare import canon  # noqa: E402
from decide import Reading, decide  # noqa: E402

DATA = TESTS / "data"
BASELINE = TESTS / "baseline.json"
SETS = {"sample100": "sample_100.docx", "physics360": "physics_bench.docx"}
failures = []


def check(ok, message):
    print(("  ok    " if ok else "  FAIL  ") + message)
    if not ok:
        failures.append(message)


def stage_structure():
    print("1 structure")
    bad = 0
    for latex, test, why in test_omml.CASES:
        try:
            ok = test(test_omml.omml(latex))
        except Exception as e:
            ok, why = False, f"{why} ({type(e).__name__}: {e})"
        if not ok:
            bad += 1
            print(f"        {why}: {latex}")
    for latex in test_omml.REFUSE:
        if not test_omml.refused(latex):
            bad += 1
            print(f"        not refused: {latex}")
    check(bad == 0, f"{len(test_omml.CASES) + len(test_omml.REFUSE) - bad}/{len(test_omml.CASES) + len(test_omml.REFUSE)} structure cases")


def readings(name):
    """Stored readings of one benchmark; extra engines live in readings_<set>_<engine>.json."""
    data = json.loads((DATA / f"readings_{name}.json").read_text(encoding="utf-8"))
    for f in DATA.glob(f"readings_{name}_*.json"):
        engine = f.stem[len(f"readings_{name}_"):]
        for k, v in json.loads(f.read_text(encoding="utf-8")).items():
            if k in data:
                data[k].setdefault("extra", {})[engine] = (v["latex"], v["confidence"])
    truth = json.loads((DATA / f"truth_{name}.json").read_text(encoding="utf-8"))
    return data, truth


def engine_readings(row):
    """Stored rows hold pix2tex (A) and pix2text_mfr (B), or a generic 'readings' map, plus extra engines."""
    if "readings" in row:
        out = {n: (r["raw"], r["conf"]) for n, r in row["readings"].items()}
    else:
        out = {"pix2tex": (row["A"], row["c1"]), "pix2text_mfr": (row["B"], row["c2"])}
    return {**out, **row.get("extra", {})}


def stage_stability():
    print("2 stability")
    unstable = []
    for name in SETS:
        data, _ = readings(name)
        for k, row in data.items():
            for raw, _ in engine_readings(row).values():
                once = core.tidy(raw)
                if core.tidy(once) != once:
                    unstable.append((name, k, once[:60]))
    for u in unstable[:5]:
        print("        ", u)
    check(not unstable, f"cleanup is stable on every stored reading ({len(unstable)} unstable)")


def stage_refusals():
    print("3 refusals")
    wrongly = []
    for name in SETS:
        data, truth = readings(name)
        for k, row in data.items():
            for raw, _ in engine_readings(row).values():
                lx = core.tidy(raw)
                try:
                    core.latex_to_omml(lx)
                except core.Unexpressible as e:
                    if canon(lx) == canon(truth[k]):
                        wrongly.append((name, k, str(e)))
                except Exception:
                    pass
    for w in wrongly[:5]:
        print("        ", w)
    check(not wrongly, f"no correct reading refused ({len(wrongly)} refused)")


def replay(name):
    data, truth = readings(name)
    order = [n for n, _ in core.settings.ENGINES]
    score = {"correct": 0, "wrong": 0, "flagged": 0}
    for k, row in data.items():
        raws = engine_readings(row)
        rs = [core.reading(n, *raws[n]) for n in order if n in raws]
        status, _, chosen = decide(rs, core.THRESHOLDS)
        if status != "converted":
            score["flagged"] += 1
        elif canon(chosen.latex) == canon(truth[k]):
            score["correct"] += 1
        else:
            score["wrong"] += 1
    return score


def compare_to_baseline(kind, scores, update):
    base = json.loads(BASELINE.read_text(encoding="utf-8")) if BASELINE.exists() else {}
    if update:
        base[kind] = scores
        BASELINE.write_text(json.dumps(base, indent=1), encoding="utf-8")
        print(f"        baseline for {kind} updated")
        return
    for name, s in scores.items():
        b = base.get(kind, {}).get(name)
        line = f"{name}: {s['correct']} correct, {s['wrong']} wrong, {s['flagged']} flagged"
        if b is None:
            check(False, line + " (no baseline: run with --update-baseline)")
        else:
            check(s["correct"] >= b["correct"] and s["wrong"] <= b["wrong"],
                  line + f"  (baseline {b['correct']} correct, {b['wrong']} wrong)")


def stage_decisions(update):
    print("4 decisions (stored readings replayed)")
    compare_to_baseline("replay", {name: replay(name) for name in SETS}, update)


def stage_full(update):
    print("5 full OCR benchmarks")
    scores = {}
    docs = {"sample100": ROOT / "samples" / "sample_100.docx", "physics360": ROOT / "samples" / "physics_bench.docx"}
    for name, doc in docs.items():
        if not doc.exists():
            check(False, f"{doc} missing (sample_100.docx goes in samples/, physics_bench.docx: python samples/make_physics.py)")
            continue
        t0 = time.time()
        out = subprocess.run([sys.executable, str(ROOT / "bench.py"), str(doc), str(DATA / f"truth_{name}.json"), "--rerun"],
                             capture_output=True, text=True, encoding="utf-8", errors="replace").stdout
        line = next((l for l in out.splitlines() if l.startswith("Pipeline as shipped")), "")
        import re
        m = re.search(r"converted (\d+), flagged (\d+), skipped (\d+); wrong among converted (\d+)", line)
        if not m:
            check(False, f"{name}: bench.py produced no result")
            continue
        conv, flag, skip, wrong = map(int, m.groups())
        scores[name] = {"correct": conv - wrong, "wrong": wrong, "flagged": flag + skip}
        print(f"        {name}: {time.time() - t0:.0f}s")
    compare_to_baseline("full", scores, update)


def stage_word():
    print("6 Word rendering")
    from docx import Document
    from lxml import etree
    out = TESTS / "out"
    out.mkdir(exist_ok=True)
    doc = Document()
    for latex, _, why in test_omml.CASES:
        p = doc.add_paragraph(f"{why}:  ")
        p._p.append(etree.fromstring(core.latex_to_omml(core.tidy(latex))))
    path = out / "word_render.docx"
    doc.save(path)
    # use the user's running Word if there is one (a second instance cannot start), else a hidden one;
    # only this test file is opened, read-only, and closed again
    ps = ("try { $w = [Runtime.InteropServices.Marshal]::GetActiveObject('Word.Application'); $own = $false } "
          "catch { $w = New-Object -ComObject Word.Application; $w.Visible = $false; $own = $true }; "
          f"$d = $w.Documents.Open('{path}', $false, $true, $false, '', '', $false, '', '', 0, 0, $false); "
          f"try {{ $n = $d.OMaths.Count; $d.ExportAsFixedFormat('{out / 'word_render.pdf'}', 17); $n }} "
          "finally { $d.Close(0); if ($own) { $w.Quit() } }")
    res = subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True, text=True)
    lines = [l for l in res.stdout.strip().splitlines() if l.strip().isdigit()]
    n = int(lines[-1]) if lines else -1
    if n < 0:
        print("        ", (res.stderr or res.stdout).strip()[:300])
    check(n == len(test_omml.CASES), f"Word accepted {n}/{len(test_omml.CASES)} equations (PDF: tests/out/word_render.pdf)")


if __name__ == "__main__":
    args = set(sys.argv[1:])
    update = "--update-baseline" in args
    stage_structure()
    stage_stability()
    stage_refusals()
    stage_decisions(update)
    if "--full" in args:
        stage_full(update)
    if "--word" in args:
        stage_word()
    print(f"\n{'ALL PASSED' if not failures else f'{len(failures)} FAILED'}")
    sys.exit(1 if failures else 0)
