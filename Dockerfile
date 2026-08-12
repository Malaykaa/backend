# ── Stage 1 : dépendances ─────────────────────────────────────────────────────
# Isolé pour profiter du cache Docker — ne reconstruit que si requirements.txt change.
FROM python:3.11-slim AS deps

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq-dev gcc \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt


# ── Stage 2 : image finale ────────────────────────────────────────────────────
FROM python:3.11-slim AS app

WORKDIR /app

# Copier seulement les packages installés, pas les outils de build (gcc, etc.)
RUN apt-get update && apt-get install -y --no-install-recommends libpq5 \
    && rm -rf /var/lib/apt/lists/*

COPY --from=deps /usr/local/lib/python3.11/site-packages /usr/local/lib/python3.11/site-packages
COPY --from=deps /usr/local/bin /usr/local/bin

COPY . .

EXPOSE 8000

# RELOAD=true active le hot-reload (dev only via docker-compose).
# En production : RELOAD n'est pas défini → uvicorn démarre sans --reload.
#
# --proxy-headers --forwarded-allow-ips='*' : sans ce drapeau, uvicorn expose
# request.client.host = l'adresse TCP brute du pair, c'est-à-dire le conteneur
# nginx pour CHAQUE visiteur — nginx transmet pourtant X-Forwarded-For
# correctement (cf. nginx.production.conf du repo frontend), mais uvicorn
# l'ignore par défaut. Conséquence vérifiée en production : slowapi
# (get_remote_address = request.client.host, lecture du code source installé)
# voyait tous les visiteurs comme une seule et même IP, épuisant en commun le
# quota "5 inscriptions / 15 min" — un seul utilisateur suffisait à bloquer
# l'inscription pour tout le monde.
#
# '*' n'est sûr qu'à la condition que le service ne soit JAMAIS atteignable
# autrement que via nginx sur le réseau Docker interne — d'où le retrait
# conjoint de `ports: 8000:8000` dans docker-compose.yml. Sans cette
# fermeture, quiconque contacterait le port 8000 directement pourrait usurper
# n'importe quelle adresse IP via un en-tête forgé.
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers ${WORKERS:-1} --proxy-headers --forwarded-allow-ips='*' $([ \"$RELOAD\" = 'true' ] && echo '--reload' || echo '')"]
