from langchain_openai import ChatOpenAI, OpenAIEmbeddings
from supabase import create_client
import os
from dotenv import load_dotenv

load_dotenv()

# =========================
# ENV
# =========================

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

# =========================
# LLM
# =========================

llm = ChatOpenAI(
    model="gpt-4o-mini",
    temperature=0,
    api_key=OPENAI_API_KEY
)

# modèle d'embeddings pour la suggestion sémantique de produits : très bon
# marché (~0,02 $/million de tokens), 1 seul appel par recherche
embeddings = OpenAIEmbeddings(
    model="text-embedding-3-small",
    api_key=OPENAI_API_KEY
)

# =========================
# SUPABASE
# =========================

supabase = create_client(
    SUPABASE_URL,
    SUPABASE_KEY
)