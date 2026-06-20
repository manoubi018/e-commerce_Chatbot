from flask import Flask, request, jsonify
from graph import graph
import uuid

app = Flask(__name__)

sessions = {}


@app.route("/chat", methods=["POST"])
def chat():

    try:

        data = request.get_json()
        question = data.get("message", "")
        session_id = data.get("session_id") or str(uuid.uuid4())

        session = sessions.get(session_id, {})

        result = graph.invoke({
            "question": question,
            "intent": "",
            "response": "",
            "company_id": session.get("company_id"),
            "company_name": session.get("company_name"),
            "cost": 0.0
        })

        sessions[session_id] = {
            "company_id": result.get("company_id"),
            "company_name": result.get("company_name")
        }

        return jsonify({
            "response": result["response"],
            "company": result.get("company_name"),
            "cost": result["cost"],
            "session_id": session_id
        })

    except Exception as e:

        return jsonify({"error": str(e)}), 500


if __name__ == "__main__":

    app.run(
        host="0.0.0.0",
        port=5000,
        debug=True
    )
