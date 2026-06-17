# Import du SDK Google GenAI pour utiliser Gemini
from google import genai
# Import des types nécessaires pour envoyer une image correctement au modèle
from google.genai import types
# Import de json pour convertir la réponse texte du modèle en dictionnaire Python
import json
# Import de la clé API Gemini depuis ta configuration
from app.config import GEMINI_API_KEY
# Import du prompt système qui décrit la tâche du modèle
from app.prompts import SYSTEM_PROMPT

# Création du client Gemini avec authentification
client = genai.Client(api_key=GEMINI_API_KEY)

   
    #Fonction principale qui :
    #- envoie une image à Gemini
    #- récupère une réponse structurée en JSON
    #- valide la réponse
    #- retourne les données + les statistiques d'usage
    
def run_gemini(image_bytes):
    
    # Configuration de la requête :
    # on force Gemini à répondre en JSON
    config = types.GenerateContentConfig(
        response_mime_type="application/json"
    )
     # Transformation des bytes de l'image en objet compatible Gemini
    image_part = types.Part.from_bytes(
        data=image_bytes,
        mime_type="image/jpeg"
    )
    # Appel du modèle Gemini avec :
    # - le prompt système
    # - l'image
    # - la configuration JSON
    response = client.models.generate_content(
        model="gemini-2.5-flash",
        contents=[SYSTEM_PROMPT, image_part],
        config=config
    )

    # Extraction du texte brut retourné par Gemini
    raw_text = response.text
    print(" GEMINI RAW OUTPUT:\n", raw_text)

    #  parsing strict
    try:
        output_data = json.loads(raw_text)

        #  validation minimale
        required_keys = [
            "nom_produit",
            "categorie",
            "description_marketing",
            "description_detaillee",
            
        ]
# Vérification que tous les champs sont présents
        for key in required_keys:
            if key not in output_data:
                raise ValueError(f"Missing key: {key}")

    except Exception as e:
        print(" JSON ERROR:", e)

        output_data = {
            "nom_produit": "Erreur",
            "categorie": "Inconnue",
            "description_marketing": "Erreur de génération IA",
            "description_detaillee": {
                "resume": "",
                "details_techniques": "",
                "caracteristiques": {}
            }
        }

    #  usage tokens
    usage = {}
    if hasattr(response, "usage_metadata") and response.usage_metadata:
        usage = {
            "prompt_token_count": response.usage_metadata.prompt_token_count,
            "candidates_token_count": response.usage_metadata.candidates_token_count,
            "total_token_count": response.usage_metadata.total_token_count
        }

    return output_data, usage