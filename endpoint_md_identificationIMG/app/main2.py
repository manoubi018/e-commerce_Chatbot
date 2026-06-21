from fastapi import FastAPI, UploadFile, File, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
import base64
import time
import os

from app.llms.gemini_client import run_gemini
from app.benchmark import benchmark

app = FastAPI()

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
templates = Jinja2Templates(directory=os.path.join(BASE_DIR, "templates"))


def to_base64(data):
    return base64.b64encode(data).decode()


@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={}
    )


@app.post("/analyze")
async def analyze(file: UploadFile = File(...)):
    image_bytes = await file.read()

    results = []

    # GEMINI
    start = time.perf_counter()
    g_out, g_usage = run_gemini(image_bytes)

    results.append(
        benchmark(
            provider="gemini",
            model="gemini-2.5-flash",
            output=g_out,
            start_time=start,
            usage=g_usage
        )
    )

    #  récupération directe du JSON IA
    best = g_out

    # ✅ PARTIE VISIBLE (frontend)
    visible = {
        "nom_produit": best.get("nom_produit", ""),
        "categorie": best.get("categorie", ""),
        "description_marketing": best.get("description_marketing", "")
    }

    #  description_detaillee volontairement cachée
    internal_json = best.get("description_detaillee", {})

    return {
        "visible": visible,
        "internal": internal_json,
        "benchmarks": results
    }