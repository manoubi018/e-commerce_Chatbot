import re
from datetime import datetime
from typing import TypedDict, Optional

from langgraph.graph import StateGraph, END

from services import llm, supabase, embeddings
from utils import normalize
from prompt import (
    ROUTER_PROMPT, ORDER_ROUTER_PROMPT, ORDER_POSITION_PROMPT,
    FAQ_COLUMN_PROMPT, COMPANY_CONFIRM_PROMPT, REMOVE_CONFIRM_PROMPT,
    LAST_ITEM_ACTION_PROMPT, CANCEL_ORDER_CONFIRM_PROMPT, PRODUCT_SELECT_PROMPT,
    ORDER_DATE_FILTER_PROMPT, PRODUCT_SUGGESTION_PROMPT
)


# =========================
# STATE
# =========================

class State(TypedDict):
    question: str
    intent: str
    response: str
    company_id: Optional[int]
    company_name: Optional[str]
    cost: float
    order_id: Optional[int]
    user_id: Optional[str]
    session_business_id: Optional[int]
    pending_orders: Optional[list]
    pending_question: Optional[str]
    pending_companies: Optional[list]
    pending_removal: Optional[dict]
    pending_new_order: Optional[bool]
    awaiting_order_action: Optional[bool]
    pending_cancel: Optional[dict]
    pending_products: Optional[list]


# =========================
# ROUTER
# =========================

# Routeur hybride : des règles déterministes (gratuites, instantanées) tranchent
# les cas fréquents et sans ambiguïté ; on n'appelle le LLM que lorsque les
# règles ne sont pas sûres. Les mots-clés sont écrits SANS accents ET SANS
# ponctuation, car on les compare à une version nettoyée de la question
# (minuscules, accents retirés, tirets/apostrophes → espaces).

def _bare(text):
    """Normalise une phrase pour les comparaisons par mots-clés : minuscules,
    accents retirés, ponctuation → espaces, espaces multiples réduits."""
    q = normalize(text)
    q = re.sub(r"[^\w\s]", " ", q)
    return re.sub(r"\s+", " ", q).strip()


def _has_kw(text, keywords):
    """True si un des mots-clés apparaît comme MOT ENTIER (tolère le pluriel
    final '-s'). Évite les faux positifs de sous-chaîne : 'mail' ne matche pas
    'maillot', 'commande' ne matche pas 'recommande'."""
    return any(re.search(rf"\b{re.escape(kw)}s?\b", text) for kw in keywords)


# position en toutes lettres → numéro (pour "la deuxième", "le dernier"...)
_ORDINALS = {
    "premier": 1, "premiere": 1, "1er": 1, "1ere": 1, "1": 1,
    "deuxieme": 2, "seconde": 2, "second": 2, "2eme": 2, "2": 2,
    "troisieme": 3, "3eme": 3, "3": 3,
    "quatrieme": 4, "4eme": 4, "4": 4,
    "cinquieme": 5, "5eme": 5, "5": 5,
    "sixieme": 6, "septieme": 7, "huitieme": 8, "neuvieme": 9, "dixieme": 10,
}

_FAQ_KW = (
    "horaire", "adresse", "ou se trouve", "telephone", "numero", "contact",
    "email", "mail", "facebook", "instagram", "tiktok", "ouvert", "ferme",
    "retour", "remboursement", "rembourser", "localisation",
)

_ORDER_KW = (
    "commande", "commander", "ajoute", "ajouter", "retire", "retirer",
    "enleve", "enlever", "supprime", "supprimer", "confirme", "confirmer",
    "annule", "annuler", "livraison", "livrer", "livree", "montant total",
    "le total", "suivi", "statut", "panier",
)

_VIEW_KW = (
    "voir les produits", "voir vos produits", "vos produits", "quels produits",
    "quels sont les produits", "les offres", "que vendez", "vendez vous",
    "vous vendez", "catalogue", "montre les produits", "montre moi les produits",
    "affiche les produits", "affiche moi les produits", "liste des produits",
    "produits proposes", "produits disponibles", "les produits",
)

_COMPANY_SEARCH_KW = (
    "une entreprise", "des entreprises", "une boutique", "un magasin",
    "une societe", "cherche une entreprise",
)

_COMPANY_SELECT_KW = ("je choisis", "je selectionne")

_BARE_AFFIRM = {
    "oui", "ok", "okay", "daccord", "d accord", "bien sur", "oui merci",
    "cest bon", "c est bon", "vas y", "vasy", "carrement", "ouais",
    "oui bien sur", "continue", "ouep", "yes",
}

_BARE_NEGATE = {
    "non", "non merci", "recommencer", "recommence", "revenir",
    "autre domaine", "nan",
}


def _rule_based_intent(question):
    """Retourne un intent déterministe pour les cas clairs, ou None si c'est
    trop ambigu (→ on laissera le LLM décider). L'ordre reproduit les priorités
    de ROUTER_PROMPT : une question FAQ précise prime sur une confirmation."""

    # version nettoyée : accents retirés, ponctuation (tirets, apostrophes...)
    # remplacée par des espaces, espaces multiples réduits. Robuste aux
    # variantes "montre-moi" / "montre moi", "qu'est-ce" / "qu est ce".
    bare = _bare(question)

    if _has_kw(bare, _FAQ_KW):
        return "faq"
    if _has_kw(bare, _ORDER_KW):
        return "order"
    if _has_kw(bare, _VIEW_KW):
        return "view_products"
    if _has_kw(bare, _COMPANY_SEARCH_KW):
        return "company_search"
    if _has_kw(bare, _COMPANY_SELECT_KW):
        return "company_select"
    if bare in _BARE_AFFIRM:
        return "continue_faq"
    if bare in _BARE_NEGATE:
        return "restart"

    # cas ambigu (ex: "je voudrais un liqueur", un domaine seul...) → LLM
    return None


def orchestrator(state):

    # 1) tentative déterministe, gratuite et instantanée
    intent = _rule_based_intent(state["question"])

    # 2) fallback LLM seulement si les règles ne tranchent pas. Le LLM a besoin
    #    de savoir si une entreprise est déjà sélectionnée ("je voudrais un
    #    liqueur" = chercher un PRODUIT si oui, une ENTREPRISE sinon).
    if intent is None:
        company_selected = "oui" if state.get("company_id") else "non"
        prompt = ROUTER_PROMPT.format(
            question=state["question"],
            company_selected=company_selected
        )
        intent = llm.invoke(prompt).content.strip().lower()

    state["intent"] = intent

    return state


# =========================
# COMPANY SEARCH
# =========================

def company_search(state):

    extract_prompt = (
        "Extrais uniquement le secteur d'activité ou le domaine mentionné dans cette phrase.\n"
        "Réponds avec le mot-clé en français ET sa traduction en anglais, séparés par une "
        "barre verticale, sans rien d'autre. Si les deux mots sont identiques ou qu'il n'y a "
        "pas de traduction évidente, répète le même mot des deux côtés.\n"
        "Format : mot_francais|mot_anglais\n"
        "Exemples :\n"
        "- 'je cherche une boulangerie sympa' → boulangerie|bakery\n"
        "- 'j aimerais trouver une entreprise en informatique' → informatique|informatique\n"
        "- 'je cherche une entreprise spécialisée en pâtisserie' → patisserie|pastry\n"
        "- 'une entreprise en musique' → musique|music\n"
        f"Phrase : {state['question']}"
    )
    extracted = llm.invoke(extract_prompt).content.strip().lower()
    parts = extracted.split("|")
    key_fr = normalize(parts[0].strip())
    key_en = normalize(parts[1].strip()) if len(parts) > 1 else key_fr

    result = supabase.table("businesses") \
        .select("id, name") \
        .or_(f"domain.ilike.%{key_fr}%,domain.ilike.%{key_en}%") \
        .execute()

    if result.data:
        names = [b["name"] for b in result.data]
        state["pending_companies"] = [{"id": b["id"], "name": b["name"]} for b in result.data]
        state["response"] = "Entreprises trouvées :\n" + "\n".join(f"- {n}" for n in names)
    else:
        state["pending_companies"] = None
        state["response"] = "Aucune entreprise trouvée."

    return state


# =========================
# COMPANY SELECT
# =========================

def _resolve_company_choice(question, pending):
    """Détermine, sans LLM, quelle entreprise de la liste l'utilisateur choisit :
    par numéro, par position littérale ("la deuxième"), par nom (même partiel),
    ou par accord simple s'il n'y a qu'une entreprise. Retourne le numéro (1..n)
    ou None si ce n'est pas clair (→ fallback LLM)."""

    b = _bare(question)

    # 1) numéro explicite
    m = re.search(r"\d+", b)
    if m:
        return int(m.group())

    # 2) position en toutes lettres
    for word, pos in _ORDINALS.items():
        if not word.isdigit() and re.search(rf"\b{word}\b", b):
            return pos
    if re.search(r"\bderni", b):  # "le dernier", "la dernière"
        return len(pending)

    # 3) nom d'entreprise (complet ou mot significatif du nom)
    for i, c in enumerate(pending, start=1):
        name = _bare(c["name"])
        if name and name in b:
            return i
        if any(w in b.split() for w in name.split() if len(w) >= 3):
            return i

    # 4) une seule entreprise proposée + confirmation/désignation → on la prend
    #    sans LLM ("oui", "je choisis cette entreprise", "je la prends"...)
    if len(pending) == 1:
        if b in _BARE_AFFIRM or _has_kw(b, (
            "choisi", "choisis", "choisir", "prend", "prends", "prendre",
            "cette", "celle", "celui", "ok", "accord", "va pour", "ce sera",
            "je la veux", "elle",
        )):
            return 1

    return None


def company_select(state):

    pending = state.get("pending_companies")

    if pending:
        # 1) résolution déterministe (numéro, position littérale, nom)
        position = _resolve_company_choice(state["question"], pending)

        # 2) fallback LLM seulement si les règles ne tranchent pas
        if position is None:
            companies_list = "\n".join(
                f"{i}. {c['name']}" for i, c in enumerate(pending, start=1)
            )
            prompt = COMPANY_CONFIRM_PROMPT.format(
                companies_list=companies_list,
                question=state["question"]
            )
            result = llm.invoke(prompt).content.strip().lower()
            match = re.search(r"\d+", result)
            position = int(match.group()) if match else None

        if position is None:
            state["response"] = (
                "Je n'ai pas compris quelle entreprise vous souhaitez choisir. "
                "Merci de préciser son nom ou sa position dans la liste."
            )
            return state

        if not (1 <= position <= len(pending)):
            state["response"] = f"Merci de choisir un numéro entre 1 et {len(pending)}."
            return state

        company = pending[position - 1]
        state["company_id"] = company["id"]
        state["company_name"] = company["name"]
        state["pending_companies"] = None
        state["response"] = f"Entreprise sélectionnée : {company['name']}"
        return state

    # aucune recherche préalable en mémoire : on retombe sur une recherche directe
    # par nom (ex: l'utilisateur nomme une entreprise sans passer par company_search)
    q = state["question"].lower()
    keywords = q.split()

    key = keywords[-1] if keywords else q

    result = supabase.table("businesses") \
        .select("*") \
        .ilike("name", f"%{key}%") \
        .execute()

    if result.data:
        company = result.data[0]
        state["company_id"] = company["id"]
        state["company_name"] = company["name"]
        state["response"] = f"Entreprise sélectionnée : {company['name']}"
    else:
        state["response"] = "Entreprise introuvable"

    return state


# =========================
# VIEW PRODUCTS
# =========================

def view_products(state):

    # consulter les produits est une action de navigation : on sort de tout
    # contexte commande en cours pour ne pas rester piégé dans order_agent
    state["awaiting_order_action"] = None

    company_id = state.get("company_id")

    if not company_id:
        state["response"] = "Veuillez d'abord choisir une entreprise."
        return state

    result = supabase.table("products") \
        .select("*") \
        .eq("business_id", company_id) \
        .eq("active", True) \
        .execute()

    products = result.data or []

    if not products:
        state["response"] = "Cette entreprise n'a aucun produit disponible."
        return state

    lines = []
    for i, p in enumerate(products, start=1):
        nom = p.get("nom", "Produit sans nom")
        prix = p.get("prix", "Prix non disponible")
        image = p.get("image", "")
        stock = p.get("stock", 0)

        # Format simple : numéro. Nom — Prix TND (Stock)
        stock_info = f"({stock} en stock)" if stock else "(rupture)"
        ligne = f"{i}. {nom} — {prix} TND {stock_info}"

        # Ajoute l'image
        if image:
            ligne += f"\n[Image: {image}]"

        lines.append(ligne)

    state["pending_products"] = [{"id": p["id"], "nom": p["nom"], "prix": p["prix"], "image": p.get("image") or p.get("img") or "", "description": p.get("description"), "stock": p.get("stock")} for p in products]

    state["response"] = (
        f"Produits proposés par {state.get('company_name')} :\n\n" +
        "\n\n".join(lines) +
        "\n\nVoulez-vous ajouter un de ces produits à votre commande ? "
        "Répondez avec son numéro (ex: 1)."
    )
    return state


# =========================
# ADD FROM PRODUCTS
# =========================
# Nouvelle fonctionnalité : après l'affichage des produits (view_products),
# le client choisit un produit par son numéro/nom pour le commander. Si une
# commande modifiable est déjà en cours, le produit y est ajouté ; sinon une
# nouvelle commande est créée avec ce produit. Le flux order habituel reprend
# ensuite (awaiting_order_action → "ajouter un autre / confirmer").

def _resolve_product_selection(question, pending):
    """Retourne (produit_choisi, quantite) depuis la liste pending_products,
    ou (None, 1) si le message ne désigne aucun produit."""

    b = _bare(question)
    numbers = re.findall(r"\d+", b)
    has_qty_hint = any(k in f" {b} " for k in (" fois ", " x ", " du ", " des "))

    # --- chemin rapide sans LLM ---
    # un seul nombre et aucun indice de quantité → sélection par position
    # ("1", "le 2", "ajoute le 3")
    if len(numbers) == 1 and not has_qty_hint:
        pos = int(numbers[0])
        if 1 <= pos <= len(pending):
            return pending[pos - 1], 1

    # aucun chiffre mais une position littérale ("le premier", "le dernier")
    if not numbers:
        for word, pos in _ORDINALS.items():
            if not word.isdigit() and re.search(rf"\b{word}\b", b):
                if 1 <= pos <= len(pending):
                    return pending[pos - 1], 1
        if re.search(r"\bderni", b) and pending:
            return pending[-1], 1

    # --- fallback LLM : noms, quantités ("2 du 3"), accords ambigus ---
    products_list = "\n".join(
        f"{i}. {p.get('nom')} — {p.get('prix')} TND"
        for i, p in enumerate(pending, start=1)
    )
    prompt = PRODUCT_SELECT_PROMPT.format(
        products_list=products_list,
        question=question
    )
    result = llm.invoke(prompt).content.strip().lower()

    if "none" in result:
        return None, 1

    pair = re.match(r"\s*(\d+)\s*\|\s*(\d+)", result)
    if pair:
        position = int(pair.group(1))
        quantite = max(int(pair.group(2)), 1)
    else:
        # tolérance : le LLM n'a renvoyé qu'un nombre → position, quantité 1
        num = re.search(r"\d+", result)
        if not num:
            return None, 1
        position = int(num.group())
        quantite = 1

    if not (1 <= position <= len(pending)):
        return None, 1

    return pending[position - 1], quantite


def _get_product_by_id(product_id, quantite_demandee):
    """Recharge le produit par son id pour vérifier le stock à jour (la liste
    pending_products peut être obsolète). Retourne (product, erreur)."""

    result = supabase.table("products") \
        .select("id, nom, prix, stock, business_id") \
        .eq("id", product_id) \
        .limit(1) \
        .execute()

    if not result.data:
        return None, "Produit introuvable dans le catalogue."

    product = result.data[0]
    stock_disponible = product.get("stock", 0)

    if stock_disponible <= 0:
        return None, f"Le produit '{product['nom']}' est actuellement en rupture de stock."

    if quantite_demandee > stock_disponible:
        return None, (
            f"Stock insuffisant pour '{product['nom']}'. "
            f"Quantité demandée : {quantite_demandee}, stock disponible : {stock_disponible}."
        )

    return product, None


def _insert_item_and_decrement(order_id, business_id, product, quantite):
    supabase.table("order_items").insert({
        "order_id": order_id,
        "business_id": business_id,
        "product_id": product["id"],
        "quantite": quantite
    }).execute()

    supabase.table("products") \
        .update({"stock": product["stock"] - quantite}) \
        .eq("id", product["id"]) \
        .execute()


def add_from_products(state):

    pending = state.get("pending_products") or []

    if not pending:
        state["response"] = (
            "Aucun produit à sélectionner. Demandez d'abord à voir les produits "
            "de l'entreprise."
        )
        return state

    selection, quantite = _resolve_product_selection(state["question"], pending)

    if selection is None:
        # le message ne désigne aucun produit de la liste : si une commande est
        # déjà en cours, c'est une autre action (confirmer, retirer, annuler,
        # ou ajouter un produit hors liste par son nom) → on délègue au flux
        # order habituel ; sinon on redemande simplement un numéro.
        if state.get("order_id") or state.get("awaiting_order_action"):
            return order_agent(state)
        state["response"] = (
            f"Indiquez le numéro du produit que vous souhaitez commander "
            f"(entre 1 et {len(pending)}), par exemple : 1."
        )
        return state

    product, error = _get_product_by_id(selection["id"], quantite)
    if error:
        state["response"] = error
        return state

    session_business_id = state.get("session_business_id")
    # le produit peut provenir d'une autre entreprise (suggestion tous catalogues) :
    # la commande et ses lignes doivent porter le business_id du produit
    product_business_id = product.get("business_id") or session_business_id
    order_id = state.get("order_id")

    # une commande modifiable est déjà en cours → on y ajoute le produit
    if order_id and _get_order_status(order_id) in MODIFIABLE_ORDER_STATUSES:
        _insert_item_and_decrement(order_id, product_business_id, product, quantite)
        new_total = _recalculate_order_total(order_id)

        # on garde pending_products : le client peut continuer à choisir
        # d'autres produits de la liste par leur numéro
        state["awaiting_order_action"] = True
        state["response"] = (
            f"{quantite} x '{product['nom']}' ajouté(s) à votre commande "
            f"(prix unitaire : {product['prix']} TND). Nouveau total : {new_total} TND.\n\n"
            "Souhaitez-vous ajouter un autre produit ou confirmer votre commande ?"
        )
        return state

    # sinon → création d'une nouvelle commande avec ce produit
    user_id = state.get("user_id")
    if not user_id or not product_business_id:
        state["response"] = "Vous devez être connecté pour créer une commande."
        return state

    order_result = supabase.table("orders").insert({
        "user_id": user_id,
        "business_id": product_business_id,
        "telephone": "",
        "total": product["prix"] * quantite
    }).execute()

    new_order_id = order_result.data[0]["id"]
    _insert_item_and_decrement(new_order_id, product_business_id, product, quantite)
    new_total = _recalculate_order_total(new_order_id)

    # on garde pending_products : le client peut continuer à choisir d'autres
    # produits de la liste par leur numéro pour les ajouter à cette commande
    state["order_id"] = new_order_id
    state["pending_new_order"] = None
    state["awaiting_order_action"] = True
    state["response"] = (
        f"Nouvelle commande créée avec {quantite} x '{product['nom']}' "
        f"(prix unitaire : {product['prix']} TND). Total : {new_total} TND.\n\n"
        "Souhaitez-vous ajouter un autre produit ou confirmer votre commande ?"
    )
    return state


# =========================
# SUGGEST PRODUCT
# =========================
# Le client décrit une idée / un besoin de produit. On compare cette idée aux
# produits par SIMILARITÉ SÉMANTIQUE via des embeddings (vecteurs de sens
# pré-calculés une fois par produit, cf. embed_products.py) : rapide et très bon
# marché (1 seul embedding par recherche). Fallback gratuit par mots-clés si les
# embeddings ne sont pas disponibles (produits non indexés, quota épuisé...).

def _business_name_of(product):
    return (product.get("businesses") or {}).get("name")


# mots vides français ignorés lors du fallback par mots-clés
_FR_STOPWORDS = {
    "un", "une", "des", "du", "de", "la", "le", "les", "pour", "avec", "et",
    "ou", "je", "veux", "voudrais", "cherche", "il", "me", "faut", "truc",
    "chose", "quelque", "au", "aux", "en", "mon", "ma", "mes", "qui", "que",
    "sur", "dans", "par", "plus", "tres", "assez", "aussi", "avoir", "etre",
}


def _vec_to_str(vec):
    """Formate un vecteur Python en littéral pgvector : '[0.1,0.2,...]'."""
    return "[" + ",".join(f"{x:.8f}" for x in vec) + "]"


def _semantic_search(question, top_k=6, floor=0.32, margin=0.05):
    """Recherche vectorielle via la fonction Postgres match_products (index
    pgvector HNSW). Retourne une liste de produits (avec le nom d'entreprise),
    [] si rien de pertinent, ou None si la voie sémantique est indisponible
    (quota épuisé, embeddings/RPC absents) → l'appelant fera le fallback.

    Filtrage RELATIF : on récupère les candidats au-dessus d'un plancher (floor),
    puis on ne garde que ceux dont le score est proche du meilleur (à `margin`
    près). Cela s'auto-calibre : quand un produit se détache nettement (ex: une
    liqueur pour "digestif"), les produits juste au-dessus du plancher mais bien
    en dessous du top (sirop, confiture, sac...) sont écartés."""

    try:
        query_vec = embeddings.embed_query(question)
    except Exception:
        return None  # embeddings indisponibles (ex: quota)

    try:
        res = supabase.rpc("match_products", {
            "query_embedding": _vec_to_str(query_vec),
            "match_count": top_k,
            "similarity_threshold": floor,
        }).execute()
    except Exception:
        return None  # fonction RPC absente / erreur → fallback

    rows = res.data or []
    if not rows:
        return []

    best = max(r.get("similarity", 0) for r in rows)
    rows = [r for r in rows if r.get("similarity", 0) >= best - margin]

    # homogénéise le nom d'entreprise avec le format businesses(name)
    for r in rows:
        r["businesses"] = {"name": r.get("business_name")}
    return rows


def _keyword_search_db(question, limit=6):
    """Fallback gratuit et scalable : recherche SQL ILIKE sur nom/description
    (aucun embedding). Ne rapatrie que les produits correspondants."""

    tokens = [t for t in re.findall(r"\w+", normalize(question))
              if len(t) >= 3 and t not in _FR_STOPWORDS]
    if not tokens:
        return []

    ors = []
    for t in tokens:
        ors.append(f"nom.ilike.%{t}%")
        ors.append(f"description.ilike.%{t}%")

    res = supabase.table("products") \
        .select("id, nom, prix, description, image, stock, business_id, businesses(name)") \
        .eq("active", True) \
        .or_(",".join(ors)) \
        .limit(limit) \
        .execute()

    return res.data or []


def suggest_product(state):

    # action de découverte : on sort de tout contexte commande en cours
    state["awaiting_order_action"] = None

    # 1) recherche vectorielle (pgvector, indexée dans Postgres) — scalable :
    #    aucun rapatriement du catalogue complet.
    # 2) fallback recherche SQL par mots-clés si la voie sémantique est
    #    indisponible (quota) ou ne renvoie rien de pertinent.
    matched = _semantic_search(state["question"])
    if not matched:
        matched = _keyword_search_db(state["question"])

    if not matched:
        state["pending_products"] = None
        state["response"] = (
            "Aucun produit ne correspond à votre idée pour le moment. "
            "Essayez de la reformuler, ou demandez à voir tous les produits."
        )
        return state

    lines = []
    for i, p in enumerate(matched, start=1):
        nom = p.get("nom", "Produit sans nom")
        prix = p.get("prix", "Prix non disponible")
        image = p.get("image", "")
        stock = p.get("stock", 0)
        business_name = _business_name_of(p)

        stock_info = f"({stock} en stock)" if stock else "(rupture)"
        ligne = f"{i}. {nom} — {prix} TND {stock_info}"
        if business_name:
            ligne += f" — {business_name}"
        if image:
            ligne += f"\n[Image: {image}]"
        lines.append(ligne)

    state["pending_products"] = [
        {
            "id": p["id"], "nom": p["nom"], "prix": p["prix"],
            "image": p.get("image") or "", "description": p.get("description"),
            "stock": p.get("stock"),
            "business": _business_name_of(p), "business_id": p.get("business_id")
        }
        for p in matched
    ]

    state["response"] = (
        "Voici les produits qui correspondent à votre idée :\n\n" +
        "\n\n".join(lines) +
        "\n\nVoulez-vous en ajouter un à votre commande ? Répondez avec son numéro (ex: 1)."
    )
    return state


# =========================
# FAQ AGENT
# =========================

VALID_FAQ_COLUMNS = (
    "phone", "horaires", "address", "email",
    "facebook_url", "instagram_url", "tiktok_url", "status", "retour"
)

# noms de colonnes possibles dans la table businesses pour chaque info (le
# schéma réel peut différer légèrement : singulier/pluriel, anglais...). On
# essaie ces alias dans l'ordre.
_FAQ_COLUMN_ALIASES = {
    "phone": ("phone", "telephone", "tel", "numero", "contact"),
    "horaires": ("horaires", "horaire", "opening_hours", "hours", "heures"),
    "address": ("address", "adresse", "localisation", "location"),
    "email": ("email", "mail", "e_mail"),
    "facebook_url": ("facebook_url", "facebook", "fb_url"),
    "instagram_url": ("instagram_url", "instagram", "insta_url"),
    "tiktok_url": ("tiktok_url", "tiktok"),
    "status": ("status", "statut", "etat"),
    "retour": ("retour", "retours", "return_policy", "politique_retour"),
}

# règles mots-clés → colonne, du plus spécifique au plus général. "status"
# (ouvert/fermé) est vérifié en dernier pour ne pas capter les questions
# d'horaires ("heures d'ouverture").
_FAQ_COLUMN_RULES = (
    ("email", ("email", "mail", "courriel")),
    ("phone", ("telephone", "numero", "appeler", "appel", "joindre", "portable")),
    ("facebook_url", ("facebook",)),
    ("instagram_url", ("instagram", "insta")),
    ("tiktok_url", ("tiktok", "tik tok")),
    ("retour", ("retour", "remboursement", "rembourser", "echange", "rendre")),
    ("horaires", ("horaire", "heure", "ouverture", "ouvre", "fermeture", "quand ouvert")),
    ("address", ("adresse", "ou se trouve", "ou est", "ou etes", "localisation",
                 "situe", "situee", "rue", "trouver")),
    ("status", ("ouvert", "ferme", "actif", "active", "disponible", "en activite")),
)


def _detect_faq_column(question):
    # 1) règles gratuites
    bare = _bare(question)
    for column, kws in _FAQ_COLUMN_RULES:
        if _has_kw(bare, kws):
            return column
    # 2) fallback LLM seulement si aucune règle ne matche
    prompt = FAQ_COLUMN_PROMPT.format(question=question)
    column = llm.invoke(prompt).content.strip().lower()
    return column if column in VALID_FAQ_COLUMNS else None


def faq(state):

    company_id = state.get("company_id")

    if not company_id:
        state["response"] = "Veuillez d'abord choisir une entreprise."
        return state

    column = _detect_faq_column(state["question"])

    if not column:
        state["response"] = "Je ne comprends pas la question."
        return state

    # on récupère toute la ligne : ainsi un nom de colonne légèrement différent
    # dans la base (ex: "horaire" au lieu de "horaires") ne fait plus planter la
    # requête (erreur 42703). On essaie ensuite plusieurs noms possibles.
    result = supabase.table("businesses") \
        .select("*") \
        .eq("id", company_id) \
        .limit(1) \
        .execute()

    row = result.data[0] if result.data else {}

    value = None
    for candidate in _FAQ_COLUMN_ALIASES.get(column, (column,)):
        if row.get(candidate) is not None:
            value = row[candidate]
            break

    state["response"] = value if value is not None else "Information non disponible"

    return state


# =========================
# ASK CONTINUE
# =========================

def ask_continue(state):

    company_name = state.get("company_name") or "cette entreprise"
    state["response"] = (
        f"{state['response']}\n\n"
        f"Souhaitez-vous obtenir d'autres informations sur {company_name} ?"
    )
    return state


# =========================
# CONFIRM CONTINUE
# =========================

def confirm_continue(state):

    company_name = state.get("company_name") or "l'entreprise"
    state["response"] = f"Bien sûr ! Quelle information souhaitez-vous sur {company_name} ?"
    return state


# =========================
# RESTART
# =========================

def restart(state):

    state["company_id"] = None
    state["company_name"] = None
    state["response"] = "D'accord ! Quel domaine vous intéresse ?"
    return state


# =========================
# ORDER RESOLUTION
# =========================

ACTIVE_ORDER_STATUSES = ("EN_COURS", "CONFIRMER", "EN_ROUTE")

# EN_COURS est le statut par défaut d'une commande fraîchement créée, avant
# confirmation par le client. Une fois confirmée (ou au-delà), la commande
# est figée : ni ajout, ni retrait de produit, ni annulation ne sont possibles
MODIFIABLE_ORDER_STATUSES = ("EN_COURS",)

STATUS_LABELS = {
    "EN_EN_APPELLE": "En attente d'appel",
    "CONFIRMER":   "Confirmée",
    "EN_COURS":    "En attente de confirmation",
    "EN_ROUTE":    "Expédiée",
    "LIVREE":      "Livrée",
    "ANNULEE":     "Annulée",
}


def _format_order_datetime(created_at):
    if not created_at:
        return "date inconnue"
    try:
        dt = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
        return dt.strftime("%d/%m/%Y à %H:%M")
    except ValueError:
        return created_at


# positions littérales → numéro (uniquement pour désigner une COMMANDE précise)
_POSITION_WORDS = {
    "premiere": 1, "premier": 1, "1ere": 1, "1er": 1,
    "deuxieme": 2, "seconde": 2, "second": 2, "2eme": 2, "2nd": 2,
    "troisieme": 3, "3eme": 3, "quatrieme": 4, "4eme": 4,
    "cinquieme": 5, "5eme": 5, "sixieme": 6, "septieme": 7,
    "huitieme": 8, "neuvieme": 9, "dixieme": 10,
}


def _extract_requested_position(question):
    """Détecte une référence EXPLICITE à une position de commande ("commande 2",
    "commande n°2", "ma deuxième commande", "la 3ème"). Retourne None si aucune
    position précise n'est désignée (défaut sûr, identique à l'ancien comportement).

    100% déterministe : on ne capte qu'un chiffre rattaché au mot "commande",
    une forme ordinale ("2eme", "3ere") ou un ordinal littéral, jamais un chiffre
    isolé (qui pourrait être une quantité)."""

    b = _bare(question)

    m = re.search(r"commande\s+(?:n\s+|no\s+|numero\s+)?(\d+)", b)
    if m:
        return int(m.group(1))

    m = re.search(r"\b(\d+)\s*(?:eme|ere|er|nd)\b", b)
    if m:
        return int(m.group(1))

    for word, pos in _POSITION_WORDS.items():
        if re.search(rf"\b{word}\b", b):
            return pos

    return None


def _resolve_order_for_user(user_id, business_id, require_active=False, requested_position=None):
    """Retrouve la commande du client authentifié sans lui demander son order_id.

    user_id et business_id proviennent tous les deux de la session authentifiée
    (table user_sessions), jamais d'une valeur fournie par le client dans le message.

    requested_position, si fourni, force la sélection de cette position précise
    dans la liste des commandes (ex: le client a dit "commande 2") plutôt que de
    se fier à un éventuel order_id déjà en cache.

    Retourne (order_id, pending_order_ids, message) :
    - order_id est fixé si une commande unique a pu être déterminée
    - pending_order_ids est fixé si plusieurs commandes sont possibles (désambiguïsation)
    - message est fixé si aucune commande n'a pu être déterminée
    """

    if not user_id or not business_id:
        return None, None, "Vous devez être connecté pour accéder à vos commandes."

    result = supabase.table("orders") \
        .select("id, status, created_at") \
        .eq("user_id", user_id) \
        .eq("business_id", business_id) \
        .order("created_at", desc=True) \
        .limit(10) \
        .execute()

    orders = result.data or []

    if not orders:
        return None, None, "Vous n'avez aucune commande enregistrée."

    active_orders = [o for o in orders if o.get("status") in ACTIVE_ORDER_STATUSES]

    if require_active and not active_orders:
        return None, None, "Vous n'avez aucune commande en cours pouvant être modifiée."

    candidates = active_orders if active_orders else orders

    if requested_position is not None:
        if 1 <= requested_position <= len(candidates):
            return candidates[requested_position - 1]["id"], None, None
        return None, None, (
            f"Vous n'avez que {len(candidates)} commande(s) en cours. "
            f"Merci de choisir un numéro entre 1 et {len(candidates)}."
        )

    if len(candidates) == 1:
        return candidates[0]["id"], None, None

    lines = []
    ids = []
    for i, o in enumerate(candidates, start=1):
        date_str = _format_order_datetime(o.get("created_at"))
        status_label = STATUS_LABELS.get(o.get("status"), o.get("status") or "Statut inconnu")
        # pas d'id brut affiché : seule la position (1, 2, ...) sert de référence pour répondre
        lines.append(f"{i}. Commande du {date_str} — {status_label}")
        ids.append(o["id"])

    message = (
        "Vous avez plusieurs commandes en cours (de la plus récente à la plus ancienne) :\n" +
        "\n".join(lines) +
        "\n\nRépondez avec le numéro correspondant (1, 2, ...) pour préciser laquelle vous concerne."
    )
    return None, ids, message


def _fr_date(iso_date):
    """AAAA-MM-JJ → JJ/MM/AAAA pour l'affichage."""
    try:
        return datetime.fromisoformat(iso_date).strftime("%d/%m/%Y")
    except ValueError:
        return iso_date


# mots-clés bon marché pour détecter qu'une date/période est peut-être mentionnée,
# afin d'éviter un appel LLM inutile quand ce n'est pas le cas
_DATE_HINTS = (
    "janvier", "février", "fevrier", "mars", "avril", "mai", "juin", "juillet",
    "août", "aout", "septembre", "octobre", "novembre", "décembre", "decembre",
    "hier", "aujourd", "semaine", "mois", "jour", "date", "depuis", "avant",
    "après", "apres", "entre", "jusqu",
)


def _parse_order_date_filter(question):
    """Extrait une éventuelle plage de dates du message pour filtrer les
    commandes. Retourne (start_date, end_date) au format AAAA-MM-JJ, chaque
    borne pouvant être None (pas de date → (None, None), comportement d'origine)."""

    # court-circuit sans appel LLM : si le message ne contient ni chiffre ni
    # mot évoquant une date/période, il n'y a rien à filtrer
    q = question.lower()
    if not any(c.isdigit() for c in q) and not any(h in q for h in _DATE_HINTS):
        return None, None

    today = datetime.now().date().isoformat()
    prompt = ORDER_DATE_FILTER_PROMPT.format(today=today, question=question)
    result = llm.invoke(prompt).content.strip().lower()

    parts = result.split("|")

    def _norm(part):
        part = part.strip()
        return part if re.fullmatch(r"\d{4}-\d{2}-\d{2}", part) else None

    start = _norm(parts[0]) if len(parts) > 0 else None
    end = _norm(parts[1]) if len(parts) > 1 else None
    return start, end


def _describe_period(start_date, end_date):
    """Libellé de la période filtrée, à insérer dans les messages."""
    if start_date and end_date:
        if start_date == end_date:
            return f" du {_fr_date(start_date)}"
        return f" entre le {_fr_date(start_date)} et le {_fr_date(end_date)}"
    if start_date:
        return f" depuis le {_fr_date(start_date)}"
    if end_date:
        return f" jusqu'au {_fr_date(end_date)}"
    return ""


def _list_orders(state):
    """Vue d'ensemble des commandes du client (pas le détail d'une seule),
    éventuellement filtrée par une date précise ou une plage de dates."""

    user_id = state.get("user_id")
    business_id = state.get("session_business_id")

    if not user_id or not business_id:
        state["response"] = "Vous devez être connecté pour accéder à vos commandes."
        return state

    start_date, end_date = _parse_order_date_filter(state["question"])

    query = supabase.table("orders") \
        .select("id, status, created_at, total") \
        .eq("user_id", user_id) \
        .eq("business_id", business_id)

    # bornes incluses : on prend la journée entière pour chaque date
    if start_date:
        query = query.gte("created_at", f"{start_date}T00:00:00")
    if end_date:
        query = query.lte("created_at", f"{end_date}T23:59:59")

    result = query.order("created_at", desc=True).limit(10).execute()

    orders = result.data or []

    period_label = _describe_period(start_date, end_date)

    if not orders:
        state["response"] = f"Vous n'avez aucune commande{period_label}."
        return state

    lines = []
    for i, o in enumerate(orders, start=1):
        date_str = _format_order_datetime(o.get("created_at"))
        status_label = STATUS_LABELS.get(o.get("status"), o.get("status") or "Statut inconnu")
        total = o.get("total")
        total_str = f" — {total} TND" if total is not None else ""
        lines.append(f"{i}. Commande du {date_str} — {status_label}{total_str}")

    # permet à Flask de résoudre une réponse numérique ultérieure ("1") vers
    # la commande correspondante, comme pour une désambiguïsation classique
    state["pending_orders"] = [o["id"] for o in orders]
    state["pending_question"] = "je veux voir les détails de cette commande"

    state["response"] = (
        f"Voici vos commandes{period_label} (de la plus récente à la plus ancienne) :\n" +
        "\n".join(lines) +
        "\n\nPrécisez un numéro ou une position (ex: \"la deuxième commande\") pour en voir le détail."
    )
    return state


# =========================
# ORDER AGENT
# =========================

def _order_sub_intent(question):
    """Classe l'action commande de façon déterministe (gratuit). Retourne None
    si c'est ambigu → order_agent retombera sur le LLM. L'ordre suit les
    priorités de ORDER_ROUTER_PROMPT."""

    b = _bare(question)

    # Robustesse à l'élision écrite SANS apostrophe : "jannule" (au lieu de
    # "j'annule") collait le pronom au verbe, si bien que "annule" n'était plus
    # un mot entier et échappait à _has_kw — le message tombait alors sur la
    # règle générique "la commande" (view_order) au lieu de cancel_order. On
    # ré-insère l'espace devant les seuls verbes d'action connus (ciblé : ni
    # "commande", ni "recommande", ni "jamais"... ne sont impactés).
    b = re.sub(
        r"\b([jlmtnscd])(annul|ajout|enlev|retir|supprim|confirm|valid)",
        r"\1 \2", b
    )

    # 1. liste de TOUTES les commandes (pluriel, aucune commande précise)
    if _has_kw(b, ("mes commandes", "liste de mes commandes",
                   "toutes mes commandes", "mes dernieres commandes",
                   "toutes les commandes", "historique")):
        return "list_orders"

    # 2. création d'une nouvelle commande
    if _has_kw(b, ("nouvelle commande", "creer une commande",
                   "cree une commande", "creer commande", "faire une commande",
                   "passer une commande", "demarrer une commande",
                   "demarre une commande", "je veux commander",
                   "veux commander", "souhaite commander", "aimerais commander")):
        return "create_order"

    # 3. facettes d'action (ajout / retrait / annulation / confirmation)
    if _has_kw(b, ("ajoute", "ajouter")):
        return "add_product"
    if _has_kw(b, ("retire", "retirer", "enleve", "enlever",
                   "supprime", "supprimer")):
        return "remove_product"
    if _has_kw(b, ("annule", "annuler", "annulation")):
        return "cancel_order"
    if _has_kw(b, ("confirme", "confirmer", "valide", "valider")):
        return "confirm_order"

    # 4. facettes d'information
    if _has_kw(b, ("total", "montant")):
        return "order_total"
    if _has_kw(b, ("statut", "confirmee", "etat de", "est ce que ma commande")):
        return "order_status"
    if _has_kw(b, ("livraison", "livree", "livrer")):
        return "delivery_date"
    if _has_kw(b, ("ou est", "suivi", "suivre", "localiser", "tracking")):
        return "track_order"

    # 5. voir une commande précise (formulations explicites, ou "ma commande" seul)
    if _has_kw(b, ("voir ma commande", "voir la commande", "montre ma commande",
                   "montre moi ma commande", "affiche ma commande",
                   "contenu de ma commande", "detail de ma commande",
                   "details de ma commande", "produits de ma commande",
                   "ma commande", "la commande", "cette commande")):
        return "view_order"

    # trop ambigu → LLM
    return None


def order_agent(state):

    # une confirmation de retrait de produit ou d'annulation de commande est
    # en attente : elle prime sur toute nouvelle classification pour ce tour
    if state.get("pending_removal"):
        return _handle_removal_confirmation(state)

    if state.get("pending_cancel"):
        return _handle_cancel_confirmation(state)

    # la commande a été initiée mais aucune ligne "orders" n'existe encore
    # (elle ne peut être créée qu'avec un total > 0, donc avec un premier
    # produit) : n'importe quel message à ce stade est une tentative de
    # nommer le produit ("sirop de cerise" seul, sans le verbe "ajoute"), pas
    # la peine de reclassifier via ORDER_ROUTER_PROMPT qui ne le reconnaîtrait
    # pas comme add_product
    if state.get("pending_new_order"):
        return _start_order_with_product(state)

    # règles gratuites d'abord ; fallback LLM seulement si ambigu
    sub_intent = _order_sub_intent(state["question"])
    if sub_intent is None:
        prompt = ORDER_ROUTER_PROMPT.format(question=state["question"])
        sub_intent = llm.invoke(prompt).content.strip().lower()

    if "list_orders" in sub_intent:
        return _list_orders(state)

    if "create_order" in sub_intent:
        return _create_order(state)

    requested_position = _extract_requested_position(state["question"])

    if requested_position or not state.get("order_id"):

        order_id, pending_ids, message = _resolve_order_for_user(
            state.get("user_id"),
            state.get("session_business_id"),
            require_active="add_product" in sub_intent or "remove_product" in sub_intent or "cancel_order" in sub_intent,
            requested_position=requested_position
        )

        if order_id:
            state["order_id"] = order_id
            state["pending_orders"] = None
        elif pending_ids:
            state["pending_orders"] = pending_ids
            state["pending_question"] = state["question"]
            state["response"] = message
            return state
        else:
            state["response"] = message
            return state

    if "add_product" in sub_intent:
        return _add_product(state)
    elif "remove_product" in sub_intent:
        return _remove_product(state)
    elif "cancel_order" in sub_intent:
        return _cancel_order(state)
    elif "confirm_order" in sub_intent:
        return _confirm_order(state)
    elif "view_order" in sub_intent:
        return _view_order(state)
    elif "order_total" in sub_intent:
        return _order_total(state)
    elif "track_order" in sub_intent:
        return _track_order(state)
    elif "delivery_date" in sub_intent:
        return _delivery_date(state)
    elif "order_status" in sub_intent:
        return _order_status(state)
    elif requested_position is not None:
        # l'utilisateur a désigné une commande précise (ex: "1") sans préciser
        # quelle information il veut : on affiche le détail par défaut, comme
        # le documente la règle 3 de ORDER_ROUTER_PROMPT.
        return _view_order(state)
    elif state.get("awaiting_order_action"):
        # on vient de demander "ajouter un autre produit ou confirmer ?" et le
        # message n'a pas été classé (ex: un titre de produit sans verbe) :
        # dans ce contexte, c'est très probablement une tentative d'ajout
        return _add_product(state)
    else:
        state["response"] = "Je n'ai pas compris votre demande concernant la commande."
        return state


def _get_order_status(order_id):
    result = supabase.table("orders") \
        .select("status") \
        .eq("id", order_id) \
        .limit(1) \
        .execute()
    return result.data[0].get("status") if result.data else None


def _extract_product_and_quantity(question):
    extract_prompt = (
        "Extrais le nom du produit et la quantité mentionnés dans cette phrase.\n"
        "Réponds uniquement au format : NOM_PRODUIT|QUANTITE\n"
        "Si aucune quantité n'est mentionnée, mets 1 par défaut.\n"
        "Exemples :\n"
        "- 'ajoute 2 paquets de lait' → lait|2\n"
        "- 'je veux 3 bouteilles d eau' → eau|3\n"
        "- 'ajoute du pain' → pain|1\n"
        f"Phrase : {question}"
    )
    extracted = llm.invoke(extract_prompt).content.strip()

    parts = extracted.split("|")
    product_name = parts[0].strip()
    try:
        quantite = int(parts[1].strip()) if len(parts) > 1 else 1
    except ValueError:
        quantite = 1

    return product_name, max(quantite, 1)


def _find_available_product(product_name, quantite_demandee):
    """Retourne (product, erreur). product vaut None si erreur est renseignée."""

    result = supabase.table("products") \
        .select("id, nom, prix, stock") \
        .ilike("nom", f"%{product_name}%") \
        .limit(1) \
        .execute()

    if not result.data:
        return None, f"Produit '{product_name}' introuvable dans le catalogue."

    product = result.data[0]
    stock_disponible = product.get("stock", 0)

    if stock_disponible <= 0:
        return None, f"Le produit '{product['nom']}' est actuellement en rupture de stock."

    if quantite_demandee > stock_disponible:
        return None, (
            f"Stock insuffisant pour '{product['nom']}'. "
            f"Quantité demandée : {quantite_demandee}, stock disponible : {stock_disponible}."
        )

    return product, None


def _add_product(state):

    order_id = state.get("order_id")
    session_business_id = state.get("session_business_id")

    if not order_id:
        state["response"] = "Aucune commande en cours. Veuillez d'abord créer une commande."
        return state

    if _get_order_status(order_id) not in MODIFIABLE_ORDER_STATUSES:
        state["response"] = "Cette commande ne peut plus être modifiée."
        return state

    product_name, quantite_demandee = _extract_product_and_quantity(state["question"])
    product, error = _find_available_product(product_name, quantite_demandee)

    if error:
        state["response"] = error
        return state

    supabase.table("order_items").insert({
        "order_id": order_id,
        "business_id": session_business_id,
        "product_id": product["id"],
        "quantite": quantite_demandee
    }).execute()

    # décrémente le stock du produit vendu
    supabase.table("products") \
        .update({"stock": product["stock"] - quantite_demandee}) \
        .eq("id", product["id"]) \
        .execute()

    new_total = _recalculate_order_total(order_id)

    state["awaiting_order_action"] = True
    state["response"] = (
        f"{quantite_demandee} x '{product['nom']}' ajouté(s) à votre commande "
        f"(prix unitaire : {product['prix']} TND). Nouveau total : {new_total} TND.\n\n"
        "Souhaitez-vous ajouter un autre produit ou confirmer votre commande ?"
    )
    return state


def _create_order(state):

    user_id = state.get("user_id")
    session_business_id = state.get("session_business_id")

    if not user_id or not session_business_id:
        state["response"] = "Vous devez être connecté pour créer une commande."
        return state

    # la ligne "orders" ne peut être créée qu'avec un total > 0 (contrainte
    # orders_total_check) : on attend donc le premier produit avant d'insérer
    state["pending_new_order"] = True
    state["pending_orders"] = None
    state["pending_removal"] = None
    state["response"] = "Nouvelle commande initiée ! Quel produit souhaitez-vous ajouter ?"
    return state


def _start_order_with_product(state):

    user_id = state.get("user_id")
    session_business_id = state.get("session_business_id")

    product_name, quantite_demandee = _extract_product_and_quantity(state["question"])
    product, error = _find_available_product(product_name, quantite_demandee)

    if error:
        state["response"] = error
        return state

    order_result = supabase.table("orders").insert({
        "user_id": user_id,
        "business_id": session_business_id,
        "telephone": "",
        "total": product["prix"] * quantite_demandee
    }).execute()

    order_id = order_result.data[0]["id"]

    supabase.table("order_items").insert({
        "order_id": order_id,
        "business_id": session_business_id,
        "product_id": product["id"],
        "quantite": quantite_demandee
    }).execute()

    supabase.table("products") \
        .update({"stock": product["stock"] - quantite_demandee}) \
        .eq("id", product["id"]) \
        .execute()

    new_total = _recalculate_order_total(order_id)

    state["order_id"] = order_id
    state["pending_new_order"] = None
    state["awaiting_order_action"] = True
    state["response"] = (
        f"Nouvelle commande créée avec {quantite_demandee} x '{product['nom']}' "
        f"(prix unitaire : {product['prix']} TND). Total : {new_total} TND.\n\n"
        "Souhaitez-vous ajouter un autre produit ou confirmer votre commande ?"
    )
    return state


def _remove_product(state):

    order_id = state.get("order_id")

    if not order_id:
        state["response"] = "Aucune commande en cours."
        return state

    if _get_order_status(order_id) not in MODIFIABLE_ORDER_STATUSES:
        state["response"] = "Cette commande ne peut plus être modifiée."
        return state

    items_result = supabase.table("order_items") \
        .select("id, quantite, product_id, products(nom, prix)") \
        .eq("order_id", order_id) \
        .execute()
    items = items_result.data or []

    # 1) tentative sans LLM : on score chaque produit de la commande par le
    #    nombre de mots (>=3 lettres) communs avec la phrase, et on prend le
    #    MEILLEUR ("retire robe maillot juliette" → 'Robe maillot Juliette',
    #    pas 'Maillot WARD Classic' qui ne partage que "maillot").
    b = _bare(state["question"])
    query_tokens = {w for w in b.split() if len(w) >= 3}

    scored = []
    for item in items:
        nom = _bare((item.get("products") or {}).get("nom", ""))
        name_tokens = {w for w in nom.split() if len(w) >= 3}
        scored.append((len(name_tokens & query_tokens), item))
    scored.sort(key=lambda s: s[0], reverse=True)

    match = None
    # on ne tranche que si un produit se détache nettement (meilleur score
    # STRICTEMENT supérieur au suivant) ; sinon ambigu → fallback LLM
    if scored and scored[0][0] > 0:
        if len(scored) == 1 or scored[0][0] > scored[1][0]:
            match = scored[0][1]

    product_name = None
    # 2) fallback LLM seulement si aucune correspondance directe
    if match is None:
        extract_prompt = (
            "Extrais uniquement le nom du produit que l'utilisateur veut retirer de sa commande.\n"
            "Réponds uniquement par le nom du produit, sans rien d'autre.\n"
            "Exemples :\n"
            "- 'retire le pain de ma commande' → pain\n"
            "- 'enlève 2 paquets de lait' → lait\n"
            "- 'supprime la confiture' → confiture\n"
            f"Phrase : {state['question']}"
        )
        product_name = llm.invoke(extract_prompt).content.strip()
        for item in items:
            nom = (item.get("products") or {}).get("nom", "")
            if product_name.lower() in nom.lower():
                match = item
                break

    if not match:
        label = product_name or state["question"].strip()
        state["response"] = f"'{label}' ne fait pas partie de votre commande."
        return state

    product_nom = (match.get("products") or {}).get("nom", product_name)

    last_item = len(items_result.data) == 1

    state["pending_removal"] = {
        "order_id": order_id,
        "item_id": match["id"],
        "product_id": match["product_id"],
        "product_name": product_nom,
        "quantite": match.get("quantite", 1),
        "last_item": last_item
    }

    if last_item:
        state["response"] = (
            f"'{product_nom}' est le seul produit de votre commande. Voulez-vous le "
            "remplacer par un autre produit, ou annuler la commande entière ?"
        )
    else:
        state["response"] = f"Êtes-vous sûr de vouloir retirer '{product_nom}' de votre commande ?"

    return state


def _restock_product(product_id, quantite):
    product_result = supabase.table("products") \
        .select("stock") \
        .eq("id", product_id) \
        .limit(1) \
        .execute()
    if product_result.data:
        stock_actuel = product_result.data[0].get("stock", 0)
        supabase.table("products") \
            .update({"stock": stock_actuel + quantite}) \
            .eq("id", product_id) \
            .execute()


def _quick_yes_no(question):
    """Détecte oui/non sans LLM pour les réponses courantes. Retourne 'oui',
    'non', ou None si c'est ambigu/contradictoire (→ fallback LLM)."""

    b = _bare(question)
    words = set(b.split())
    yes = {"oui", "ouais", "ok", "okay", "daccord", "dac", "vasy", "carrement",
           "yes", "confirme", "confirmer", "valide", "valider", "confirmer"}
    no = {"non", "nan"}

    has_yes = bool(words & yes) or "d accord" in b or "c est bon" in b \
        or "cest bon" in b or "vas y" in b
    has_no = bool(words & no)

    if has_yes and not has_no:
        return "oui"
    if has_no and not has_yes:
        return "non"
    return None


def _last_item_action(question):
    """annuler / garder / remplacer sans LLM, ou None si ambigu (→ fallback)."""

    b = _bare(question)

    if any(k in b for k in ("remplace", "remplacer", "a la place", "plutot", "mets")):
        return "remplacer"
    if any(k in b for k in ("annule", "annuler", "annulation", "tout annuler",
                            "annule tout")):
        return "annuler"
    if any(k in b for k in ("garde", "garder", "laisse", "non", "rien",
                            "change davis", "change d avis")):
        return "garder"
    return None


def _handle_removal_confirmation(state):

    pending = state["pending_removal"]

    if pending.get("last_item"):
        return _handle_last_item_action(state, pending)

    answer = _quick_yes_no(state["question"])
    if answer is None:
        prompt = REMOVE_CONFIRM_PROMPT.format(question=state["question"])
        answer = llm.invoke(prompt).content.strip().lower()

    if "oui" in answer:
        supabase.table("order_items").delete().eq("id", pending["item_id"]).execute()
        _restock_product(pending["product_id"], pending["quantite"])
        new_total = _recalculate_order_total(pending["order_id"])

        state["pending_removal"] = None
        state["response"] = (
            f"'{pending['product_name']}' a été retiré de votre commande. "
            f"Nouveau total : {new_total} TND."
        )
        return state

    if "non" in answer:
        state["pending_removal"] = None
        state["response"] = f"D'accord, '{pending['product_name']}' reste dans votre commande."
        return state

    state["response"] = (
        f"Merci de répondre par oui ou non : voulez-vous vraiment retirer "
        f"'{pending['product_name']}' de votre commande ?"
    )
    return state


def _handle_last_item_action(state, pending):

    action = _last_item_action(state["question"])
    if action is None:
        prompt = LAST_ITEM_ACTION_PROMPT.format(question=state["question"])
        action = llm.invoke(prompt).content.strip().lower()

    if "annuler" in action:
        supabase.table("orders") \
            .update({"status": "ANNULEE"}) \
            .eq("id", pending["order_id"]) \
            .execute()
        supabase.table("order_items").delete().eq("id", pending["item_id"]).execute()
        _restock_product(pending["product_id"], pending["quantite"])
        _recalculate_order_total(pending["order_id"])

        state["pending_removal"] = None
        state["order_id"] = None
        state["response"] = "Votre commande a été annulée."
        return state

    if "garder" in action:
        state["pending_removal"] = None
        state["response"] = f"D'accord, '{pending['product_name']}' reste dans votre commande."
        return state

    if "remplacer" in action:
        new_product_name, quantite_demandee = _extract_product_and_quantity(state["question"])
        product, error = _find_available_product(new_product_name, quantite_demandee)

        if error:
            state["response"] = f"{error} Voulez-vous essayer un autre produit, ou annuler la commande ?"
            return state

        supabase.table("order_items").delete().eq("id", pending["item_id"]).execute()
        _restock_product(pending["product_id"], pending["quantite"])

        supabase.table("order_items").insert({
            "order_id": pending["order_id"],
            "business_id": state.get("session_business_id"),
            "product_id": product["id"],
            "quantite": quantite_demandee
        }).execute()
        supabase.table("products") \
            .update({"stock": product["stock"] - quantite_demandee}) \
            .eq("id", product["id"]) \
            .execute()

        new_total = _recalculate_order_total(pending["order_id"])

        state["pending_removal"] = None
        state["awaiting_order_action"] = True
        state["response"] = (
            f"'{pending['product_name']}' a été remplacé par {quantite_demandee} x "
            f"'{product['nom']}' (prix unitaire : {product['prix']} TND). "
            f"Nouveau total : {new_total} TND.\n\n"
            "Souhaitez-vous ajouter un autre produit ou confirmer votre commande ?"
        )
        return state

    state["response"] = (
        f"'{pending['product_name']}' est le seul produit de votre commande. "
        "Voulez-vous le remplacer par un autre produit, ou annuler la commande entière ?"
    )
    return state


def _recalculate_order_total(order_id):
    """Recalcule le total d'une commande à partir de order_items (source de
    vérité) et le repersiste dans orders.total, pour éviter que cette colonne
    ne reste désynchronisée (ex: commandes créées hors de _add_product)."""

    items_result = supabase.table("order_items") \
        .select("quantite, products(prix)") \
        .eq("order_id", order_id) \
        .execute()

    new_total = sum(
        (item.get("products") or {}).get("prix", 0) * item.get("quantite", 0)
        for item in (items_result.data or [])
    )

    supabase.table("orders") \
        .update({"total": new_total}) \
        .eq("id", order_id) \
        .execute()

    return new_total


def _confirm_order(state):

    state["awaiting_order_action"] = None

    order_id = state.get("order_id")

    if not order_id:
        state["response"] = "Aucune commande à confirmer."
        return state

    result = supabase.table("orders") \
        .select("status") \
        .eq("id", order_id) \
        .limit(1) \
        .execute()

    if not result.data:
        state["response"] = "Commande introuvable."
        return state

    status = result.data[0].get("status", "")

    if status == "CONFIRMER":
        state["response"] = f"Votre commande #{order_id} est déjà confirmée."
    elif status == "EN_ROUTE":
        state["response"] = f"Votre commande #{order_id} est déjà expédiée, il est trop tard pour la confirmer."
    elif status == "LIVREE":
        state["response"] = f"Votre commande #{order_id} a déjà été livrée."
    elif status == "ANNULEE":
        state["response"] = f"Votre commande #{order_id} a été annulée, elle ne peut pas être confirmée."
    elif status == "EN_COURS":
        supabase.table("orders") \
            .update({"status": "CONFIRMER"}) \
            .eq("id", order_id) \
            .execute()
        state["response"] = (
            f"Votre commande #{order_id} a été confirmée avec succès. "
            "Vous recevrez une notification dès son expédition."
        )
    else:
        # statuts du workflow d'appel client (EN_EN_APPELLE, APPELLE_CLIENT_*,
        # NON_REPONDRE_CLIENT_*) : la confirmation est gérée par l'équipe
        state["response"] = f"Votre commande #{order_id} est en cours de traitement par notre équipe."

    return state


def _cancel_order(state):

    state["awaiting_order_action"] = None

    order_id = state.get("order_id")

    if not order_id:
        state["response"] = "Aucune commande à annuler."
        return state

    result = supabase.table("orders") \
        .select("status") \
        .eq("id", order_id) \
        .limit(1) \
        .execute()

    if not result.data:
        state["response"] = "Commande introuvable."
        return state

    status = result.data[0].get("status", "")

    if status == "ANNULEE":
        state["response"] = f"Votre commande #{order_id} est déjà annulée."
        return state

    # seule une commande pas encore confirmée peut être annulée directement ;
    # une commande confirmée (ou au-delà) est figée, comme pour l'ajout/retrait
    if status not in MODIFIABLE_ORDER_STATUSES:
        state["response"] = f"Votre commande #{order_id} est déjà confirmée, elle ne peut plus être annulée."
        return state

    state["pending_cancel"] = {"order_id": order_id}
    state["response"] = f"Êtes-vous sûr de vouloir annuler votre commande #{order_id} ?"
    return state


def _handle_cancel_confirmation(state):

    pending = state["pending_cancel"]
    order_id = pending["order_id"]

    answer = _quick_yes_no(state["question"])
    if answer is None:
        prompt = CANCEL_ORDER_CONFIRM_PROMPT.format(question=state["question"])
        answer = llm.invoke(prompt).content.strip().lower()

    if "oui" in answer:
        items_result = supabase.table("order_items") \
            .select("id, product_id, quantite") \
            .eq("order_id", order_id) \
            .execute()

        for item in (items_result.data or []):
            _restock_product(item["product_id"], item.get("quantite", 0))

        supabase.table("orders") \
            .update({"status": "ANNULEE"}) \
            .eq("id", order_id) \
            .execute()

        supabase.table("order_items").delete().eq("order_id", order_id).execute()

        _recalculate_order_total(order_id)

        state["pending_cancel"] = None
        state["order_id"] = None
        state["response"] = f"Votre commande #{order_id} a été annulée."
        return state

    if "non" in answer:
        state["pending_cancel"] = None
        state["response"] = f"D'accord, votre commande #{order_id} n'a pas été annulée."
        return state

    state["response"] = (
        f"Merci de répondre par oui ou non : voulez-vous vraiment annuler votre commande #{order_id} ?"
    )
    return state


def _view_order(state):

    order_id = state.get("order_id")

    if not order_id:
        state["response"] = "Aucune commande en cours."
        return state

    result = supabase.table("order_items") \
        .select("product_id, quantite, products(nom, prix)") \
        .eq("order_id", order_id) \
        .execute()

    if not result.data:
        state["response"] = "Votre commande est vide."
        return state

    lines = []
    for item in result.data:
        product = item.get("products") or {}
        name = product.get("nom", f"Produit #{item['product_id']}")
        prix_unitaire = product.get("prix")
        qty = item.get("quantite", 1)
        if prix_unitaire is not None:
            total_ligne = prix_unitaire * qty
            price_str = f" = {total_ligne} TND"
        else:
            price_str = ""
        lines.append(f"- {name} x{qty}{price_str}")

    state["response"] = "Produits dans votre commande :\n" + "\n".join(lines)
    return state


def _order_total(state):

    order_id = state.get("order_id")

    if not order_id:
        state["response"] = "Aucune commande en cours."
        return state

    result = supabase.table("orders") \
        .select("id") \
        .eq("id", order_id) \
        .limit(1) \
        .execute()

    if not result.data:
        state["response"] = "Commande introuvable."
        return state

    total = _recalculate_order_total(order_id)

    state["response"] = f"Montant total de votre commande #{order_id} : {total} TND"
    return state


def _order_status(state):

    order_id = state.get("order_id")

    if not order_id:
        state["response"] = "Aucune commande en cours."
        return state

    result = supabase.table("orders") \
        .select("status") \
        .eq("id", order_id) \
        .limit(1) \
        .execute()

    if not result.data:
        state["response"] = "Commande introuvable."
        return state

    status_raw = result.data[0].get("status", "")
    label = STATUS_LABELS.get(status_raw, status_raw or "Statut inconnu")

    state["response"] = f"Votre commande #{order_id} est actuellement : {label}."
    return state


def _track_order(state):

    order_id = state.get("order_id")

    if not order_id:
        state["response"] = "Aucune commande à suivre."
        return state

    state["response"] = "Le suivi de localisation n'est pas encore disponible pour votre commande."
    return state


def _delivery_date(state):

    order_id = state.get("order_id")

    if not order_id:
        state["response"] = "Aucune commande en cours."
        return state

    result = supabase.table("orders") \
        .select("status, created_at") \
        .eq("id", order_id) \
        .limit(1) \
        .execute()

    if not result.data:
        state["response"] = "Commande introuvable."
        return state

    status = result.data[0].get("status", "")

    if status == "LIVREE":
        state["response"] = f"Votre commande #{order_id} a déjà été livrée."
    elif status in ("EN_EN_APPELLE", "CONFIRMER", "EN_COURS"):
        state["response"] = (
            f"Votre commande #{order_id} est en cours de traitement. "
            "La date de livraison sera disponible une fois expédiée."
        )
    elif status == "EN_ROUTE":
        state["response"] = (
            f"Votre commande #{order_id} est en route. "
            "Veuillez contacter le transporteur pour la date exacte."
        )
    else:
        state["response"] = "Impossible de déterminer la date de livraison."

    return state


# =========================
# FALLBACK
# =========================

def fallback(state):

    state["response"] = (
        "Je n'ai pas compris votre demande. Vous pouvez me demander des informations "
        "sur une entreprise (horaires, adresse...) ou sur votre commande "
        "(statut, contenu, livraison...)."
    )
    return state


# =========================
# ROUTING
# =========================

def route(state):

    intent = state["intent"].strip().lower()

    # une confirmation de retrait de produit ou d'annulation de commande en
    # attente (oui/non) prime sur tout : "oui" ressemblerait à continue_faq,
    # "non" à restart, alors que les deux doivent être résolus par order_agent
    if state.get("pending_removal") or state.get("pending_cancel"):
        return "order_agent"

    # une commande vient d'être initiée et attend son premier produit : toute
    # réponse doit rester dans order_agent, pas être mal classée ailleurs
    if state.get("pending_new_order"):
        return "order_agent"

    # une liste de produits a été affichée (view_products) et le client répond
    # pour en commander un : on capte les sélections (numéro, accord, "ajoute le
    # premier", "confirme"...) AVANT awaiting_order_action pour que la sélection
    # par numéro de tuile continue de fonctionner même après un premier ajout.
    # Si le message n'est finalement pas une sélection de produit,
    # add_from_products délègue lui-même au flux order habituel.
    if state.get("pending_products"):
        q = state["question"].strip().lower()
        # on ne capte QUE de vraies sélections de produit dans la liste :
        # - un message contenant un chiffre ("1", "ajoute le 2", "2 du 3")
        # - un verbe d'ajout explicite ("ajoute", "prends")
        # - un message non classé / une simple confirmation ("oui")
        # Les messages de GESTION de commande (voir, confirmer, total, statut...),
        # d'intent "order", ne sont PAS captés ici : ils repartent vers
        # order_agent, ce qui évite tout ré-ajout fantôme du produit.
        has_digit = bool(re.search(r"\d", q))
        add_signal = "ajoute" in q or "ajouter" in q or "prends" in q or "prend" in q
        if has_digit or add_signal or intent in ("", "unknown", "continue_faq"):
            return "add_from_products"

    # on vient de demander "ajouter un autre produit ou confirmer ?" : un nom
    # de produit sans verbe (ex: "modern tunisian stories") ressemble à un
    # domaine d'entreprise pour le routeur principal, donc on force le
    # contexte commande plutôt que de le laisser mal classer.
    # EXCEPTION : certaines intentions explicites (voir les produits, choisir
    # une entreprise, recommencer) doivent pouvoir SORTIR du contexte commande,
    # sinon le client reste piégé si aucune commande n'est jamais confirmée.
    if state.get("awaiting_order_action"):
        if not any(k in intent for k in ("view_products", "suggest_product", "company_select", "restart")):
            return "order_agent"

    if "company_search" in intent:
        return "company_search"

    if "company_select" in intent:
        return "company_select"

    if "view_products" in intent:
        return "view_products"

    if "suggest_product" in intent:
        return "suggest_product"

    if "continue_faq" in intent:
        # "oui / ok / bien sûr" sans entreprise encore choisie n'est pas une
        # confirmation de "continuer sur la même entreprise" : c'est une
        # confirmation de sélection parmi les résultats de recherche en attente
        if not state.get("company_id") and state.get("pending_companies"):
            return "company_select"
        return "confirm_continue"

    if "restart" in intent:
        return "restart"

    if "order" in intent:
        return "order_agent"

    if "faq" in intent:
        return "faq"

    if state.get("pending_orders"):
        return "order_agent"

    if not state.get("company_id") and state.get("pending_companies"):
        return "company_select"

    if state.get("company_id"):
        return "faq"

    return "fallback"


# =========================
# GRAPH
# =========================

builder = StateGraph(State)

builder.add_node("orchestrator", orchestrator)
builder.add_node("company_search", company_search)
builder.add_node("company_select", company_select)
builder.add_node("view_products", view_products)
builder.add_node("add_from_products", add_from_products)
builder.add_node("suggest_product", suggest_product)
builder.add_node("faq", faq)
builder.add_node("ask_continue", ask_continue)
builder.add_node("confirm_continue", confirm_continue)
builder.add_node("restart", restart)
builder.add_node("order_agent", order_agent)
builder.add_node("fallback", fallback)

builder.set_entry_point("orchestrator")

builder.add_conditional_edges("orchestrator", route)

builder.add_edge("company_search", END)
builder.add_edge("company_select", END)
builder.add_edge("view_products", END)
builder.add_edge("add_from_products", END)
builder.add_edge("suggest_product", END)
builder.add_edge("faq", "ask_continue")
builder.add_edge("ask_continue", END)
builder.add_edge("confirm_continue", END)
builder.add_edge("restart", END)
builder.add_edge("order_agent", END)
builder.add_edge("fallback", END)

graph = builder.compile()
