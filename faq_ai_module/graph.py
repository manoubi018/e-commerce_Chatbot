from typing import TypedDict, Optional

from langgraph.graph import StateGraph, END

from services import llm, supabase
from utils import detect_column, normalize
from prompt import ROUTER_PROMPT


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

    q = state["question"].lower()
    keywords = q.split()

    key = normalize(keywords[-1]) if keywords else normalize(q)

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

builder.set_entry_point("orchestrator")

builder.add_conditional_edges("orchestrator", route)

builder.add_edge("company_search", END)
builder.add_edge("company_select", END)
builder.add_edge("faq", "ask_continue")
builder.add_edge("ask_continue", END)
builder.add_edge("confirm_continue", END)
builder.add_edge("restart", END)

graph = builder.compile()