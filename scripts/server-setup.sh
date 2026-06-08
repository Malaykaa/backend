#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# server-setup.sh — Initialisation serveur LWS (Ubuntu 22.04 / Debian 12)
# Usage : bash server-setup.sh <GITHUB_OWNER> <DEPLOY_USER>
# Ex    : bash server-setup.sh monorg deploy
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

GITHUB_OWNER="${1:?Usage: $0 <GITHUB_OWNER> <DEPLOY_USER>}"
DEPLOY_USER="${2:?Usage: $0 <GITHUB_OWNER> <DEPLOY_USER>}"
PROJECT_PATH="/opt/malaykaa"
DOMAIN="malayka.co"

log() { echo -e "\n\033[1;32m▶ $*\033[0m"; }

# ── 1. Mise à jour système ─────────────────────────────────────────────────
log "Mise à jour des paquets..."
apt-get update -qq && apt-get upgrade -y -qq

# ── 2. Dépendances de base ─────────────────────────────────────────────────
log "Installation des dépendances..."
apt-get install -y -qq curl git ufw ca-certificates gnupg certbot

# ── 3. Docker CE ───────────────────────────────────────────────────────────
log "Installation de Docker..."
if ! command -v docker &>/dev/null; then
  curl -fsSL https://get.docker.com | sh
fi
docker --version
docker compose version

# ── 4. Utilisateur deploy ──────────────────────────────────────────────────
log "Création de l'utilisateur $DEPLOY_USER..."
if ! id "$DEPLOY_USER" &>/dev/null; then
  useradd -m -s /bin/bash "$DEPLOY_USER"
fi
usermod -aG docker "$DEPLOY_USER"

# Répertoire SSH pour le deploy user
DEPLOY_HOME=$(getent passwd "$DEPLOY_USER" | cut -d: -f6)
mkdir -p "$DEPLOY_HOME/.ssh"
chmod 700 "$DEPLOY_HOME/.ssh"
touch "$DEPLOY_HOME/.ssh/authorized_keys"
chmod 600 "$DEPLOY_HOME/.ssh/authorized_keys"
chown -R "$DEPLOY_USER:$DEPLOY_USER" "$DEPLOY_HOME/.ssh"

# ── 5. Structure projet ────────────────────────────────────────────────────
log "Création de la structure projet dans $PROJECT_PATH..."
mkdir -p "$PROJECT_PATH"/{backend,frontend}
chown -R "$DEPLOY_USER:$DEPLOY_USER" "$PROJECT_PATH"

# ── 6. Portainer ──────────────────────────────────────────────────────────
log "Installation de Portainer..."
if ! docker ps -a --format '{{.Names}}' | grep -q "^portainer$"; then
  docker volume create portainer_data
  docker run -d \
    --name portainer \
    --restart=always \
    -p 9000:9000 \
    -p 9443:9443 \
    -v /var/run/docker.sock:/var/run/docker.sock \
    -v portainer_data:/data \
    portainer/portainer-ce:latest
  echo "Portainer démarré — accès : http://$(curl -s ifconfig.me):9000"
else
  echo "Portainer déjà installé."
fi

# ── 7. Réseau Docker partagé ───────────────────────────────────────────────
log "Création du réseau Docker malaykaa_net..."
docker network create malaykaa_net 2>/dev/null || echo "Réseau déjà existant."

# ── 8. Firewall ───────────────────────────────────────────────────────────
log "Configuration du firewall (UFW)..."
ufw default deny incoming
ufw default allow outgoing
ufw allow 22/tcp    comment "SSH"
ufw allow 80/tcp    comment "HTTP frontend"
ufw allow 443/tcp   comment "HTTPS (futur)"
ufw allow 9000/tcp  comment "Portainer HTTP"
ufw allow 9443/tcp  comment "Portainer HTTPS"
ufw allow 8000/tcp  comment "Backend API (debug uniquement)"
ufw --force enable
ufw status verbose

# ── 9. Certificat SSL Let's Encrypt ───────────────────────────────────────
# Stockage isolé dans /opt/malaykaa/letsencrypt — PAS /etc/letsencrypt, qui est
# géré par ISPConfig et nettoie périodiquement les certificats qu'il ne reconnaît
# pas comme siens (cause d'une perte totale des certs malayka.co constatée le 7/06).
LE_DIR="/opt/malaykaa/letsencrypt"
LE_FLAGS="--config-dir $LE_DIR/config --work-dir $LE_DIR/work --logs-dir $LE_DIR/logs"
mkdir -p "$LE_DIR/config" "$LE_DIR/work" "$LE_DIR/logs"

log "Obtention du certificat SSL pour $DOMAIN..."
# Le port 80 doit être libre — aucun container frontend ne doit tourner ici
if certbot certonly \
    --standalone \
    $LE_FLAGS \
    --non-interactive \
    --agree-tos \
    --register-unsafely-without-email \
    -d "$DOMAIN" \
    -d "www.$DOMAIN"; then
  echo "Certificat obtenu avec succès."
else
  echo "⚠️  Certbot a échoué. Vérifie que le DNS de $DOMAIN pointe vers ce serveur."
  echo "    Relance manuellement : certbot certonly --standalone $LE_FLAGS -d $DOMAIN -d www.$DOMAIN"
fi

# Renouvellement automatique via cron — on retire d'abord toute ancienne ligne
# "certbot renew" pour éviter les doublons si ce script est relancé.
CRON_LINE="0 3 * * * certbot renew $LE_FLAGS --quiet --deploy-hook 'docker compose -f $PROJECT_PATH/frontend/docker-compose.yml restart frontend'"
( crontab -l 2>/dev/null | grep -v "certbot renew"; echo "$CRON_LINE" ) | crontab -
echo "Cron de renouvellement SSL configuré (tous les jours à 3h)."

# ── 10. Génération de la clé SSH pour GitHub Actions ──────────────────────
log "Génération de la clé SSH pour le CI/CD GitHub..."
KEY_PATH="$DEPLOY_HOME/.ssh/github_deploy_key"
if [ ! -f "$KEY_PATH" ]; then
  ssh-keygen -t ed25519 -f "$KEY_PATH" -C "github-actions@malaykaa" -N ""
  chown "$DEPLOY_USER:$DEPLOY_USER" "$KEY_PATH" "$KEY_PATH.pub"
fi

cat "$KEY_PATH.pub" >> "$DEPLOY_HOME/.ssh/authorized_keys"

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo " ✅  Serveur prêt !"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""
echo " 📋  Secrets à copier dans GitHub → Settings → Secrets :"
echo ""
echo "   DEPLOY_HOST  = $(curl -s ifconfig.me)"
echo "   DEPLOY_USER  = $DEPLOY_USER"
echo "   DEPLOY_SSH_KEY (clé privée ci-dessous) :"
echo ""
cat "$KEY_PATH"
echo ""
echo "   DEPLOY_PATH (backend)  = $PROJECT_PATH/backend"
echo "   DEPLOY_PATH (frontend) = $PROJECT_PATH/frontend"
echo ""
echo " 🌐  Portainer : http://$(curl -s ifconfig.me):9000"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
