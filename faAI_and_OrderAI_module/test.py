from flask import Flask, request, jsonify
from graph import graph
from services import supabase
import uuid
from datetime import datetime, timezone

app = Flask(__name__)


# =========================
# HELPERS SESSION SUPABASE
# =========================

def get_session(session_id):
    result = supabase.table("sessions") \
        .select("company_id, company_name, order_id") \
        .eq("session_id", session_id) \
        .limit(1) \
        .execute()
    return result.data[0] if result.data else {}


def save_session(session_id, company_id, company_name, order_id):
    now = datetime.now(timezone.utc).isoformat()
    existing = supabase.table("sessions") \
        .select("session_id") \
        .eq("session_id", session_id) \
        .limit(1) \
        .execute()

    if existing.data:
        supabase.table("sessions") \
            .update({
                "company_id": company_id,
                "company_name": company_name,
                "order_id": order_id,
                "updated_at": now
            }) \
            .eq("session_id", session_id) \
            .execute()
    else:
        supabase.table("sessions") \
            .insert({
                "session_id": session_id,
                "company_id": company_id,
                "company_name": company_name,
                "order_id": order_id,
                "updated_at": now
            }) \
            .execute()


# =========================
# CHAT
# =========================

@app.route("/chat", methods=["POST"])
def chat():

    try:

        data = request.get_json()
        question = data.get("message", "")
        session_id = data.get("session_id") or str(uuid.uuid4())

        session = get_session(session_id)

        result = graph.invoke({
            "question": question,
            "intent": "",
            "response": "",
            "company_id": session.get("company_id"),
            "company_name": session.get("company_name"),
            "cost": 0.0,
            "order_id": session.get("order_id")
        })

        save_session(
            session_id,
            result.get("company_id"),
            result.get("company_name"),
            result.get("order_id")
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

@app.route("/test-order", methods=["POST"])
def test_order():

    try:

        data = request.get_json()
        question = data.get("message", "")
        order_id = data.get("order_id")
        company_id = data.get("company_id")

        result = graph.invoke({
            "question": question,
            "intent": "",
            "response": "",
            "company_id": company_id,
            "company_name": None,
            "cost": 0.0,
            "order_id": order_id
        })

        return jsonify({
            "response": result["response"],
            "intent": result.get("intent"),
            "order_id": order_id
        })

    except Exception as e:

        return jsonify({"error": str(e)}), 500


if __name__ == "__main__":

    app.run(
        host="0.0.0.0",
        port=5000,
        debug=True
    )
