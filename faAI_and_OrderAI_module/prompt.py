ROUTER_PROMPT = """
Tu es un routeur intelligent.

Tu dois classer la requête utilisateur.

INTENTS POSSIBLES :

- company_search : l'utilisateur cherche une entreprise par domaine
- company_select : l'utilisateur choisit une entreprise dans une liste
- faq : question sur une entreprise déjà sélectionnée (horaires, adresse, contact...)
- continue_faq : l'utilisateur veut continuer à poser des questions sur la même entreprise (oui, bien sûr, continue, d'accord)
- restart : l'utilisateur ne veut plus d'informations et veut recommencer (non, non merci, revenir, autre domaine)
- order : tout ce qui concerne les commandes (ajouter un produit, confirmer, suivre, total, livraison)
- unknown

RÈGLES :

- "je cherche une entreprise..." → company_search
- "donne moi une entreprise en..." → company_search
- Si le message ne contient qu'un secteur d'activité ou un domaine, seul ou
  avec un simple article ("musique", "la musique", "en musique",
  "informatique", "boulangerie"), sans autre indication → company_search.
  L'utilisateur répond simplement à la question "quel domaine vous
  intéresse ?", il n'a pas besoin de reformuler une phrase complète.
- "je choisis ...", "je prends ..." → company_select
- questions type horaires, adresse, contact, email, téléphone → faq
- "oui", "bien sûr", "continue", "d'accord", "oui merci" (SEULS, sans aucune
  question précise dans le même message) → continue_faq
- Si le message contient à la fois une confirmation ("oui", "bien sûr"...) ET
  une question précise (horaires, adresse, contact, email...), classe-le en
  faq : la question posée prime toujours sur la simple confirmation.
  Exemple : "oui je veux connaitre l'adresse merci" → faq (pas continue_faq)
- "non", "non merci", "revenir", "autre domaine", "recommencer" → restart
- "ajoute", "commande", "confirme ma commande", "où est ma commande",
  "montant total", "date de livraison", "produits de ma commande" → order

Question :
{question}

Réponds uniquement par l'intent.
"""

FAQ_COLUMN_PROMPT = """
Tu dois identifier quelle information sur une entreprise est demandée dans cette question.

COLONNES POSSIBLES :

- phone : téléphone, numéro, contact, appeler
- horaires : horaires d'ouverture, heures d'ouverture
- address : adresse, localisation, où se trouve l'entreprise
- email : adresse email, mail
- facebook_url : page Facebook
- instagram_url : compte Instagram
- tiktok_url : compte TikTok
- status : si l'entreprise est ouverte, fermée, active
- retour : politique de retour, remboursement

Sois tolérant aux fautes de frappe, pluriels et reformulations (ex: "adress",
"adresses", "ou se trouve" doivent tous être reconnus comme "address").

Question :
{question}

Réponds uniquement par le nom exact d'une des colonnes ci-dessus
(phone, horaires, address, email, facebook_url, instagram_url, tiktok_url,
status, retour), ou "unknown" si aucune ne correspond.
"""

COMPANY_CONFIRM_PROMPT = """
Tu dois déterminer quelle entreprise, parmi une liste proposée au tour précédent,
l'utilisateur confirme ou sélectionne dans son message.

ENTREPRISES PROPOSÉES :
{companies_list}

Message de l'utilisateur :
{question}

RÈGLES :
- Si une seule entreprise est proposée et que le message exprime un accord ou une
  confirmation générale (ex: "ok", "oui", "bien sûr", "ok pour cette entreprise",
  "je la prends", "c'est bon", "d'accord", "vas-y"), réponds par son numéro (1).
- Si plusieurs entreprises sont proposées, réponds par le numéro de celle
  explicitement désignée, que ce soit par son nom (même partiel ou mal
  orthographié) ou par sa position ("la première", "la deuxième", "je choisis X").
- Si le message ne confirme ni ne désigne clairement aucune entreprise de la
  liste (nouvelle recherche, question sans rapport...), réponds "none".

Réponds uniquement par le numéro ou "none", sans rien d'autre.
"""

ORDER_POSITION_PROMPT = """
Tu dois détecter si l'utilisateur désigne une commande précise par sa position
dans une liste de commandes (ex: "commande 2", "commande n°3", "ma deuxième
commande", "la première", "la 3ème").

Réponds uniquement par le numéro de position (1, 2, 3, ...) si une position
est clairement désignée. Réponds "none" si aucune position précise n'est
désignée (ex: question générale sur "ma commande" sans numéro, ou question
qui ne concerne pas une commande précise).

Sois tolérant aux fautes de frappe, accents manquants et variantes
("premiere", "1ere", "1ère" → 1 ; "deuxieme", "seconde", "2eme" → 2 ; etc.).

Exemples :
- "la commande 2" → 2
- "commande n°5" → 5
- "ma deuxième commande" → 2
- "la première commande" → 1
- "la 3ème" → 3
- "où est ma commande ?" → none
- "quel est le total ?" → none
- "confirme ma commande" → none

Question :
{question}

Réponds uniquement par le numéro ou "none", sans rien d'autre.
"""

REMOVE_CONFIRM_PROMPT = """
Tu dois déterminer si l'utilisateur confirme ou refuse de retirer un produit
de sa commande, suite à la question "Êtes-vous sûr de vouloir retirer ce
produit ?".

Message de l'utilisateur :
{question}

RÈGLES :
- Si le message confirme (ex: "oui", "oui je confirme", "vas-y", "d'accord",
  "c'est bon", "supprime-le") → réponds "oui"
- Si le message refuse (ex: "non", "non merci", "laisse-le", "annule",
  "je change d'avis") → réponds "non"
- Si le message ne répond pas clairement à cette question → réponds "unclear"

Réponds uniquement par "oui", "non" ou "unclear", sans rien d'autre.
"""

LAST_ITEM_ACTION_PROMPT = """
Tu dois déterminer ce que veut faire l'utilisateur au sujet du dernier produit
restant dans sa commande, suite à la question "Voulez-vous le remplacer par un
autre produit, ou annuler la commande entière ?".

Message de l'utilisateur :
{question}

RÈGLES :
- Si l'utilisateur veut annuler toute la commande (ex: "annule", "annule la
  commande", "annule tout", "oui annule") → réponds "annuler"
- Si l'utilisateur veut garder le produit actuel, ne rien changer (ex: "non",
  "laisse comme ça", "je change d'avis", "garde-le") → réponds "garder"
- Si l'utilisateur mentionne un autre produit pour le remplacer (ex: "remplace
  par du pain", "mets plutôt de la confiture", "du sirop de fraise à la
  place") → réponds "remplacer"
- Si ce n'est pas clair → réponds "unclear"

Réponds uniquement par "annuler", "garder", "remplacer" ou "unclear".
"""

CANCEL_ORDER_CONFIRM_PROMPT = """
Tu dois déterminer si l'utilisateur confirme ou refuse d'annuler toute sa
commande, suite à la question "Êtes-vous sûr de vouloir annuler cette
commande ?".

Message de l'utilisateur :
{question}

RÈGLES :
- Si le message confirme (ex: "oui", "oui je confirme", "vas-y", "d'accord",
  "annule-la") → réponds "oui"
- Si le message refuse (ex: "non", "non merci", "laisse-la", "je change
  d'avis") → réponds "non"
- Si le message ne répond pas clairement à cette question → réponds "unclear"

Réponds uniquement par "oui", "non" ou "unclear", sans rien d'autre.
"""

ORDER_ROUTER_PROMPT = """
Tu es un sous-routeur spécialisé dans la gestion des commandes.

INTENTS POSSIBLES :

- create_order   : l'utilisateur veut démarrer/créer une toute nouvelle commande
- add_product    : l'utilisateur veut ajouter un produit à sa commande
- remove_product : l'utilisateur veut retirer/enlever/supprimer un produit de sa commande
- confirm_order  : l'utilisateur veut confirmer/valider sa commande
- cancel_order   : l'utilisateur veut annuler toute la commande (pas juste retirer un produit)
- list_orders    : l'utilisateur veut voir la liste de TOUTES ses commandes (vue d'ensemble, pas le détail d'une seule)
- view_order     : l'utilisateur veut voir les produits d'UNE commande précise (déjà connue ou désignée)
- order_total    : l'utilisateur veut connaître le montant total
- track_order    : l'utilisateur veut savoir où est sa commande
- delivery_date  : l'utilisateur veut connaître la date de livraison prévue
- order_status   : l'utilisateur veut savoir si sa commande est confirmée, en cours, livrée etc.
- unknown

DIFFÉRENCE list_orders vs view_order : "mes commandes" (pluriel, aucune
commande précise désignée) = list_orders. "ma commande", "la commande 2",
"ma deuxième commande" (singulier, une commande précise désignée ou déjà en
contexte) = view_order.

EXEMPLES :

- "je veux faire une nouvelle commande" → create_order
- "je veux commander" → create_order
- "démarre une nouvelle commande" → create_order
- "ajoute ce produit à ma commande" → add_product
- "retire ce produit de ma commande" → remove_product
- "enlève le pain de ma commande" → remove_product
- "supprime la confiture de ma commande" → remove_product
- "confirme ma commande" → confirm_order
- "annule ma commande" → cancel_order
- "j'annule la deuxième commande" → cancel_order
- "je veux annuler la commande 2" → cancel_order
- "donne moi mes commandes" → list_orders
- "donne moi la liste de mes commandes" → list_orders
- "quelles sont mes commandes ?" → list_orders
- "je veux connaitre mes commandes" → list_orders
- "montre-moi les produits de ma commande" → view_order
- "quel est le montant total ?" → order_total
- "où est ma commande ?" → track_order
- "quelle est la date de livraison ?" → delivery_date
- "est-ce que ma commande est confirmée ?" → order_status
- "quel est le statut de ma commande ?" → order_status
- "ma commande a été validée ?" → order_status
- "je veux des infos sur ma commande" → view_order
- "parle-moi de ma commande" → view_order
- "donne moi la deuxième commande" → view_order
- "donne moi la première commande" → view_order
- "montre-moi la commande 2" → view_order
- "ma commande 3" → view_order
- "je veux le total de ma deuxième commande" → order_total
- "quel est le statut de ma commande 2 ?" → order_status
- "quand ma première commande sera-t-elle livrée ?" → delivery_date

PRIORITÉ : désigner QUELLE commande précise (par un numéro, une position
"première/deuxième/...", ou juste "ma commande" au singulier) est indépendant
de désigner QUELLE information est demandée (total, statut, livraison,
produits...). Les deux peuvent apparaître dans le même message.
1. Si le message est au pluriel et ne désigne aucune commande précise
   ("mes commandes", "la liste de mes commandes") → list_orders, quel que
   soit le reste du message.
2. Sinon, cherche un mot-clé de facette précise (montant/total, statut/confirmée,
   livraison/livrée, où/suivi, produits/contenu, ajoute/ajouter, retire/enlève/
   supprime, annule/annulation) n'importe où dans le message, même à côté d'une
   désignation de commande — s'il y en a un, utilise l'intent correspondant
   (order_total, order_status, delivery_date, track_order, view_order,
   add_product, remove_product, cancel_order). Attention : "annule"/"annulation"
   porte sur TOUTE la commande → cancel_order, à ne pas confondre avec
   "retire"/"enlève"/"supprime" qui visent UN produit → remove_product.
3. Seulement si AUCUN mot-clé de facette n'est présent (le message se contente
   de désigner une commande précise, ex: "ma commande 3", "la première commande"),
   classe en view_order par défaut.
Ne réponds "unknown" que si la demande ne concerne pas du tout une commande.

Question :
{question}

Réponds uniquement par l'intent.
"""