import os
from dotenv import load_dotenv

load_dotenv()

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")

CATEGORIES = [
    "Électronique",
    "Vêtements",
    "Chaussures",
    "Beauté",
    "Maison",
    "Sport",
    "Alimentation"
]
