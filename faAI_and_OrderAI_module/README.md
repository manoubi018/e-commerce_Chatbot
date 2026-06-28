# FAQ AI Module

Chatbot intelligent basé sur un workflow agentique pour aider les utilisateurs à trouver des informations sur des entreprises (horaires, contact, adresse, réseaux sociaux...).

## Architecture

Le module repose sur un **graphe d'état LangGraph** avec routage conditionnel basé sur l'intention détectée par un LLM.

```
Utilisateur
    │
    ▼
[orchestrator] ── détecte l'intention ──► [company_search]
                                         [company_select]
                                         [faq]
                                         [ask_continue]
                                         [confirm_continue]
                                         [restart]
```

### Flux de conversation

1. L'utilisateur envoie une question (ex: *"Cherche un restaurant tunisien"*)
2. L'`orchestrator` classifie l'intention via GPT-4o-mini
3. Le nœud correspondant est exécuté (recherche, sélection, FAQ...)
4. La réponse est renvoyée à l'utilisateur avec le contexte de session

## Stack technique

| Composant | Technologie |
|-----------|-------------|
| API REST | Flask |
| Orchestration | LangGraph + LangChain |
| LLM | OpenAI GPT-4o-mini |
| Base de données | Supabase (PostgreSQL) |
| Langage | Python 3 |

## Structure du projet

```
faq_ai_module/
├── graph.py          # Machine à états LangGraph (nœuds + routage)
├── prompt.py         # Template de prompt pour la classification d'intention
├── services.py       # Initialisation des clients LLM et Supabase
├── utils.py          # Normalisation de texte + mapping colonnes FAQ
├── test.py           # Script de test et point d'entrée Flask
├── requirements.txt  # Dépendances Python
└── .env              # Variables d'environnement (non versionné)
```

## Prérequis

- Python 3.10+
- Un compte [OpenAI](https://platform.openai.com/) avec accès à GPT-4o-mini
- Un projet [Supabase](https://supabase.com/) avec une table `businesses`

### Schéma de la table `businesses`

```sql
CREATE TABLE businesses (
  id            SERIAL PRIMARY KEY,
  name          TEXT,
  domain        TEXT,
  telephone     TEXT,
  horaires      TEXT,
  adresse       TEXT,
  email         TEXT,
  facebook_url  TEXT,
  instagram_url TEXT,
  tiktok_url    TEXT,
  retour        TEXT
);
```

## Installation

```bash
# Cloner le dépôt
git clone https://github.com/manoubi018/e-commerce_Chatbot.git
cd faq_ai_module

# Créer et activer l'environnement virtuel
python -m venv .venv
.venv\Scripts\activate        # Windows
# source .venv/bin/activate   # Linux/macOS

# Installer les dépendances
pip install -r requirements.txt
```

## Configuration

Créer un fichier `.env` à la racine du projet :

```env
OPENAI_API_KEY=sk-...
SUPABASE_URL=https://<project>.supabase.co
SUPABASE_KEY=<anon-or-service-key>
```

## Utilisation

### Lancer le serveur Flask

```bash
python test.py
```

Le serveur démarre sur `http://localhost:5000`.

### Endpoint

```
POST /chat
```

**Corps de la requête :**

```json
{
  "message": "Quels sont les horaires du magasin ?",
  "session_id": "abc123"
}
```

**Réponse :**

```json
{
  "response": "Le magasin est ouvert du lundi au vendredi de 9h à 18h.",
  "company": "Mon Entreprise",
  "cost": 0.0002,
  "session_id": "abc123"
}
```

> Le `session_id` est optionnel. S'il n'est pas fourni, un identifiant est généré automatiquement. Il permet de maintenir le contexte de la conversation (entreprise sélectionnée) entre plusieurs appels.

### Intentions reconnues

| Intention | Description |
|-----------|-------------|
| `company_search` | Recherche d'une entreprise par domaine |
| `company_select` | Sélection d'une entreprise dans une liste |
| `faq` | Question sur l'entreprise sélectionnée |
| `continue_faq` | Question de suivi sur la même entreprise |
| `restart` | Recommencer avec une nouvelle entreprise |
| `unknown` | Intention non reconnue |

### Questions FAQ supportées

- Téléphone / Contact
- Horaires d'ouverture
- Adresse
- Email
- Réseaux sociaux (Facebook, Instagram, TikTok)
- Politique de retour

## Dépendances

```
flask
python-dotenv
langgraph
langchain
langchain-openai
supabase
openai
```
