# SDK officiel Google Generative AI (ancienne version du SDK)
import google.generativeai as genai
import json  #  Ajout indispensable pour lire le JSON
# Clé API stockée dans la configuration de ton projet
from app.config import GEMINI_API_KEY
# Prompt système qui définit la tâche du modèle
from app.prompts import SYSTEM_PROMPT
# Initialisation du SDK avec ta clé API
genai.configure(api_key=GEMINI_API_KEY)
# Chargement du modèle Gemini (version texte + image)
model = genai.GenerativeModel("gemini-1.5-flash-latest")

def run_gemini(image_bytes):
    response = model.generate_content([
        SYSTEM_PROMPT,
        {
            "mime_type": "image/jpeg",
            "data": image_bytes
        }
    ])

    # Récupération sécurisée sous forme de dictionnaire pour le benchmark
    usage_obj = getattr(response, "usage_metadata", None)
    if usage_obj:
        usage = {
            "prompt_token_count": getattr(usage_obj, "prompt_token_count", 0),
            "candidates_token_count": getattr(usage_obj, "candidates_token_count", 0),
            "total_token_count": getattr(usage_obj, "total_token_count", 0)
        }
    else:
        usage = {}

    #  CONVERSION DU TEXTE EN DICTIONNAIRE POUR MAIN.PY
    try:
        output_data = json.loads(response.text)
    except Exception:
        # Si Gemini a renvoyé du texte au lieu de JSON, on crée une structure
        # par défaut pour éviter que main.py ne crashe en erreur 500
        output_data = {
            "categorie": "Inconnue",
            "nom_produit": "Erreur de format",
            "description_marketing": response.text
        }

    return output_data, usage  #  On renvoie output_data (le dictionnaire) au lieu de response.text
