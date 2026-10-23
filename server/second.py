"""Second-opinion OCR model: pix2text-mfr-1.5 (TrOCR, MIT licence, 115 MB) run with onnxruntime.

Only used for images pix2tex is unsure about; loaded on first use, so it costs nothing otherwise.
Greedy decoding is done here (no optimum/transformers needed) so we also get token confidence.
"""
import math
import os

import numpy as np
from PIL import Image

REPO = "breezedeus/pix2text-mfr-1.5"
MODEL_DIR = os.environ.get("EQ_MFR_DIR", os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "models", "pix2text-mfr-1.5"))
BOS, EOS, MAX_TOKENS = 1, 2, 512

_enc = _dec = _tok = None


def model_dir():
    if os.path.exists(os.path.join(MODEL_DIR, "encoder_model.onnx")):
        return MODEL_DIR
    from huggingface_hub import snapshot_download  # first run without the installer
    return snapshot_download(REPO, local_dir=MODEL_DIR)


def load():
    global _enc, _dec, _tok
    if _enc is None:
        import onnxruntime as ort
        from tokenizers import Tokenizer
        d = model_dir()
        opts = ort.SessionOptions()
        opts.intra_op_num_threads = max(1, (os.cpu_count() or 4) // 2)  # leave the machine responsive
        _enc = ort.InferenceSession(os.path.join(d, "encoder_model.onnx"), opts, providers=["CPUExecutionProvider"])
        _dec = ort.InferenceSession(os.path.join(d, "decoder_model.onnx"), opts, providers=["CPUExecutionProvider"])
        _tok = Tokenizer.from_file(os.path.join(d, "tokenizer.json"))


def preprocess(img):
    """TrOCR/DeiT preprocessing: 384x384 bicubic, scale to [-1, 1], CHW."""
    x = np.asarray(img.convert("RGB").resize((384, 384), Image.BICUBIC), dtype=np.float32) / 255.0
    return ((x - 0.5) / 0.5).transpose(2, 0, 1)[None]


def read(img):
    """Return (latex, geometric-mean token probability)."""
    load()
    hidden = _enc.run(None, {"pixel_values": preprocess(img)})[0]
    ids, logp = [BOS], 0.0
    for _ in range(MAX_TOKENS):
        logits = _dec.run(None, {"input_ids": np.array([ids], dtype=np.int64), "encoder_hidden_states": hidden})[0][0, -1]
        logits = logits - logits.max()
        probs = np.exp(logits) / np.exp(logits).sum()
        nxt = int(probs.argmax())
        logp += math.log(max(float(probs[nxt]), 1e-9))
        ids.append(nxt)
        if nxt == EOS:
            break
    n = len(ids) - 1
    return _tok.decode(ids[1:], skip_special_tokens=True), math.exp(logp / max(n, 1))
