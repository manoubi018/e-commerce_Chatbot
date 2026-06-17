# app/llms/openai_client.py

from openai import OpenAI
import json
from app.config import OPENAI_API_KEY
from app.prompts import SYSTEM_PROMPT

# Initialisation du client OpenAI
client = OpenAI(api_key=OPENAI_API_KEY)


def run_openai(image_b64: str):
    """
    Envoie une image encodée en base64 à OpenAI (GPT-4o-mini vision),
    et retourne un JSON structuré + usage tokens.
    """

    try:
        # Appel du modèle multimodal (vision)
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
                            "text": "Analyse cette image et retourne uniquement le JSON demandé."
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
            temperature=0.2
        )

        raw_text = response.choices[0].message.content

        # Parsing JSON strict
        try:
            output_data = json.loads(raw_text)

        except Exception:
            output_data = {
                "nom_produit": "Erreur",
                "categorie": "Inconnue",
                "description_marketing": "Erreur de format",
                "description_detaillee": {
                    "resume": "",
                    "details_techniques": "",
                    "caracteristiques": {}
                }
            }

        # Tokens / usage
        usage = {}
        if response.usage:
            usage = {
                "prompt_tokens": response.usage.prompt_tokens,
                "completion_tokens": response.usage.completion_tokens,
                "total_tokens": response.usage.total_tokens
            }

        return output_data, usage

    except Exception as e:
        # fallback global (sécurité backend)
        return {
            "nom_produit": "Erreur API",
            "categorie": "Inconnue",
            "description_marketing": "Erreur OpenAI",
            "description_detaillee": {
                "resume": "",
                "details_techniques": "",
                "caracteristiques": {}
            }
        }, {
            "error": str(e)
        }