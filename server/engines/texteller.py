"""TexTeller 3 (Apache-2.0, ~300M parameters, trained on 80M formula images) on the GPU via transformers.

Preprocessing reproduces the texteller package (trim white border, grayscale, longest side 448,
normalise, pad to 448x448) without depending on that package or OpenCV.
"""
import math
import os

import numpy as np
from PIL import Image

from settings import MODELS_DIR

NAME = "texteller"
REPO = "OleehyO/TexTeller"
MODEL_DIR = os.environ.get("EQ_TEXTELLER_DIR", os.path.join(MODELS_DIR, "texteller"))
SIZE, MEAN, STD, MAX_TOKENS = 448, 0.9545467, 0.15394445, 1024

_model = _tok = _device = None


def load():
    global _model, _tok, _device
    if _model is None:
        import torch
        from transformers import RobertaTokenizerFast, VisionEncoderDecoderModel
        src = MODEL_DIR if os.path.exists(os.path.join(MODEL_DIR, "config.json")) else REPO
        _device = "cuda" if torch.cuda.is_available() else "cpu"
        _model = VisionEncoderDecoderModel.from_pretrained(src).to(_device).eval()
        _tok = RobertaTokenizerFast.from_pretrained(src)
        if src == REPO:  # first run without the installer: keep a local copy for offline use
            _model.save_pretrained(MODEL_DIR)
            _tok.save_pretrained(MODEL_DIR)
    return _model


def trim_white_border(rgb):
    a = np.asarray(rgb.convert("RGB")).astype(np.int16)
    corners = [tuple(a[0, 0]), tuple(a[0, -1]), tuple(a[-1, 0]), tuple(a[-1, -1])]
    bg = np.array(max(set(corners), key=corners.count), dtype=np.int16)
    ink = np.abs(a - bg).max(axis=2) > 15
    ys, xs = np.where(ink)
    if not len(ys):
        return rgb
    return rgb.crop((xs.min(), ys.min(), xs.max() + 1, ys.max() + 1))


def preprocess(rgb):
    import torch
    im = trim_white_border(rgb).convert("L")
    scale = min((SIZE - 1) / min(im.size), SIZE / max(im.size))
    im = im.resize((max(1, round(im.width * scale)), max(1, round(im.height * scale))), Image.BICUBIC)
    x = (np.asarray(im, dtype=np.float32) / 255.0 - MEAN) / STD
    canvas = np.zeros((SIZE, SIZE), dtype=np.float32)
    canvas[: x.shape[0], : x.shape[1]] = x
    return torch.from_numpy(canvas)[None, None]


def read(rgb):
    import torch
    model = load()
    with torch.no_grad():
        out = model.generate(preprocess(rgb).to(_device), max_new_tokens=MAX_TOKENS, num_beams=1, do_sample=False,
                             pad_token_id=_tok.pad_token_id, eos_token_id=_tok.eos_token_id,
                             bos_token_id=_tok.bos_token_id, output_scores=True, return_dict_in_generate=True)
    probs = [torch.softmax(s[0].float(), -1).max().item() for s in out.scores]
    conf = math.exp(sum(math.log(max(p, 1e-9)) for p in probs) / max(len(probs), 1))
    return _tok.batch_decode(out.sequences, skip_special_tokens=True)[0], conf
