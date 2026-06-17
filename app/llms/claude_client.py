# Import du SDK OpenAI
from openai import OpenAI

# Import JSON pour parser la réponse du modèle
import json

# Import clé API
from app.config import OPENAI_API_KEY

# Import du prompt système
from app.prompts import SYSTEM_PROMPT

# Création du client OpenAI
client = OpenAI(api_key=OPENAI_API_KEY)


def run_openai(image_b64):
    """
    Fonction principale :
    - envoie une image à GPT-4o
    - force une réponse JSON structurée
    - parse la réponse
    - retourne données + usage tokens
    """

    # Appel du modèle vision OpenAI
    response = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {
                "role": "system",
                "content": SYSTEM_PROMPT
            },
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": "Analyse cette image et retourne le JSON demandé."
                    },
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:image/jpeg;base64,{image_b64}"
                        }
                    }
                ]
            }
        ],
        response_format={ "type": "json_object" }
    )

    # Récupération du texte JSON brut
    raw_text = response.choices[0].message.content

    print("OPENAI RAW OUTPUT:\n", raw_text)

    # Parsing JSON strict
    try:
        output_data = json.loads(raw_text)

        required_keys = [
            "nom_produit",
            "categorie",
            "description_marketing",
            "description_detaillee"
        ]

        for key in required_keys:
            if key not in output_data:
                raise ValueError(f"Missing key: {key}")

    except Exception as e:
        print("JSON ERROR:", e)

        output_data = {
            "nom_produit": "Erreur",
            "categorie": "Inconnue",
            "description_marketing": "Erreur génération IA",
            "description_detaillee": {
                "resume": "",
                "details_techniques": "",
                "caracteristiques": {}
            }
        }

    # Usage tokens (si dispo)
    usage = {}
    if response.usage:
        usage = {
            "prompt_token_count": response.usage.prompt_tokens,
            "candidates_token_count": response.usage.completion_tokens,
            "total_token_count": response.usage.total_tokens
        }

    return output_data, usage