#!/usr/bin/env bash
# =============================================================================
#  zenobot_alerts: one-command installer for Hetzner / any Ubuntu VPS (22.04, 24.04, 26.04)
#
#  Run it on the server (as root or with sudo):
#     curl -fsSL https://raw.githubusercontent.com/<you>/zenobot_alerts/main/install.sh | sudo bash
#
#  What it does:
#    1. installs Docker, git and a firewall
#    2. downloads your copy of the bot from GitHub into /opt/zenobot_alerts
#    3. asks for your Telegram token, chat id and a dashboard password
#    4. starts the bot + an HTTPS web server for the dashboard
#    5. installs the "zenobot" helper command (update / logs / restart / status)
#
#  Running it again is safe: it updates the code and keeps your settings.
# =============================================================================
set -euo pipefail

APP_DIR="/opt/zenobot_alerts"
ENV_FILE="$APP_DIR/.env"

say()  { printf '\n\033[1;36m==> %s\033[0m\n' "$*"; }
ok()   { printf '\033[1;32m✔ %s\033[0m\n' "$*"; }
warn() { printf '\033[1;33m⚠ %s\033[0m\n' "$*"; }
die()  { printf '\033[1;31m✖ %s\033[0m\n' "$*" >&2; exit 1; }

# Questions are read from the keyboard even when the script comes through "curl | bash"
ask() {  # ask "Question" default -> prints the answer
  local q="$1" def="${2:-}" ans=""
  if [[ -n "$def" ]]; then q="$q [$def]"; fi
  read -r -p "$q: " ans < /dev/tty || true
  printf '%s' "${ans:-$def}"
}
ask_secret() {
  local q="$1" ans=""
  read -r -s -p "$q: " ans < /dev/tty || true
  echo >&2
  printf '%s' "$ans"
}

[[ $EUID -eq 0 ]] || die "Please run as root, e.g.:  curl -fsSL <url> | sudo bash"
command -v apt-get >/dev/null || die "This installer is for Ubuntu/Debian servers."

# --------------------------------------------- 0. swap for small (1 GB) servers
# Building the bot needs a bit more memory than running it. On servers with
# less than 2 GB RAM we add a 2 GB swap file (extra memory on disk) once.
MEM_MB=$(awk '/MemTotal/ {print int($2/1024)}' /proc/meminfo)
if [[ "$MEM_MB" -lt 2000 ]] && ! swapon --show 2>/dev/null | grep -q .; then
  say "Small server (${MEM_MB} MB RAM): adding 2 GB of swap…"
  fallocate -l 2G /swapfile || dd if=/dev/zero of=/swapfile bs=1M count=2048
  chmod 600 /swapfile
  mkswap /swapfile >/dev/null
  swapon /swapfile
  grep -q '^/swapfile' /etc/fstab || echo '/swapfile none swap sw 0 0' >> /etc/fstab
  ok "Swap added"
fi

# ----------------------------------------------------------------- 1. packages
say "Installing system packages (this can take a few minutes)…"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq ca-certificates curl git ufw openssl >/dev/null
if ! command -v docker >/dev/null || ! docker compose version >/dev/null 2>&1; then
  say "Installing Docker…"
  curl -fsSL https://get.docker.com | sh >/dev/null
fi
systemctl enable --now docker >/dev/null 2>&1 || true
ok "Docker $(docker --version | awk '{print $3}' | tr -d ,) ready"

# ------------------------------------------------------------------- 2. code
if [[ -d "$APP_DIR/.git" ]]; then
  say "Updating the code from GitHub…"
  git -C "$APP_DIR" pull --ff-only || warn "Could not update (keeping the current version)."
else
  say "Downloading the bot from your GitHub repository"
  echo "Paste the address of YOUR repository, e.g. https://github.com/yourname/zenobot_alerts"
  REPO_URL="$(ask "GitHub repository URL")"
  [[ "$REPO_URL" == https://github.com/* ]] || die "That doesn't look like a GitHub address."
  REPO_URL="${REPO_URL%.git}"; REPO_URL="${REPO_URL%/}"
  echo "If the repository is PRIVATE, paste a GitHub access token (read-only)."
  echo "If it is PUBLIC, just press Enter."
  GH_TOKEN="$(ask_secret "GitHub token (optional)")"
  if [[ -n "$GH_TOKEN" ]]; then
    CLONE_URL="https://x-access-token:${GH_TOKEN}@${REPO_URL#https://}.git"
  else
    CLONE_URL="${REPO_URL}.git"
  fi
  git clone --depth 1 "$CLONE_URL" "$APP_DIR" || die "Could not download the repository. Check the URL (and token if private)."
fi
cd "$APP_DIR"
[[ -f docker-compose.yml ]] || die "docker-compose.yml not found in the repository. Upload the latest project files to GitHub."
ok "Code is in $APP_DIR"

# --------------------------------------------------------------- 3. settings
get_env() { grep -E "^$1=" "$ENV_FILE" 2>/dev/null | head -1 | cut -d= -f2- || true; }

if [[ ! -f "$ENV_FILE" ]]; then
  say "Your settings (saved only on this server, in $ENV_FILE)"
  TOKEN=""
  while [[ ! "$TOKEN" =~ ^[0-9]+:[A-Za-z0-9_-]{30,}$ ]]; do
    TOKEN="$(ask_secret "Telegram bot token from @BotFather (hidden while typing)")"
    [[ "$TOKEN" =~ ^[0-9]+:[A-Za-z0-9_-]{30,}$ ]] || warn "That token doesn't look right, try again."
  done
  CHAT=""
  while [[ ! "$CHAT" =~ ^-?[0-9]+$ ]]; do
    CHAT="$(ask "Your Telegram Chat ID from @userinfobot (a number)")"
  done
  PASS="$(ask_secret "Dashboard password (Enter = generate one for you)")"
  if [[ -z "$PASS" ]]; then PASS="$(openssl rand -base64 18 | tr -d '/+=' | cut -c1-16)"; GENERATED=1; fi
  JUP="$(ask "Jupiter API key (optional, press Enter to skip)")"

  IP="$(curl -4 -fsS --max-time 10 https://api.ipify.org || curl -4 -fsS --max-time 10 https://ifconfig.me || true)"
  DEFAULT_DOMAIN=""
  [[ -n "$IP" ]] && DEFAULT_DOMAIN="${IP//./-}.sslip.io"
  echo "Dashboard address: press Enter to use the free one shown, or type your own domain."
  DOMAIN="$(ask "Dashboard domain" "$DEFAULT_DOMAIN")"
  [[ -n "$DOMAIN" ]] || die "No domain/IP detected. Re-run and type one."

  umask 077
  cat > "$ENV_FILE" <<EOF
# zenobot_alerts settings: edit with:  zenobot edit
TELEGRAM_BOT_TOKEN=$TOKEN
CHAT_ID=$CHAT
DASHBOARD_PASSWORD=$PASS
JUPITER_API_KEY=$JUP
DOMAIN=$DOMAIN
EOF
  chmod 600 "$ENV_FILE"
  ok "Settings saved"
else
  ok "Keeping your existing settings ($ENV_FILE)"
  DOMAIN="$(get_env DOMAIN)"; PASS="$(get_env DASHBOARD_PASSWORD)"
fi

# --------------------------------------------------------------- 4. firewall
say "Configuring the firewall (SSH, HTTP, HTTPS only)…"
ufw allow OpenSSH >/dev/null || ufw allow 22/tcp >/dev/null
ufw allow 80/tcp >/dev/null
ufw allow 443/tcp >/dev/null
ufw --force enable >/dev/null
ok "Firewall on"

# ------------------------------------------------------------ 5. helper cmd
cat > /usr/local/bin/zenobot <<'EOF'
#!/usr/bin/env bash
# zenobot helper: manage the bot without remembering Docker commands
cd /opt/zenobot_alerts || exit 1
case "${1:-help}" in
  update)  git pull --ff-only && docker compose up -d --build && docker image prune -f >/dev/null && echo "Updated ✔" ;;
  logs)    docker compose logs -f --tail=100 bot ;;
  restart) docker compose restart && echo "Restarted ✔" ;;
  stop)    docker compose stop ;;
  start)   docker compose up -d ;;
  status)  docker compose ps ;;
  edit)    ${EDITOR:-nano} .env && docker compose up -d && echo "Settings applied ✔" ;;
  password) grep '^DASHBOARD_PASSWORD=' .env | cut -d= -f2- ;;
  *) echo "Usage: zenobot {update|logs|restart|stop|start|status|edit|password}" ;;
esac
EOF
chmod +x /usr/local/bin/zenobot

# ------------------------------------------------------------------ 6. start
say "Building and starting the bot (first time: ~3–5 minutes)…"
docker compose up -d --build
docker image prune -f >/dev/null || true

say "Waiting for the bot to come online…"
for _ in $(seq 1 60); do
  if docker compose exec -T bot python -c "import urllib.request;urllib.request.urlopen('http://localhost:8080/healthz',timeout=3)" >/dev/null 2>&1; then
    ok "Bot is running"; break
  fi
  sleep 3
done

cat <<EOF

=====================================================================
  ✅ zenobot_alerts is installed
---------------------------------------------------------------------
  Telegram : you should get "zenobot_alerts is online" — send /settings
  Dashboard: https://$DOMAIN   (the HTTPS certificate can take 1–2 minutes)
  Login    : user  admin   password  $( [[ "${GENERATED:-0}" == 1 ]] && echo "$PASS" || echo "(the one you chose)" )

  Useful commands on this server:
    zenobot logs      live logs (Ctrl+C to exit)
    zenobot update    download the latest code from GitHub and restart
    zenobot edit      change token / chat id / password
    zenobot password  show the dashboard password
    zenobot status    is it running?
=====================================================================
EOF
