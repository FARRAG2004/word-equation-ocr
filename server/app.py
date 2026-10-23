"""Local OCR server: uvicorn app:app --host 127.0.0.1 --port 8765

Started on demand by the Word add-in; exits by itself after EQ_IDLE_EXIT seconds without
requests (default 120), so the models only occupy RAM/VRAM while the tool is in use.
"""
import os
import threading
import time

from fastapi import FastAPI, File, UploadFile

import core

IDLE_EXIT = float(os.environ.get("EQ_IDLE_EXIT", "120"))
app = FastAPI()
last_used = time.monotonic()
busy = 0


def idle_watchdog():
    while True:
        time.sleep(5)
        if IDLE_EXIT > 0 and not busy and time.monotonic() - last_used > IDLE_EXIT:
            os._exit(0)  # frees all model memory at once


@app.on_event("startup")
def warm():
    core.model()
    threading.Thread(target=idle_watchdog, daemon=True).start()


@app.get("/health")
def health():
    return {"ok": True}


@app.post("/ocr")
async def ocr(image: UploadFile = File(...), verify: bool = True):
    global last_used, busy
    busy += 1
    try:
        return core.process(await image.read(), verify=verify, source=image.filename or "")
    finally:
        busy -= 1
        last_used = time.monotonic()
