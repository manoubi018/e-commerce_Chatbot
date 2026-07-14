# FA AI & Order AI Module

Chatbot intelligent basé sur un workflow agentique (LangGraph) qui gère deux domaines :

- **FAQ** : rechercher une entreprise et répondre aux questions la concernant (horaires, adresse, contact, réseaux sociaux...)
- **Order AI** : créer une commande, ajouter/retirer des produits, la confirmer ou l'annuler, consulter son statut, son contenu, son total et sa livraison

Le routage entre les deux domaines, et entre les sous-actions de chacun, est décidé par un LLM (GPT-4o-mini) à partir du langage libre de l'utilisateur — aucune commande figée n'est nécessaire.

## Architecture

Le module repose sur un **graphe d'état LangGraph** avec routage conditionnel basé sur l'intention détectée.

```
Utilisateur
    │
    ▼
[orchestrator] ── détecte l'intention ──► [company_search]
                                         [company_select]
                                         [faq] ──► [ask_continue]
                                         [confirm_continue]
                                         [restart]
                                         [order_agent] ──► add_product / remove_product
                                                           create_order / cancel_order
                                                           confirm_order / view_order
                                                           order_total / order_status
                                                           track_order / delivery_date
                                                           list_orders
                                         [fallback]
```

### Flux FAQ

1. L'utilisateur cherche une entreprise par domaine (*"je cherche une boulangerie"*, ou même juste *"boulangerie"*) → `company_search`
2. Le bot affiche les entreprises trouvées et les garde en mémoire (`pending_companies`)
3. L'utilisateur confirme librement (*"ok pour celle-là"*, *"je prends la deuxième"*, *"Le Fournil"*...) → `company_select` résout la sélection via LLM contre la liste en attente
4. Les questions suivantes (horaires, adresse...) sont répondues via `faq`, avec relance automatique (`ask_continue`) pour poser une autre question sur la même entreprise

### Flux Order AI

1. **Créer une commande** (*"je veux faire une nouvelle commande"*) : la commande n'est pas encore écrite en base — une commande ne peut exister qu'avec un total strictement positif (contrainte `orders_total_check`), donc le bot attend le premier produit
2. **Premier produit** : dès qu'un nom de produit est fourni (même sans verbe, ex: *"sirop de cerise"*), la commande est créée avec ce produit et le statut par défaut `EN_COURS`
3. **Ajouter / retirer des produits** : chaque ajout relance "ajouter un autre produit ou confirmer ?" ; chaque retrait demande confirmation. Retirer le **dernier** produit propose de le **remplacer** ou d'**annuler toute la commande** (jamais de commande vide)
4. **Confirmer** (*"confirme ma commande"*) : passe le statut à `CONFIRMER`. Au-delà, la commande est figée (plus d'ajout/retrait/annulation possible)
5. **Annuler directement** (*"annule ma commande"*, *"annule la commande 2"*) : uniquement possible tant que la commande est `EN_COURS` — restocke les produits et passe le statut à `ANNULEE`
6. **Consulter** : liste de toutes les commandes, contenu d'une commande précise (par position : *"la deuxième commande"*, ou juste un numéro en réponse à la liste), total, statut, date de livraison

## Stack technique

| Composant | Technologie |
|-----------|-------------|
| API REST | Flask |
| Orchestration | LangGraph + LangChain |
| LLM | OpenAI GPT-4o-mini |
| Base de données | Supabase (PostgreSQL) |
| Authentification | Token opaque hashé (SHA-256) contre `user_sessions` |
| Langage | Python 3 |

## Structure du projet

```
faAI_and_OrderAI_module/
├── graph.py           # Machine à états LangGraph (nœuds, routage, logique métier)
├── prompt.py          # Tous les prompts de classification et d'extraction LLM
├── services.py        # Initialisation des clients LLM (OpenAI) et Supabase
├── auth.py            # Vérification du token opaque → (user_id, business_id)
├── utils.py           # Normalisation de texte (accents, casse)
├── test.py            # Serveur Flask (routes /chat, /dev/create-test-session, /test-order)
├── templates/          # Interface web minimale de test
├── requirements.txt   # Dépendances Python
└── .env               # Variables d'environnement (non versionné)
```

## Prérequis

- Python 3.10+
- Un compte [OpenAI](https://platform.openai.com/) avec accès à GPT-4o-mini
- Un projet [Supabase](https://supabase.com/) avec les tables décrites ci-dessous

### Tables Supabase

**`businesses`** — fiches entreprises pour le FAQ (`id`, `name`, `domain`, `phone`, `horaires`, `address`, `email`, `facebook_url`, `instagram_url`, `tiktok_url`, `status`, `retour`).

**`products`** — catalogue (`id`, `nom`, `prix`, `stock`).

**`orders`** — commandes. Colonnes notables :
- `status` (enum `status_commande`, défaut `EN_COURS`) : `EN_COURS`, `CONFIRMER`, `EN_ROUTE`, `LIVREE`, `ANNULEE`, plus les statuts du workflow d'appel client (`EN_EN_APPELLE`, `APPELLE_CLIENT_1/2`, `NON_REPONDRE_CLIENT_1/2`) gérés par l'équipe, pas par le chatbot.
- `total` : contrainte `CHECK (total > 0 OR status = 'ANNULEE')` — une commande active ne peut jamais être vide.
- `telephone` (NOT NULL, rempli vide par le chatbot — non utilisé dans ce flux).

**`order_items`** — lignes de commande (`order_id`, `business_id`, `product_id`, `quantite`).

**`user_sessions`** — authentification (`user_id`, `business_id`, `token_hash`, `expires_at`, `revoked_at`, `last_seen_at`).

**`sessions`** — état de conversation par `session_id` : `company_id`, `company_name`, `order_id`, `user_id`, et les champs d'attente (`pending_orders`, `pending_question`, `pending_companies`, `pending_removal`, `pending_new_order`, `awaiting_order_action`, `pending_cancel`) qui permettent au bot de se souvenir du contexte entre deux messages HTTP indépendants.

## Installation

```bash
git clone https://github.com/manoubi018/e-commerce_Chatbot.git
cd faAI_and_OrderAI_module

python -m venv .venv
.venv\Scripts\activate        # Windows
# source .venv/bin/activate   # Linux/macOS

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

### Authentification

Le client envoie son token en clair via l'en-tête `Authorization: Bearer <token>`. Le serveur le hash (SHA-256) et le compare à `user_sessions.token_hash` pour retrouver `(user_id, business_id)` — jamais de valeur fournie directement par le client dans le message.

Pour générer un token de test :

```
POST /dev/create-test-session
{ "user_id": 1, "business_id": 1 }
```

### Endpoint principal

```
POST /chat
Authorization: Bearer <token>
```

**Corps de la requête :**

```json
{
  "message": "je veux faire une nouvelle commande",
  "session_id": "abc123"
}
```

**Réponse :**

```json
{
  "response": "Nouvelle commande initiée ! Quel produit souhaitez-vous ajouter ?",
  "company": null,
  "cost": 0.0,
  "session_id": "abc123"
}
```

> `session_id` est optionnel : s'il n'est pas fourni, un identifiant est généré et renvoyé. Il permet de maintenir le contexte (entreprise sélectionnée, commande en cours, questions en attente) entre plusieurs appels.

### Intentions reconnues (routeur principal)

| Intention | Description |
|-----------|-------------|
| `company_search` | Recherche d'une entreprise par domaine |
| `company_select` | Sélection/confirmation d'une entreprise trouvée |
| `faq` | Question sur l'entreprise sélectionnée |
| `continue_faq` | Confirmation générale (contexte-dépendante : continuer le FAQ ou confirmer une entreprise) |
| `restart` | Recommencer avec un nouveau domaine |
| `order` | Toute demande liée aux commandes (délègue à `order_agent`) |
| `unknown` | Intention non reconnue → `fallback` |

### Sous-intentions Order AI

| Intention | Description |
|-----------|-------------|
| `create_order` | Démarrer une nouvelle commande |
| `add_product` | Ajouter un produit |
| `remove_product` | Retirer un produit |
| `cancel_order` | Annuler toute la commande (uniquement si `EN_COURS`) |
| `confirm_order` | Confirmer la commande |
| `list_orders` | Vue d'ensemble de toutes les commandes |
| `view_order` | Détail d'une commande précise |
| `order_total` | Montant total (recalculé depuis `order_items`, jamais lu en cache) |
| `order_status` | Statut de la commande |
| `track_order` | Suivi de livraison |
| `delivery_date` | Date de livraison estimée |

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
