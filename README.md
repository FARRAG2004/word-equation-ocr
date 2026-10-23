# Word Equation OCR

Free, open-source, **fully offline** Word add-in: it finds equation **images** in a document and replaces each one, in place, with a **native, editable Word equation**. No cloud, no API keys, no paid services.

<div dir="rtl">

إضافة مجانية ومفتوحة المصدر لبرنامج Word، **بتشتغل من غير إنترنت**. بتدوّر على **صور** المعادلات في المستند، وبتحط مكان كل صورة **معادلة Word حقيقية** تقدر تعدّلها. مفيش خدمات سحابية ولا مفاتيح API ولا أي حاجة مدفوعة.

</div>

## Install / التثبيت

1. Download **`EquationAddIn-x.y.z.zip`** from [Releases](../../releases/latest) and unzip it.
2. Close Word, then double-click **`install.bat`**.
3. Open Word: you'll find an **Equations** tab with the **Convert equation images** button. The first time, Word may ask whether to install the customization: click **Install**.

<div dir="rtl">

1. نزّل ملف **`EquationAddIn-x.y.z.zip`** من صفحة [Releases](../../releases/latest) وفك الضغط.
2. اقفل Word، وبعدين دوس مرتين على **`install.bat`**.
3. افتح Word. هتلاقي تبويب اسمه **Equations** وفيه زرار **Convert equation images**. أول مرة ممكن Word يسألك عن تثبيت الإضافة، دوس **Install**.

</div>

The installer downloads everything it needs by itself, per-user and without admin rights. It needs an internet connection **only during install**:

| What | Size | Notes |
|---|---|---|
| Python 3.12 (python.org) | ~25 MB | Skipped if Python 3.12 is already installed |
| PyTorch | ~2.5 GB with an NVIDIA GPU (CUDA), ~200 MB without | The CPU build is slower but works |
| pix2tex and other libraries | ~300 MB | |
| The two OCR models (pix2tex + pix2text-mfr) | ~230 MB | |

Everything goes to `%LOCALAPPDATA%\EquationAddIn`. To remove it all, run `uninstall.bat` from that folder.

**Requirements:** Windows 10/11 and Microsoft Word desktop 2016 or later (Microsoft 365 works).

## How it works

```
picture ──► pix2tex (local AI model, GPU)  ─┐
        └─► pix2text-mfr-1.5 (local AI, CPU) ┴─► cross-check:
              both readings are the same equation?  ── yes ─► accept
              no: pix2text-mfr confident (≥ 0.95)?  ── yes ─► accept its reading
              no: pix2tex confident (≥ 0.99)?       ── yes ─► accept its reading
              no ──► keep picture + review comment
     accepted LaTeX ──► cleanup ──► MathML ──► OMML (MML2OMML.XSL from Office) ──► Word
```

Both models are small, specialised AI models that only read equations. Everything runs on your PC, with no internet after install.

| | pix2tex | pix2text-mfr-1.5 |
|---|---|---|
| Architecture | ViT + Transformer | TrOCR (vision encoder–decoder), ONNX |
| Size | ~115 MB | ~115 MB |
| Licence | MIT | MIT |
| Reads correctly, alone (460 test images) | 312 | 429 |

**Why the cross-check:** across both benchmarks the two models agreed on 300 images and were wrong on only 3 of them. When they disagree, pix2text-mfr is right far more often. "The same equation" is decided by comparing normalised MathML (`server/compare.py`), so harmless spelling differences such as `{\frac{a}{b}}` vs `\frac{a}{b}` count as agreement. Set `EQ_CROSSCHECK=0` for the faster v1.2 behaviour, where the second model only sees images pix2tex is unsure about.

**Native Word objects** (`server/omml_fix.py`): Microsoft's MML2OMML.XSL gets several structures wrong. They are rewritten after conversion:

| LaTeX | XSL alone gave | Now |
|---|---|---|
| `\vec`, `\overrightarrow`, `\dot`, `\ddot`, `\tilde`, `\widehat` | a limit above (`m:limUpp`) | accent (`m:acc`, U+20D7/0307/0308/0303/0302) |
| `\bar`, `\overline` | a "―" accent | bar (`m:bar`) |
| `\underbrace`, `\overbrace` | the brace as a raw character | group character (`m:groupChr`) |
| `\binom` | small parentheses as text | stretching delimiter (`m:d`) around a no-bar fraction |
| column vectors, `pmatrix`/`bmatrix`/`Bmatrix` | equation array (`m:eqArr`) or a fraction | matrix (`m:m`) in `m:d` |
| `cases`, `\left(\frac{}{}\right)` | non-stretching `{` and `( )` | `m:d` |
| `\lim_{x\to0}`, `\max_i` | subscript beside an italic "lim" | upright operator with the limit underneath |
| `\operatorname{rank}` | italic `rankA` | upright `rank A` |

**Standing rule:** anything that still can't be shown correctly (an accent left as a superscript, a raw brace character, an unconverted `\command`) is **refused**. The picture stays, and the review comment says why. Nothing broken is inserted.

Run `.venv\Scripts\python tests\test_omml.py` for 53 structure tests covering every case above, `%` in every position, bold, script and blackboard letters, and the classes that must not regress.

**Cleanup** (`core.tidy`) fixes model output that would render badly in Word:
- tokenised spacing (`\mathbf { F }` loses its bold in conversion);
- `\underset{}{\overset{}{\sum}}`, rewritten as a real n-ary sum;
- one-cell matrices, empty sub/superscripts, and `\operatorname{sin}`, rewritten as `\sin`;
- the meaningless sequences both models produce for `%`.

- **On demand, no idle cost:** nothing runs while Word is just open. Clicking **Convert equation images** starts the local server (FastAPI on `127.0.0.1:8765`) and loads the models. The server shuts itself down after 5 idle minutes (`EQ_IDLE_EXIT`, in seconds) and frees all RAM and VRAM; it is also stopped when Word closes.
- **Confidence** is each model's own token confidence (the geometric mean of per-token probabilities); pix2tex also tries 3 scales and keeps its most confident reading. Pictures are never compared, because the rendered font never matches the source image.
  - On non-equation pictures (photo, diagram, a line of text) pix2text-mfr scored 0.30–0.94, below its 0.95 threshold (`EQ_SECOND_THRESHOLD`). So those are flagged rather than turned into "equations".
  - Set `EQ_SECOND=0` to use pix2tex only.
  - Flagged pictures stay in place, with a Word **comment** giving the reason and the guessed LaTeX.
  - Every image is OCR'd; there is no "is this an equation?" pre-filter. Each decision is logged to `ocr_log.jsonl` so a filter can later be built from real data.
- **Command-line version** (no Word needed):
  `%LOCALAPPDATA%\EquationAddIn\.venv\Scripts\python %LOCALAPPDATA%\EquationAddIn\convert.py input.docx output.docx [--no-verify]`
  `--no-verify` converts every readable image, which measures raw OCR accuracy separately from the verification step.

## Accuracy

Two benchmarks, scored with `bench.py` (MathML-normalised comparison):

1. **`sample_100.docx`**: 100 equations (25 simple, 30 medium, 25 electrical, 20 hard), with a hand-made ground truth in `samples/ground_truth_manual.json`.
2. **`samples/physics_bench.docx`**: 60 physics and electrical equations, each rendered in 3 fonts (Computer Modern like KaTeX/MathJax, STIX like Times, DejaVu Sans) at 300 and 150 dpi, so 360 images with exact ground truth. Rebuild it with `samples/make_physics.py`.

| Pipeline | 100-equation set | 360 physics/electrical | Time per image (RTX 3060 laptop) |
|---|---|---|---|
| v1.1 – pix2tex only | 75 correct, 2 wrong, 23 review | – | ~3.5 s in Word |
| v1.2 – pix2tex, then pix2text-mfr if unsure | 96 correct, 4 wrong | 292 correct (81%), 56 wrong, 12 review | ~1.5–2.1 s |
| **v1.3 – cross-check + cleanup (default)** | **100 correct, 0 wrong** | **329 correct (91%), 24 wrong, 7 review** | **~1.6–2.7 s** |

Physics/electrical v1.3 readings by font: Computer Modern 116/120, STIX 109/120, DejaVu Sans 107/120. The main remaining errors are inherent to the fonts: `I` vs `l` in sans-serif, `f` read as `/` in STIX, and `ε` vs `ϵ`.

```powershell
.venv\Scripts\python bench.py samples\physics_bench.docx samples\ground_truth_physics.json --rerun
.venv\Scripts\python samples\collect_scores.py BENCH.docx TRUTH.json xp.json      # both readings per image
.venv\Scripts\python samples\analyse_scores.py xp.json=TRUTH.json                # compare decision rules
```

Models also evaluated: PP-FormulaNet_plus (PaddleOCR). It has strong published scores, but it needs PaddlePaddle (over 1 GB, from a download server that was unreliable during testing) and extra dependencies, so it was not adopted.

**Known limitations**
- An OCR misread of `%` that is itself a valid expression can't be caught, for example `5\%` read as `5V_0`, which could genuinely mean 5·V₀. Only the meaningless misreads (`\lnot_0`, `\mathcal{Y}_0`) are corrected.
- Parentheses around more than one object, such as `\left(1+\frac{1}{n}\right)`, don't stretch in Word.
- `I`/`l`, `f`/`/` and `ε`/`ϵ` are hard to tell apart in some fonts.

Works best on clean digital documents (for example AI-generated ones) with LaTeX-style equation images. Scans and photos aren't supported yet.

## Build from source

Needs Visual Studio 2022 with the **Office/SharePoint development** workload.

```powershell
.\build.ps1 -Version 1.0.0     # -> dist\EquationAddIn-1.0.0.zip
```

The first build creates a self-signed key `EquationAddIn_Key.pfx` for signing the manifests. It is git-ignored and never committed.

For development without installing, point the add-in at your checkout with the `EQUATION_ADDIN_HOME` environment variable, and create `.venv` there as `installer\install.ps1` does.

## Credits

[pix2tex / LaTeX-OCR](https://github.com/lukas-blecher/LaTeX-OCR) (MIT) and [latex2mathml](https://github.com/roniemartinez/latex2mathml) (MIT). Microsoft's `MML2OMML.XSL` is loaded from your Office installation and isn't redistributed.

License: MIT
