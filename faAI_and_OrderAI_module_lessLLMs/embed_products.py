"""Indexation vectorielle des produits pour la suggestion sémantique (pgvector).

Calcule les embeddings des produits en LOT (batch) et les stocke dans la colonne
vector `products.embedding`. Conçu pour passer à l'échelle (milliers de produits,
plusieurs entreprises) : l'API d'embeddings est appelée par paquets, et par
défaut seuls les produits NON encore indexés sont traités (incrémental).

Prérequis SQL (une seule fois, éditeur SQL Supabase) :

    -- si tu avais créé une colonne jsonb à l'étape précédente, supprime-la :
    ALTER TABLE products DROP COLUMN IF EXISTS embedding;

    CREATE EXTENSION IF NOT EXISTS vector;
    ALTER TABLE products ADD COLUMN embedding vector(1536);
    CREATE INDEX IF NOT EXISTS products_embedding_idx
        ON products USING hnsw (embedding vector_cosine_ops);

    -- fonction de recherche par similarité (utilisée par le chatbot) :
    CREATE OR REPLACE FUNCTION match_products(
        query_embedding text,
        match_count int DEFAULT 6,
        similarity_threshold float DEFAULT 0.42
    )
    RETURNS TABLE (
        id bigint, nom text, prix numeric, description text, image text,
        stock int, business_id bigint, business_name text, similarity float
    )
    LANGUAGE sql STABLE AS $$
        SELECT
            p.id::bigint, p.nom::text, p.prix::numeric, p.description::text,
            p.image::text, p.stock::int, p.business_id::bigint,
            b.name::text AS business_name,
            (1 - (p.embedding <=> query_embedding::vector))::float AS similarity
        FROM products p
        LEFT JOIN businesses b ON b.id = p.business_id
        WHERE p.active = true
          AND p.embedding IS NOT NULL
          AND (1 - (p.embedding <=> query_embedding::vector)) >= similarity_threshold
        ORDER BY p.embedding <=> query_embedding::vector
        LIMIT match_count;
    $$;

Usage :
    python embed_products.py          # indexe seulement les produits non indexés
    python embed_products.py --all    # réindexe TOUT le catalogue

Réindexer un produit modifié : mets son embedding à NULL puis relance le script :
    UPDATE products SET embedding = NULL WHERE id = <id>;
"""

import sys

from services import supabase, embeddings

BATCH = 100  # nombre de produits par appel d'embeddings


def _vec_to_str(vec):
    return "[" + ",".join(f"{x:.8f}" for x in vec) + "]"


def main():
    reindex_all = "--all" in sys.argv

    query = supabase.table("products").select("id, nom, description")
    if not reindex_all:
        query = query.is_("embedding", "null")

    products = query.execute().data or []

    if not products:
        print("Rien à indexer (tout est déjà indexé). Utilise --all pour tout refaire.")
        return

    print(f"{len(products)} produit(s) à indexer (batch de {BATCH})...")

    for i in range(0, len(products), BATCH):
        chunk = products[i:i + BATCH]
        textes = [
            f"{p.get('nom', '')}. {p.get('description') or ''}".strip()
            for p in chunk
        ]

        # un seul appel d'embeddings pour tout le paquet
        vecteurs = embeddings.embed_documents(textes)

        for p, v in zip(chunk, vecteurs):
            supabase.table("products") \
                .update({"embedding": _vec_to_str(v)}) \
                .eq("id", p["id"]) \
                .execute()

        print(f"  {min(i + BATCH, len(products))}/{len(products)}")

    print("Indexation terminée.")


if __name__ == "__main__":
    main()
