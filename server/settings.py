"""Settings from config.json, overridable by environment variables for testing.

EQ_ENGINES="pix2tex,pix2text_mfr"   which engines run, in order of trust
EQ_THRESHOLD_<NAME>=0.97            confidence an engine needs to win a disagreement alone
EQ_CROSSCHECK=0                     only the first engine reads, the others only when it is unsure
EQ_IDLE_EXIT=300                    seconds without requests before the server exits
"""
import json
import os

SERVER_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(SERVER_DIR)
MODELS_DIR = os.environ.get("EQ_MODELS", os.path.join(ROOT, "models"))
LOG_PATH = os.environ.get("EQ_LOG", os.path.join(ROOT, "ocr_log.jsonl"))

with open(os.path.join(SERVER_DIR, "config.json"), encoding="utf-8") as f:
    _config = json.load(f)

_legacy = {"PIX2TEX": "EQ_THRESHOLD", "PIX2TEXT_MFR": "EQ_SECOND_THRESHOLD"}  # names used before v1.6
ENGINES = [(e["name"], float(os.environ.get(f"EQ_THRESHOLD_{e['name'].upper()}",
                                            os.environ.get(_legacy.get(e["name"].upper(), ""), e["threshold"]))))
           for e in _config["engines"]]
if os.environ.get("EQ_SECOND") == "0":
    ENGINES = [e for e in ENGINES if e[0] == "pix2tex"]
if os.environ.get("EQ_ENGINES"):
    wanted = [n.strip() for n in os.environ["EQ_ENGINES"].split(",") if n.strip()]
    known = dict(ENGINES)
    ENGINES = [(n, known.get(n, 0.95)) for n in wanted]
CROSSCHECK = os.environ.get("EQ_CROSSCHECK", "1" if _config.get("cross_check", True) else "0") != "0"
IDLE_EXIT = float(os.environ.get("EQ_IDLE_EXIT", _config.get("idle_exit_seconds", 120)))
