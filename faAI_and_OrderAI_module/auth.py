import hashlib
from datetime import datetime, timezone

from services import supabase


class AuthError(Exception):
    pass


def get_session_from_request(request):
    """Vérifie le token opaque envoyé par le client contre user_sessions.

    Le client envoie son token en clair via 'Authorization: Bearer <token>'.
    On ne compare jamais le token en clair : on le hash (SHA-256) et on
    cherche la ligne correspondante dans user_sessions (token_hash).

    Retourne (user_id, business_id).
    """

    auth_header = request.headers.get("Authorization", "")

    if not auth_header.startswith("Bearer "):
        raise AuthError("Token d'authentification manquant.")

    token = auth_header.split(" ", 1)[1].strip()

    if not token:
        raise AuthError("Token d'authentification manquant.")

    token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()

    result = supabase.table("user_sessions") \
        .select("user_id, business_id, expires_at, revoked_at") \
        .eq("token_hash", token_hash) \
        .limit(1) \
        .execute()

    if not result.data:
        raise AuthError("Token invalide.")

    session = result.data[0]

    if session.get("revoked_at"):
        raise AuthError("Session révoquée.")

    expires_at = session.get("expires_at")
    if expires_at:
        expires = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
        if expires <= datetime.now(timezone.utc):
            raise AuthError("Session expirée.")

    user_id = session.get("user_id")
    business_id = session.get("business_id")

    if not user_id:
        raise AuthError("Session invalide : identifiant utilisateur manquant.")

    supabase.table("user_sessions") \
        .update({"last_seen_at": datetime.now(timezone.utc).isoformat()}) \
        .eq("token_hash", token_hash) \
        .execute()

    return user_id, business_id
