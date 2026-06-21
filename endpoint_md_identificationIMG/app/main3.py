from fastapi import FastAPI, UploadFile, File, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

import base64
import time
import os
import json

from app.llms.gemini_client import run_gemini
from app.llms.openai_client import run_openai
from app.gemAiBenchmark import gemAibenchmark

app = FastAPI()

# dossier templates
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
templates = Jinja2Templates(directory=os.path.join(BASE_DIR, "templates"))


# conversion image -> base64 (OpenAI)
def to_base64(data):
    return base64.b64encode(data).decode()


# page HTML
@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="analyse_OpenGemini.html",
        context={}
    )


@app.post("/analyze")
async def analyze(file: UploadFile = File(...)):
    image_bytes = await file.read()

    results = []

    # =========================
    # GEMINI
    # =========================
    start = time.perf_counter()
    g_out, _ = run_gemini(image_bytes)

    results.append(
        gemAibenchmark(
            provider="gemini",
            model="gemini-2.5-flash",
            output=g_out,
            start_time=start
        )
    )

    # =========================
    # OPENAI
    # =========================
    start = time.perf_counter()
    image_b64 = to_base64(image_bytes)

    o_out, _ = run_openai(image_b64)

    results.append(
        gemAibenchmark(
            provider="openai",
            model="gpt-4o-mini",
            output=o_out,
            start_time=start
        )
    )

    # =========================
    # BEST (speed only)
    # =========================
    best_result = min(results, key=lambda x: x["latency"])
    best = best_result.get("output", {})
    best_model = best_result.get("provider", "")

    # =========================
    # FRONT SAFE
    # =========================
    visible = {
        "nom_produit": best.get("nom_produit", ""),
        "categorie": best.get("categorie", ""),
        "description_marketing": best.get("description_marketing", "")
    }

    internal_json = best.get("description_detaillee", {})

    return {
        "visible": visible,
        "internal": internal_json,
        "benchmarks": results,
        "best_model": best_model
    }