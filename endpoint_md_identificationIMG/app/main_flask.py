from flask import Flask, request, jsonify, render_template
import base64
import time
import os

from app.llms.gemini_client import run_gemini
from app.benchmark import benchmark

app = Flask(__name__, template_folder=os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "templates"))


def to_base64(data):
    return base64.b64encode(data).decode()


@app.get("/")
def home():
    return render_template("index.html")


@app.post("/analyze")
def analyze():
    if "file" not in request.files:
        return jsonify({"error": "No file provided"}), 400

    file = request.files["file"]
    image_bytes = file.read()

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

    best = g_out

    # Partie visible (frontend)
    visible = {
        "nom_produit": best.get("nom_produit", ""),
        "categorie": best.get("categorie", ""),
        "description_marketing": best.get("description_marketing", "")
    }

    # description_detaillee volontairement cachée
    internal_json = best.get("description_detaillee", {})

    return jsonify({
        "visible": visible,
        "internal": internal_json,
        "benchmarks": results
    })


if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=5000)
