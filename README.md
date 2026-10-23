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
picture ──► stage 1: pix2tex (local AI model, GPU/CPU)
              confident (≥ 0.99)? ── yes ──────────────────────────┐
              no ──► stage 2: pix2text-mfr-1.5 (local AI model, CPU) │
                       confident (≥ 0.95)? ── yes ──────────────────┤
                       no ──► keep picture + review comment          ▼
                                     LaTeX ──► MathML ──► OMML (MML2OMML.XSL from Office) ──► Word
```

Both models are small, specialised AI models that only read equations. Everything runs on your PC, with no internet after install.

| | pix2tex (stage 1) | pix2text-mfr-1.5 (stage 2) |
|---|---|---|
| Architecture | ViT + Transformer | TrOCR (vision encoder–decoder), ONNX |
| Size | ~115 MB | ~115 MB |
| Licence | MIT | MIT |
| Used for | every image | only images stage 1 is unsure about (~23% on the benchmark) |

- **On demand, no idle cost:** nothing runs while Word is just open. Clicking **Convert equation images** starts the local server (FastAPI on `127.0.0.1:8765`) and loads the models. The server shuts itself down after 5 idle minutes (`EQ_IDLE_EXIT`, in seconds) and frees all RAM and VRAM; it is also stopped when Word closes.
- **Verification** uses each model's own token confidence (the geometric mean of per-token probabilities). Stage 1 also tries 3 scales and keeps the most confident reading. Pictures are never compared, because the rendered font never matches the source image.
  - Stage 2 has its own threshold, `EQ_SECOND_THRESHOLD` (default 0.95). On non-equation pictures (photo, diagram, a line of text) it scored 0.30–0.94, so those are flagged rather than turned into "equations".
  - Set `EQ_SECOND=0` to use pix2tex only.
  - Flagged pictures stay in place, with a Word **comment** giving the reason and the guessed LaTeX.
  - Every image is OCR'd; there is no "is this an equation?" pre-filter. Each decision is logged to `ocr_log.jsonl` so a filter can later be built from real data.
- **Command-line version** (no Word needed):
  `%LOCALAPPDATA%\EquationAddIn\.venv\Scripts\python %LOCALAPPDATA%\EquationAddIn\convert.py input.docx output.docx [--no-verify]`
  `--no-verify` converts every readable image, which measures raw OCR accuracy separately from the verification step.

## Accuracy

These numbers come from the 100-equation benchmark (`sample_100.docx`: 25 simple, 30 medium, 25 electrical, 20 hard), scored with `bench.py` against `samples/ground_truth_manual.json`, a hand transcription of the images:

| Pipeline | Replaced correctly | Replaced wrongly | Sent to review | Time for 100 (RTX 3060 laptop) |
|---|---|---|---|---|
| v1.1 – pix2tex only, threshold 0.99 | 75 | 2 | 23 | ~6 min in Word |
| **v1.2 – pix2tex + pix2text-mfr (default)** | **96** | **4** | **0** | **~3.8 min** |

On the 23 images pix2tex was unsure about, pix2text-mfr-1.5 read 21 correctly, while pix2tex got 5. The remaining errors are a dropped `%` (EQ-068), a misread `c_e` (EQ-071), a square root that doesn't cover the whole sum (EQ-047) and empty accents around a matrix (EQ-083, removed by the LaTeX cleanup).

Models also evaluated: PP-FormulaNet_plus (PaddleOCR). It has strong published scores, but it needs PaddlePaddle (over 1 GB, from a download server that was unreliable during testing) and extra dependencies, so it was not adopted.

```powershell
.venv\Scripts\python bench.py sample_100.docx ground_truth.json   # accuracy, per-category, threshold sweep
```

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
