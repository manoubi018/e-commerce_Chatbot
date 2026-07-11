import re
from datetime import datetime
from typing import TypedDict, Optional

from langgraph.graph import StateGraph, END

from services import llm, supabase
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

def orchestrator(state):

    # le routeur a besoin de savoir si une entreprise est déjà sélectionnée :
    # "je voudrais un liqueur" = chercher un PRODUIT si une entreprise est
    # choisie, mais chercher une ENTREPRISE (domaine) sinon.
    company_selected = "oui" if state.get("company_id") else "non"

    prompt = ROUTER_PROMPT.format(
        question=state["question"],
        company_selected=company_selected
    )

    result = llm.invoke(prompt)

    state["intent"] = result.content.strip().lower()

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

def company_select(state):

    pending = state.get("pending_companies")

    if pending:
        companies_list = "\n".join(
            f"{i}. {c['name']}" for i, c in enumerate(pending, start=1)
        )
        prompt = COMPANY_CONFIRM_PROMPT.format(
            companies_list=companies_list,
            question=state["question"]
        )
        result = llm.invoke(prompt).content.strip().lower()
        match = re.search(r"\d+", result)

        if not match:
            state["response"] = (
                "Je n'ai pas compris quelle entreprise vous souhaitez choisir. "
                "Merci de préciser son nom ou sa position dans la liste."
            )
            return state

        position = int(match.group())
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
# Nouvelle fonctionnalité : le client décrit une idée / un besoin de produit.
# On compare cette idée (via le LLM) au nom et à la description de tous les
# produits de l'entreprise, et on affiche ceux qui correspondent, sous forme de
# tuiles sélectionnables (comme view_products) pour pouvoir en commander un.

def _business_name_of(product):
    return (product.get("businesses") or {}).get("name")


# mots vides français ignorés lors du pré-filtrage par mots-clés
_FR_STOPWORDS = {
    "un", "une", "des", "du", "de", "la", "le", "les", "pour", "avec", "et",
    "ou", "je", "veux", "voudrais", "cherche", "il", "me", "faut", "truc",
    "chose", "quelque", "au", "aux", "en", "mon", "ma", "mes", "qui", "que",
    "sur", "dans", "par", "plus", "tres", "assez", "aussi", "avoir", "etre",
}


def _keyword_prefilter(question, products):
    """Réduit la liste des produits à ceux dont le nom ou la description
    contient au moins un mot-clé du message, AVANT l'appel LLM (gain de vitesse
    sur un gros catalogue). Si rien ne ressort (idée exprimée en synonymes), on
    retombe sur la liste complète pour ne pas perdre en pertinence."""

    tokens = [normalize(t) for t in re.findall(r"\w+", question.lower())]
    tokens = [t for t in tokens if len(t) >= 3 and t not in _FR_STOPWORDS]

    if not tokens:
        return products

    candidates = []
    for p in products:
        haystack = normalize(f"{p.get('nom', '')} {p.get('description', '')}")
        if any(t in haystack for t in tokens):
            candidates.append(p)

    return candidates if candidates else products


def suggest_product(state):

    # action de découverte : on sort de tout contexte commande en cours
    state["awaiting_order_action"] = None

    # recherche dans TOUS les produits actifs de toutes les entreprises ;
    # on joint le nom de l'entreprise pour situer chaque produit
    result = supabase.table("products") \
        .select("id, nom, prix, description, image, stock, business_id, businesses(name)") \
        .eq("active", True) \
        .execute()

    products = result.data or []

    if not products:
        state["response"] = "Aucun produit n'est disponible pour le moment."
        return state

    # pré-filtre bon marché pour n'envoyer au LLM que les candidats plausibles
    search_pool = _keyword_prefilter(state["question"], products)

    products_list = "\n".join(
        f"{i}. {p.get('nom', 'Produit')} ({_business_name_of(p) or 'entreprise inconnue'}) : "
        f"{p.get('description') or 'sans description'}"
        for i, p in enumerate(search_pool, start=1)
    )
    prompt = PRODUCT_SUGGESTION_PROMPT.format(
        suggestion=state["question"],
        products_list=products_list
    )
    matches_raw = llm.invoke(prompt).content.strip().lower()

    positions = []
    if "none" not in matches_raw:
        for tok in re.findall(r"\d+", matches_raw):
            pos = int(tok)
            if 1 <= pos <= len(search_pool) and pos not in positions:
                positions.append(pos)

    matched = [search_pool[p - 1] for p in positions]

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


def _detect_faq_column(question):
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

    result = supabase.table("businesses") \
    .select(column) \
    .eq("id", company_id) \
    .limit(1) \
    .execute()

    if result.data and result.data[0].get(column) is not None:
        state["response"] = result.data[0][column]
    else:
        state["response"] = "Information non disponible"

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


def _extract_requested_position(question):
    """Détecte via le LLM une référence explicite à une position de commande dans
    le message (ex: "commande 2", "commande n°2", "ma deuxième commande", "la
    première") pour permettre à l'utilisateur de désigner directement une commande
    précise, même si une autre commande est déjà en cache pour cette session."""

    prompt = ORDER_POSITION_PROMPT.format(question=question)
    result = llm.invoke(prompt).content.strip().lower()

    match = re.search(r"\d+", result)
    return int(match.group()) if match else None


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

    prompt = ORDER_ROUTER_PROMPT.format(question=state["question"])
    result = llm.invoke(prompt)
    sub_intent = result.content.strip().lower()

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

    items_result = supabase.table("order_items") \
        .select("id, quantite, product_id, products(nom, prix)") \
        .eq("order_id", order_id) \
        .execute()

    match = None
    for item in (items_result.data or []):
        nom = (item.get("products") or {}).get("nom", "")
        if product_name.lower() in nom.lower():
            match = item
            break

    if not match:
        state["response"] = f"'{product_name}' ne fait pas partie de votre commande."
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


def _handle_removal_confirmation(state):

    pending = state["pending_removal"]

    if pending.get("last_item"):
        return _handle_last_item_action(state, pending)

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
