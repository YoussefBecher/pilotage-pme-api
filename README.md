# Pilotage PME : API FastAPI + PostgreSQL + assistant IA
## Démarrer (Docker Desktop requis)
    copy .env.example .env      # puis renseigner ANTHROPIC_API_KEY et changer JWT_SECRET
    docker compose up -d --build
    docker compose exec api python seed.py
API : http://localhost:8000/docs  — comptes démo : patron@demo.tn / employe@demo.tn (mot de passe : DEMO_PASSWORD)
## Brancher l'interface React
Dans le projet React : `.env` avec `VITE_DEMO=false`, puis `npm run dev` (le proxy Vite envoie /api vers localhost:8000).
## Sécurité de l'assistant /chat (deux garde-fous indépendants)
1. Validation sqlglot : une seule requête SELECT, tables autorisées uniquement (fact_ventes, dim_produit, dim_client, v_stock).
2. Exécution avec le rôle PostgreSQL `bi_readonly` : lecture seule, timeout 5 s, aucun accès à la table users. LIMIT 200 imposé.
Le LLM ne reçoit que le schéma, la question et les 20 premières lignes de résultat. La requête SQL est renvoyée à l'interface pour vérification.
## Avant la production
Changer les mots de passe (JWT_SECRET, bi_readonly, comptes démo), activer HTTPS, utiliser un pool de connexions et un compteur Redis pour la limite de questions, brancher l'envoi réel des rapports.
