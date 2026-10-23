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
| OCR model weights | ~115 MB | |

Everything goes to `%LOCALAPPDATA%\EquationAddIn`. To remove it all, run `uninstall.bat` from that folder.

**Requirements:** Windows 10/11 and Microsoft Word desktop 2016 or later (Microsoft 365 works).

## How it works

```
picture ──► pix2tex (LaTeX-OCR, local GPU/CPU) ──► LaTeX ──► latex2mathml ──► MathML
        ──► MML2OMML.XSL (ships with Office) ──► OMML ──► Range.InsertXML in Word
```

- **Local server**: FastAPI on `127.0.0.1:8765`, endpoint `POST /ocr`. The add-in starts it in the background when Word opens.
- **Verification** uses the OCR model's own token confidence (the geometric mean of the per-token probabilities, best of 3 scales). It does not compare pictures, because the rendered font never matches the source image.
  - Confidence ≥ `EQ_THRESHOLD` (default 0.99): the picture is replaced with the equation.
  - Below that, or if the LaTeX can't be converted: the picture stays, and a Word **comment** is added with the reason and the guessed LaTeX.
  - Every image is sent to the OCR; there is no "is this an equation?" pre-filter. Each decision is logged to `ocr_log.jsonl` (with the old pre-filter's statistics) so a new filter can be built from real data.
- **Command-line version** (no Word needed):
  `%LOCALAPPDATA%\EquationAddIn\.venv\Scripts\python %LOCALAPPDATA%\EquationAddIn\convert.py input.docx output.docx [--no-verify]`
  `--no-verify` converts every readable image, which measures raw OCR accuracy separately from the verification step.

## Accuracy

These numbers come from the 100-equation benchmark (`sample_100.docx`: 25 simple, 30 medium, 25 electrical, 20 hard), scored with `bench.py` against `samples/ground_truth_manual.json`, a hand transcription of the images:

| Threshold | Replaced | Wrong among replaced | Precision | Correct but sent to review |
|---|---|---|---|---|
| no verification | 99 | 19 | 80% | 0 |
| 0.97 | 88 | 9 | 90% | 1 |
| **0.99 (default)** | **77** | **2** | **97%** | **5** |

Raw OCR accuracy is 80/100 (simple 20/25, medium 25/30, electrical 20/25, hard 15/20). The weakest cases are matrices, piecewise functions and arrow accents.

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
