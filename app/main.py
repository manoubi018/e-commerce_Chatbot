from fastapi import FastAPI, UploadFile, File
import base64
import time
import uuid

from app.llms.gemini_client import run_gemini
#from app.llms.openai_client import run_openai
#from app.llms.claude_client import run_claude
from app.benchmark import benchmark

app = FastAPI()


def to_base64(data):
    return base64.b64encode(data).decode()


@app.post("/analyze")
async def analyze(file: UploadFile = File(...)):
    image_bytes = await file.read()
    image_b64 = to_base64(image_bytes)

    results = []

    # GEMINI
    start = time.perf_counter()
    g_out, g_usage = run_gemini(image_bytes)
    results.append(benchmark("gemini", "gemini-1.5-pro", g_out, start, g_usage))

    # OPENAI
    #start = time.perf_counter()
    #o_out, o_usage = run_openai(image_b64)
    #results.append(benchmark("openai", "gpt-4o-mini", o_out, start, o_usage))

    # CLAUDE
    #start = time.perf_counter()
    #c_out, c_usage = run_claude(image_b64)
    #results.append(benchmark("claude", "claude-3-opus", c_out, start, c_usage))

    best = results[0]["raw_output"]

    visible = {
        "categorie": best.get("categorie", ""),
        "nom_produit": best.get("nom_produit", ""),
        "description_marketing": best.get("description_marketing", "")
    }

    return {
        "visible": visible,
        "benchmarks": results
    }
