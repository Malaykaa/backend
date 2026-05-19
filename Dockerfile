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
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers ${WORKERS:-1} $([ \"$RELOAD\" = 'true' ] && echo '--reload' || echo '')"]
