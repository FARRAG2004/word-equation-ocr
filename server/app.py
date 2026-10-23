"""Local OCR server: uvicorn app:app --host 127.0.0.1 --port 8765"""
from fastapi import FastAPI, File, UploadFile

import core

app = FastAPI()


@app.on_event("startup")
def warm():
    core.model()


@app.get("/health")
def health():
    return {"ok": True}


@app.post("/ocr")
async def ocr(image: UploadFile = File(...)):
    return core.process(await image.read())
