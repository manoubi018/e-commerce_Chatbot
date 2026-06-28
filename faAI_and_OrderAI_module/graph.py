from typing import TypedDict, Optional

from langgraph.graph import StateGraph, END

from services import llm, supabase
from utils import detect_column, normalize
from prompt import ROUTER_PROMPT, ORDER_ROUTER_PROMPT


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


# =========================
# ROUTER
# =========================

def orchestrator(state):

    prompt = ROUTER_PROMPT.format(
        question=state["question"]
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
        "Réponds uniquement par le mot clé du domaine, rien d'autre.\n"
        "Exemples :\n"
        "- 'je cherche une boulangerie sympa' → boulangerie\n"
        "- 'j aimerais trouver une entreprise en informatique' → informatique\n"
        "- 'je cherche une entreprise spécialisée en pâtisserie' → patisserie\n"
        f"Phrase : {state['question']}"
    )
    key = llm.invoke(extract_prompt).content.strip().lower()
    key = normalize(key)

    result = supabase.table("businesses") \
        .select("name") \
        .ilike("domain", f"%{key}%") \
        .execute()

    state["response"] = result.data if result.data else "Aucune entreprise trouvée"

    return state


# =========================
# COMPANY SELECT
# =========================

def company_select(state):

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
# FAQ AGENT
# =========================

def faq(state):

    company_id = state.get("company_id")

    if not company_id:
        state["response"] = "Veuillez d'abord choisir une entreprise."
        return state

    column = detect_column(state["question"])

    # FIX: sécurisation None + colonne invalide
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
# ORDER AGENT
# =========================

def order_agent(state):

    prompt = ORDER_ROUTER_PROMPT.format(question=state["question"])
    result = llm.invoke(prompt)
    sub_intent = result.content.strip().lower()

    if "add_product" in sub_intent:
        return _add_product(state)
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
    else:
        state["response"] = "Je n'ai pas compris votre demande concernant la commande."
        return state


def _add_product(state):

    order_id = state.get("order_id")
    company_id = state.get("company_id")

    if not order_id:
        state["response"] = "Aucune commande en cours. Veuillez d'abord créer une commande."
        return state

    # extraction du nom du produit et de la quantité depuis la question
    extract_prompt = (
        "Extrais le nom du produit et la quantité mentionnés dans cette phrase.\n"
        "Réponds uniquement au format : NOM_PRODUIT|QUANTITE\n"
        "Si aucune quantité n'est mentionnée, mets 1 par défaut.\n"
        "Exemples :\n"
        "- 'ajoute 2 paquets de lait' → lait|2\n"
        "- 'je veux 3 bouteilles d eau' → eau|3\n"
        "- 'ajoute du pain' → pain|1\n"
        f"Phrase : {state['question']}"
    )
    extracted = llm.invoke(extract_prompt).content.strip()

    # Parser la réponse du LLM
    parts = extracted.split("|")
    product_name = parts[0].strip()
    try:
        quantite_demandee = int(parts[1].strip()) if len(parts) > 1 else 1
    except ValueError:
        quantite_demandee = 1

    if quantite_demandee < 1:
        quantite_demandee = 1

    # Chercher le produit et vérifier le stock
    result = supabase.table("products") \
        .select("id, nom, prix, stock") \
        .ilike("nom", f"%{product_name}%") \
        .limit(1) \
        .execute()

    if not result.data:
        state["response"] = f"Produit '{product_name}' introuvable dans le catalogue."
        return state

    product = result.data[0]
    stock_disponible = product.get("stock", 0)

    if stock_disponible <= 0:
        state["response"] = f"Le produit '{product['nom']}' est actuellement en rupture de stock."
        return state

    if quantite_demandee > stock_disponible:
        state["response"] = (
            f"Stock insuffisant pour '{product['nom']}'. "
            f"Quantité demandée : {quantite_demandee}, stock disponible : {stock_disponible}."
        )
        return state

    supabase.table("order_items").insert({
        "order_id": order_id,
        "business_id": company_id,
        "product_id": product["id"],
        "quantite": quantite_demandee
    }).execute()

    state["response"] = (
        f"{quantite_demandee} x '{product['nom']}' ajouté(s) à votre commande "
        f"(prix unitaire : {product['prix']} €)."
    )
    return state


def _confirm_order(state):

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

    if status == "confirmed":
        state["response"] = f"Votre commande #{order_id} est déjà confirmée."
    elif status == "processing":
        state["response"] = f"Votre commande #{order_id} est déjà en cours de préparation, elle ne peut plus être modifiée."
    elif status == "shipped":
        state["response"] = f"Votre commande #{order_id} est déjà expédiée, il est trop tard pour la confirmer."
    elif status == "delivered":
        state["response"] = f"Votre commande #{order_id} a déjà été livrée."
    elif status == "cancelled":
        state["response"] = f"Votre commande #{order_id} a été annulée, elle ne peut pas être confirmée."
    else:
        supabase.table("orders") \
            .update({"status": "confirmed"}) \
            .eq("id", order_id) \
            .execute()
        state["response"] = (
            f"Votre commande #{order_id} a été confirmée avec succès. "
            "Vous recevrez une notification dès son expédition."
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
            price_str = f" = {total_ligne} €"
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
        .select("total") \
        .eq("id", order_id) \
        .limit(1) \
        .execute()

    if not result.data:
        state["response"] = "Commande introuvable."
        return state

    total = result.data[0].get("total")
    if total is None:
        state["response"] = "Le montant total n'est pas encore calculé."
        return state

    state["response"] = f"Montant total de votre commande #{order_id} : {total} €"
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

    status_labels = {
        "pending":    "En attente de traitement",
        "confirmed":  "Confirmée",
        "processing": "En cours de préparation",
        "shipped":    "Expédiée",
        "delivered":  "Livrée",
        "cancelled":  "Annulée",
    }
    status_raw = result.data[0].get("status", "")
    label = status_labels.get(status_raw, status_raw or "Statut inconnu")

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

    if status == "delivered":
        state["response"] = f"Votre commande #{order_id} a déjà été livrée."
    elif status in ("pending", "confirmed", "processing"):
        state["response"] = (
            f"Votre commande #{order_id} est en cours de traitement. "
            "La date de livraison sera disponible une fois expédiée."
        )
    elif status == "shipped":
        state["response"] = (
            f"Votre commande #{order_id} est en route. "
            "Veuillez contacter le transporteur pour la date exacte."
        )
    else:
        state["response"] = "Impossible de déterminer la date de livraison."

    return state


# =========================
# ROUTING
# =========================

def route(state):

    intent = state["intent"].strip().lower()

    if "company_search" in intent:
        return "company_search"

    if "company_select" in intent:
        return "company_select"

    if "continue_faq" in intent:
        return "confirm_continue"

    if "restart" in intent:
        return "restart"

    if "order" in intent:
        return "order_agent"

    if "faq" in intent:
        return "faq"

    if state.get("company_id"):
        return "faq"

    return END


# =========================
# GRAPH
# =========================

builder = StateGraph(State)

builder.add_node("orchestrator", orchestrator)
builder.add_node("company_search", company_search)
builder.add_node("company_select", company_select)
builder.add_node("faq", faq)
builder.add_node("ask_continue", ask_continue)
builder.add_node("confirm_continue", confirm_continue)
builder.add_node("restart", restart)
builder.add_node("order_agent", order_agent)

builder.set_entry_point("orchestrator")

builder.add_conditional_edges("orchestrator", route)

builder.add_edge("company_search", END)
builder.add_edge("company_select", END)
builder.add_edge("faq", "ask_continue")
builder.add_edge("ask_continue", END)
builder.add_edge("confirm_continue", END)
builder.add_edge("restart", END)
builder.add_edge("order_agent", END)

graph = builder.compile()
