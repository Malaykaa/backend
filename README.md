# Malaykaa Backend

Backend FastAPI du moteur d'exécution Malaykaa : conversation guidée par
agents spécialisés (carrière, bourse, financement, etc.), génération de
livrables (CV, lettres), matching d'opportunités scrapées, et notifications
WhatsApp via OneMessage.

---

## Stack technique

| Couche             | Choix                                                              |
| ------------------ | ------------------------------------------------------------------ |
| API                | FastAPI 0.120 + Uvicorn (standard) + SlowAPI (rate limiting)       |
| ORM / migrations   | SQLAlchemy 2.0 + Alembic                                           |
| Base de données    | PostgreSQL 16 + extension **pgvector** (recherche sémantique)      |
| Cache / OTP store  | Redis 5 (fallback mémoire en local)                                |
| LLM providers      | OpenAI / Gemini / Anthropic / mock (sélectionné par `LLM_PROVIDER`)|
| Embeddings         | Perplexity `pplx-embed-v1-4b` (3416 dims, sans index HNSW — voir migration `m4n5o6p7q8r9`) |
| Scraping           | Apify (collecte) + Perplexity (enrichissement)                     |
| Scheduler          | APScheduler in-process (matching auto + cron scraping)             |
| WhatsApp           | OneMessage (principal) → Twilio (fallback)                         |
| Auth               | JWT en cookies httpOnly + OTP par WhatsApp                         |

Python 3.11+. Tests : `pytest` + `pytest-asyncio` + `testcontainers[postgres]`.

---

## Architecture

```
app/
├── main.py                     # FastAPI bootstrap + routers + middlewares
├── core/
│   ├── config.py              # Settings pydantic + fail-fast prod
│   ├── database.py            # SQLAlchemy engine + sessionmaker + Base
│   ├── deps.py                # Auth deps (cookie JWT + Bearer)
│   ├── security.py            # bcrypt + JWT encode/decode
│   ├── rate_limit.py          # SlowAPI limiter
│   └── exceptions.py          # 4xx/5xx normalisées
├── models/                     # SQLAlchemy ORM (User, Goal, Plan, Doc, Chat,
│                               #   Opportunity, ScrapedOffer, UserIntent, …)
├── schemas/                    # Pydantic I/O (auth, chat, goals, …)
├── repositories/               # CRUD/queries (1 repo par modèle)
├── services/                   # Logique métier (PAS de SQL ici)
│   ├── auth_service.py
│   ├── chat_service.py        # Orchestration conversation + RAG offres
│   ├── plan_service.py
│   ├── document_service.py
│   ├── opportunity_service.py
│   ├── match_runner.py        # Cron : embed intent → push top-K offres
│   ├── embedding_service.py   # Perplexity wrapper (singleton, défensif)
│   ├── scraped_offer_service.py
│   ├── otp_service.py         # Redis OTP (fallback mémoire)
│   ├── whatsapp_service.py    # OneMessage → Twilio fallback → log
│   └── scraping/              # Pipeline Apify + Perplexity + scheduler
├── agents/                     # Agents spécialisés derrière l'orchestrator
│   ├── orchestrator.py        # Triage → ExecutionEngine
│   ├── triage.py              # Classification d'intent (LLM + heuristiques)
│   ├── execution_engine.py    # Mode direct ou workflow multi-agents
│   ├── exam_agent.py / scholarship_agent.py / career_agent.py / …
│   └── events.py              # ProgressEvent (SSE)
├── llm/                        # Provider abstrait + implémentations
│   ├── base.py                # Interface complete()/stream()
│   ├── continuation.py        # Anti-troncature multi-rounds
│   ├── mock_provider.py       # Déterministe pour tests
│   └── openai_provider.py / gemini_provider.py / claude_provider.py
└── routers/                    # Endpoints HTTP — fines couches d'adaptation
    ├── auth.py / users.py
    ├── chat.py                # CRUD threads + messages + SSE stream
    ├── action_adapter.py      # /ai/actions/{preset}/smart/stream
    ├── recommendations_router.py
    ├── opportunities.py / goals.py / plans.py / documents.py / files.py
    ├── tracking.py
    └── admin_scraping.py
```

### Flux de données — chat avec RAG

```
POST /chat/threads/{id}/stream
   → ChatService.stream_message()
       → MemoryService.get_context()           # historique compressé
       → ScrapedOfferService.search_for_agent()# RAG offres scrapées
       → Orchestrator.stream_route()
           → Triage.analyze() (LLM)
           → ExecutionEngine.execute(plan)
               → AgentX.process()              # exam, career, …
               → yield ProgressEvent (SSE)
       → ChatRepo.add_message(assistant)
   → format_chat_event() → SSE → frontend
```

### Cron de matching automatique

Toutes les heures, `match_runner.run_due_matchings()` :
1. Lit les profils dont `match_last_run_at + match_frequency_hours < now`
2. Embed la dernière `UserIntent` du user (Perplexity)
3. `pgvector` cosine + scoring profil → top-K `ScrapedOffer`
4. Insère un `ChatMessage` assistant + push WhatsApp si activé
5. Met à jour `profile.match_last_run_at`

---

## Installation et lancement local

### Prérequis

- Python 3.11+
- Docker (pour Postgres + Redis et pour les tests d'intégration)
- (Optionnel) Poetry ; sinon `pip install -r requirements.txt` suffit

### Setup

```bash
# 1. Variables d'environnement
cp .env.example .env
# → renseigner JWT_SECRET_KEY, OPENAI_API_KEY, PERPLEXITY_API_KEY, etc.

# 2. Postgres + pgvector + Redis (via Docker)
docker run -d --name malaykaa-pg \
    -e POSTGRES_PASSWORD=postgres -e POSTGRES_DB=malaykaa \
    -p 5432:5432 pgvector/pgvector:pg16
docker run -d --name malaykaa-redis -p 6379:6379 redis:7-alpine

# 3. Dépendances Python
pip install -r requirements.txt
pip install -r requirements-dev.txt   # pour les tests

# 4. Migrations
alembic upgrade head

# 5. Lancer l'API
uvicorn app.main:app --reload --port 8000
# → http://localhost:8000/api/docs
```

---

## Variables d'environnement

Voir `.env.example` pour la liste exhaustive et les valeurs par défaut. Les
variables **sensibles** :

| Variable                 | Rôle                                              | Obligatoire en prod |
| ------------------------ | ------------------------------------------------- | ------------------- |
| `JWT_SECRET_KEY`         | Signature des cookies JWT (32+ caractères)        | ✅ (fail-fast)       |
| `DATABASE_URL`           | DSN PostgreSQL (pgvector requis)                  | ✅                   |
| `REDIS_URL`              | Store OTP (fail-fast en staging/prod)             | ✅                   |
| `LLM_PROVIDER`           | `openai` / `gemini` / `claude` / `mock`           | ✅                   |
| `OPENAI_API_KEY` (ou autre) | Selon `LLM_PROVIDER`                          | ✅ (selon provider)  |
| `PERPLEXITY_API_KEY`     | Embeddings + scraping enrichi                     | Recommandé          |
| `ONEMESSAGE_BASE_URL` + `ONEMESSAGE_API_KEY` | OTP WhatsApp (provider principal) | ✅ (ou Twilio)       |
| `OTP_MOCK_ACCEPT_ANY`    | DEV ONLY — accepte tout code à 6 chiffres         | Refusé en prod      |

---

## Endpoints principaux

Documentation interactive : `GET /api/docs` (Swagger UI).

| Préfixe                       | Description                                            |
| ----------------------------- | ------------------------------------------------------ |
| `POST /auth/send-otp`         | Envoie un OTP WhatsApp au numéro                       |
| `POST /auth/verify-otp-register` | Crée le compte si OTP valide                        |
| `POST /auth/login` / `/login-phone` | Connexion email ou téléphone                    |
| `POST /auth/refresh`          | Rafraîchit l'access token via cookie                   |
| `GET  /auth/me`               | Profil de l'utilisateur authentifié                    |
| `POST /chat/threads`          | Crée un thread (avec ou sans `presetKey`)              |
| `POST /chat/threads/{id}/messages` | Envoie un message (réponse non-streaming)         |
| `POST /chat/threads/{id}/stream` | Envoie un message (réponse SSE)                     |
| `GET  /chat/pour-moi`         | Feed unifié : objectifs + recos + propositions + docs  |
| `GET  /recommendations/propositions` | Top-N offres scrapées matchées                  |
| `POST /recommendations/{offer_ref}/feedback` | clicked / saved / applied / ignored     |
| `GET  /goals` / `POST /goals` | CRUD objectifs utilisateur                             |
| `GET  /plans/{goal_id}` / `PATCH /plans/.../complete` | Plan d'action et étapes        |
| `POST /documents/generate`    | Génère un livrable (CV, lettre…) via DocumentAgent     |
| `POST /admin/scraping/run`    | Déclenche manuellement la pipeline de scraping         |
| `GET  /health`                | Healthcheck (statut + environnement)                   |

---

## Commandes utiles

### Migrations Alembic

```bash
alembic upgrade head                                # Applique toutes les migrations
alembic revision --autogenerate -m "description"    # Crée une nouvelle migration
alembic downgrade -1                                # Rollback de la dernière migration
alembic current                                     # Affiche la révision en cours
```

### Backfill embeddings

Indexe (ou ré-indexe) les `scraped_offers.embedding` pour celles qui n'en
ont pas encore — utile après l'activation de `PERPLEXITY_API_KEY` ou
après un import en masse.

```bash
python -m scripts.backfill_embeddings
```

### Tests

```bash
# Tests unitaires (rapides, pas de Docker)
pytest tests/unit/

# Tests d'intégration (lance un Postgres pgvector éphémère via testcontainers)
pytest tests/integration/

# Toute la suite avec couverture
pytest --cov=app --cov-report=term-missing

# Tester un fichier ou un test précis
pytest tests/unit/test_engine.py -k continuation
```

### Lint / format

```bash
ruff check app tests              # Lint
ruff check app tests --fix        # Auto-fix
black app tests                   # Format
mypy app                          # Typage statique
```

### Audit de sécurité

```bash
pip-audit                         # Vulnérabilités dans les deps
```

---

## Conventions internes

- **Routers**  : couches HTTP minces, aucune logique métier ni SQL.
- **Services** : logique métier, orchestration ; ne touchent pas directement à SQLAlchemy.
- **Repositories** : seul endroit où on écrit du SQL/ORM.
- **Tests**   : `tests/unit/` (mocks) et `tests/integration/` (DB réelle via testcontainers, `LLM_PROVIDER=mock`).
- **Migrations** : un fichier Alembic par changement de schéma. Toute nouvelle colonne pgvector doit être créée via une migration `op.execute("CREATE EXTENSION IF NOT EXISTS vector")` puis `op.add_column(...)` avec `Vector(EMBEDDING_DIM)`.
- **Sécurité**  : validate_security_settings() refuse de booter en staging/prod si JWT par défaut, Redis absent, ou OTP_MOCK_ACCEPT_ANY actif.

---

## Déploiement en production

Le déploiement prod nécessite :
- Un serveur avec Docker (PostgreSQL 16 + pgvector, Redis)
- Un processus Gunicorn + Uvicorn workers
- Les variables d'env configurées (voir tableau ci-dessus)
- `alembic upgrade head` au démarrage
- `python -m scripts.backfill_embeddings` après activation de Perplexity

Un Dockerfile est fourni à la racine. Configuration prod recommandée :
```bash
gunicorn app.main:app \
  -w 2 -k uvicorn.workers.UvicornWorker \
  --bind 0.0.0.0:8000 \
  --proxy-headers
```

> Note : désactiver `SCHEDULER_ENABLED` sur tous les workers sauf un
> pour éviter les doublons de cron jobs.

---

## Licence

Propriétaire — Malaykaa, tous droits réservés.
