import re
import unicodedata


def normalize(text: str) -> str:
    return ''.join(
        c for c in unicodedata.normalize('NFD', text)
        if unicodedata.category(c) != 'Mn'
    ).lower()

# =========================
# COLUMN MAPPING (ORDERED)
# =========================

FAQ_COLUMN_MAPPING = [
    # téléphone / contact
    ("téléphone", "phone"),
    ("telephone", "phone"),
    ("contact", "phone"),
    ("numéro", "phone"),
    ("numero", "phone"),
    ("appeler", "phone"),

    # horaires
    ("horaires", "horaires"),
    ("horaire", "horaires"),
    ("ouvert", "horaires"),

    # adresse
    ("adresse", "address"),
    ("localisation", "address"),
    ("où", "address"),

    # email
    ("email", "email"),
    ("mail", "email"),

    # réseaux sociaux
    ("facebook", "facebook_url"),
    ("instagram", "instagram_url"),
    ("tiktok", "tiktok_url"),

    # statut / ouverture
    ("ouvert", "status"),
    ("ouverte", "status"),
    ("active", "status"),
    ("actif", "status"),
    ("activité", "status"),
    ("statut", "status"),
    ("fermé", "status"),
    ("fermée", "status"),

    # autres
    ("retour", "retour"),
]


# =========================
# DETECT COLUMN FUNCTION
# =========================

def detect_column(question: str):

    q = question.lower()

    for keyword, column in FAQ_COLUMN_MAPPING:

        # match mot complet (évite faux positifs)
        if re.search(rf"\b{re.escape(keyword)}\b", q):
            return column

    return None