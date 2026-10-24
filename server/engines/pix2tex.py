"""pix2tex (LaTeX-OCR, MIT): ViT + Transformer on the GPU. Reads at three scales, keeps the most confident."""
import math
import os
import types

from PIL import Image, ImageOps

NAME = "pix2tex"
MULTI_PASS = True

_model = None
_step_probs = []  # top-token probability of each decoding step, filled by a forward hook


def load():
    global _model
    if _model is None:
        import torch
        import pix2tex.cli
        from pix2tex.cli import LatexOCR
        # pix2tex copies every reading to the Windows clipboard, overwriting what the user copied
        # and flooding the clipboard history. Never touch the user's clipboard.
        pix2tex.cli.clipboard = types.SimpleNamespace(copy=lambda *args, **kwargs: None)
        if os.environ.get("EQ_TORCH_THREADS"):
            torch.set_num_threads(int(os.environ["EQ_TORCH_THREADS"]))
        _model = LatexOCR()
        _model.args.temperature = 1e-8  # greedy decoding: same image -> same LaTeX
        # With greedy decoding the chosen token is the arg-max, so its probability is the max softmax.
        _model.model.decoder.net.register_forward_hook(
            lambda mod, inp, out: _step_probs.append(torch.softmax(out[:, -1, :].float(), -1).max().item()))
    return _model


def read_once(im):
    _step_probs.clear()
    latex = load()(im).replace(r"\!", "")
    probs = _step_probs[:] or [0.0]
    return latex, math.exp(sum(math.log(max(p, 1e-9)) for p in probs) / len(probs))


def read(rgb, good_enough=lambda latex: False):
    """Three scales; stop when confident or when good_enough(latex)."""
    best = ("", -1.0)
    for h in (None, 48, 64):
        im = rgb if h is None else ImageOps.expand(
            rgb.resize((max(8, round(rgb.width * h / rgb.height)), h), Image.LANCZOS), 8, "white")
        latex, conf = read_once(im)
        if conf > best[1]:
            best = (latex, conf)
        if best[1] >= 0.99 or good_enough(latex):
            break
    return best
