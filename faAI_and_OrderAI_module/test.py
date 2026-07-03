from flask import Flask, request, jsonify, render_template
from graph import graph
from services import supabase
from auth import get_session_from_request, AuthError
import uuid
import secrets
import hashlib
from datetime import datetime, timezone, timedelta

app = Flask(__name__)


# =========================
# HELPERS SESSION SUPABASE
# =========================

def get_session(session_id):
    result = supabase.table("sessions") \
        .select("company_id, company_name, order_id, user_id, pending_orders, pending_question, pending_companies, pending_removal, pending_new_order, awaiting_order_action, pending_cancel") \
        .eq("session_id", session_id) \
        .limit(1) \
        .execute()
    return result.data[0] if result.data else {}


def save_session(session_id, company_id, company_name, order_id, user_id,
                  pending_orders=None, pending_question=None, pending_companies=None,
                  pending_removal=None, pending_new_order=None, awaiting_order_action=None,
                  pending_cancel=None):
    now = datetime.now(timezone.utc).isoformat()
    payload = {
        "company_id": company_id,
        "company_name": company_name,
        "order_id": order_id,
        "user_id": user_id,
        "pending_orders": pending_orders,
        "pending_question": pending_question,
        "pending_companies": pending_companies,
        "pending_removal": pending_removal,
        "pending_new_order": pending_new_order,
        "awaiting_order_action": awaiting_order_action,
        "pending_cancel": pending_cancel,
        "updated_at": now
    }

    existing = supabase.table("sessions") \
        .select("session_id") \
        .eq("session_id", session_id) \
        .limit(1) \
        .execute()

    if existing.data:
        supabase.table("sessions") \
            .update(payload) \
            .eq("session_id", session_id) \
            .execute()
    else:
        supabase.table("sessions") \
            .insert({**payload, "session_id": session_id}) \
            .execute()


# =========================
# INTERFACE DE TEST
# =========================

@app.route("/", methods=["GET"])
def index():
    return render_template("index.html")


# Route de dev qui crée directement une ligne user_sessions et renvoie le
# token en clair, pour éviter de le générer et l'insérer à la main à chaque
# test. Ne pas exposer cette route en production (aucune vérification
# d'identité : n'importe qui pourrait se créer une session pour n'importe
# quel user_id/business_id).
@app.route("/dev/create-test-session", methods=["POST"])
def create_test_session():

    try:

        data = request.get_json()
        user_id = data.get("user_id")
        business_id = data.get("business_id")

        if not user_id or not business_id:
            return jsonify({"error": "user_id et business_id sont requis."}), 400

        token = secrets.token_urlsafe(32)
        token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
        expires_at = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()

        supabase.table("user_sessions").insert({
            "user_id": user_id,
            "business_id": business_id,
            "token_hash": token_hash,
            "expires_at": expires_at
        }).execute()

        return jsonify({"token": token})

    except Exception as e:

        return jsonify({"error": str(e)}), 500


# =========================
# CHAT
# =========================

@app.route("/chat", methods=["POST"])
def chat():

    try:

        try:
            user_id, session_business_id = get_session_from_request(request)
        except AuthError as e:
            return jsonify({"error": str(e)}), 401

        data = request.get_json()
        question = data.get("message", "")
        session_id = data.get("session_id") or str(uuid.uuid4())

        session = get_session(session_id)

        # Un session_id réutilisé par un autre utilisateur authentifié repart de zéro
        # (comparaison en texte : user_id peut revenir en int depuis user_sessions
        # mais en str depuis la colonne text de sessions)
        if session.get("user_id") and str(session.get("user_id")) != str(user_id):
            session = {}

        # L'utilisateur répond au numéro d'une commande proposée au tour précédent
        pending_orders = session.get("pending_orders")
        if pending_orders and question.strip().isdigit():

            choice = int(question.strip())

            if not (1 <= choice <= len(pending_orders)):
                return jsonify({
                    "response": f"Merci de répondre avec un numéro entre 1 et {len(pending_orders)}.",
                    "company": session.get("company_name"),
                    "cost": 0.0,
                    "session_id": session_id
                })

            selected_order_id = pending_orders[choice - 1]

            result = graph.invoke({
                "question": session.get("pending_question") or question,
                "intent": "",
                "response": "",
                "company_id": session.get("company_id"),
                "company_name": session.get("company_name"),
                "cost": 0.0,
                "order_id": selected_order_id,
                "user_id": user_id,
                "session_business_id": session_business_id,
                "pending_orders": None,
                "pending_companies": session.get("pending_companies"),
                "pending_removal": session.get("pending_removal"),
                "pending_new_order": session.get("pending_new_order"),
                "awaiting_order_action": session.get("awaiting_order_action"),
                "pending_cancel": session.get("pending_cancel")
            })

            save_session(
                session_id,
                result.get("company_id"),
                result.get("company_name"),
                selected_order_id,
                user_id,
                # on garde la même liste sélectionnable : l'utilisateur peut
                # choisir un autre numéro de la même liste au tour suivant
                pending_orders=pending_orders,
                pending_question=session.get("pending_question"),
                pending_companies=result.get("pending_companies"),
                pending_removal=result.get("pending_removal"),
                pending_new_order=result.get("pending_new_order"),
                awaiting_order_action=result.get("awaiting_order_action"),
                pending_cancel=result.get("pending_cancel")
            )

            return jsonify({
                "response": result["response"],
                "company": result.get("company_name"),
                "cost": result["cost"],
                "session_id": session_id
            })

        result = graph.invoke({
            "question": question,
            "intent": "",
            "response": "",
            "company_id": session.get("company_id"),
            "company_name": session.get("company_name"),
            "cost": 0.0,
            "order_id": session.get("order_id"),
            "user_id": user_id,
            "session_business_id": session_business_id,
            "pending_orders": None,
            "pending_companies": session.get("pending_companies"),
            "pending_removal": session.get("pending_removal"),
            "pending_new_order": session.get("pending_new_order"),
            "awaiting_order_action": session.get("awaiting_order_action"),
            "pending_cancel": session.get("pending_cancel")
        })

        save_session(
            session_id,
            result.get("company_id"),
            result.get("company_name"),
            result.get("order_id"),
            user_id,
            pending_orders=result.get("pending_orders"),
            pending_question=result.get("pending_question") if result.get("pending_orders") else None,
            pending_companies=result.get("pending_companies"),
            pending_removal=result.get("pending_removal"),
            pending_new_order=result.get("pending_new_order"),
            awaiting_order_action=result.get("awaiting_order_action"),
            pending_cancel=result.get("pending_cancel")
        )

        return jsonify({
            "response": result["response"],
            "company": result.get("company_name"),
            "cost": result["cost"],
            "session_id": session_id
        })

    except Exception as e:

        return jsonify({"error": str(e)}), 500


# =========================
# TEST ORDER AGENT
# =========================
# Route de dev qui contourne le token opaque : accepte soit un order_id
# explicite (debug ciblé), soit un user_id + business_id pour tester la
# résolution automatique de commande telle qu'elle se comporte réellement
# pour un client connecté (business_id simule ici user_sessions.business_id).
# Ne pas exposer cette route en production.

@app.route("/test-order", methods=["POST"])
def test_order():

    try:

        data = request.get_json()
        question = data.get("message", "")
        company_id = data.get("company_id")
        user_id = data.get("user_id")
        session_business_id = data.get("business_id")
        order_id = data.get("order_id")

        result = graph.invoke({
            "question": question,
            "intent": "",
            "response": "",
            "company_id": company_id,
            "company_name": None,
            "cost": 0.0,
            "order_id": order_id,
            "user_id": user_id,
            "session_business_id": session_business_id,
            "pending_orders": None
        })

        return jsonify({
            "response": result["response"],
            "intent": result.get("intent"),
            "order_id": result.get("order_id"),
            "pending_orders": result.get("pending_orders")
        })

    except Exception as e:

        return jsonify({"error": str(e)}), 500


if __name__ == "__main__":

    app.run(
        host="0.0.0.0",
        port=5000,
        debug=True
    )
