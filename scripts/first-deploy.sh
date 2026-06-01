#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# first-deploy.sh — Premier déploiement du backend sur le serveur
# À exécuter UNE SEULE FOIS en tant que deploy sur le serveur
# Prérequis : server-setup.sh déjà exécuté
# Usage : bash first-deploy.sh <GITHUB_OWNER> <GITHUB_TOKEN>
# Ex    : bash first-deploy.sh moisegondo ghp_xxxxxxxxxxxx
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

GITHUB_OWNER="${1:?Usage: $0 <GITHUB_OWNER> <GITHUB_TOKEN>}"
GITHUB_TOKEN="${2:?Usage: $0 <GITHUB_OWNER> <GITHUB_TOKEN>}"
PROJECT_PATH="/opt/malaykaa/backend"
BACKEND_IMAGE="ghcr.io/${GITHUB_OWNER}/malaykaa-backend:latest"

log() { echo -e "\n\033[1;32m▶ $*\033[0m"; }

# ── 1. Authentification ghcr.io ───────────────────────────────────────────
log "Connexion à GitHub Container Registry..."
echo "$GITHUB_TOKEN" | docker login ghcr.io -u "$GITHUB_OWNER" --password-stdin

# ── 2. Vérification des fichiers requis ───────────────────────────────────
log "Vérification des fichiers requis..."
for f in "$PROJECT_PATH/docker-compose.yml" "$PROJECT_PATH/.env"; do
  if [ ! -f "$f" ]; then
    echo "❌  Fichier manquant : $f"
    echo "    Copie-le depuis ton poste : scp backend/$(basename $f) deploy@SERVEUR:$f"
    exit 1
  fi
done

# ── 3. Réseau Docker partagé ──────────────────────────────────────────────
log "Réseau Docker partagé..."
docker network create malaykaa_net 2>/dev/null || echo "Réseau déjà existant."

# ── 4. Démarrage DB et Redis ──────────────────────────────────────────────
log "Démarrage de la base de données et Redis..."
cd "$PROJECT_PATH"
BACKEND_IMAGE="ghcr.io/${GITHUB_OWNER}/malaykaa-backend" IMAGE_TAG=latest \
  docker compose up -d db redis

log "Attente que la base de données soit prête..."
sleep 10

# ── 5. Migrations Alembic ─────────────────────────────────────────────────
log "Application des migrations..."
BACKEND_IMAGE="ghcr.io/${GITHUB_OWNER}/malaykaa-backend" IMAGE_TAG=latest \
  docker compose run --rm backend alembic upgrade head

# ── 6. Démarrage backend ──────────────────────────────────────────────────
log "Démarrage du backend..."
BACKEND_IMAGE="ghcr.io/${GITHUB_OWNER}/malaykaa-backend" IMAGE_TAG=latest \
  docker compose up -d backend

# ── 7. Vérification ───────────────────────────────────────────────────────
log "Vérification..."
docker ps --format "table {{.Names}}\t{{.Status}}\t{{.Ports}}"

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo " ✅  Backend déployé !"
echo ""
echo "   📡  API docs : https://malayka.co/api/docs"
echo "   🐳  Portainer : http://$(curl -s ifconfig.me):9000"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
