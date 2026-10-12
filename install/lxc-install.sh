#!/usr/bin/env bash
# Instala o actualiza Voice Agent Bridge en Debian 12/13 (dentro del LXC o en cualquier equipo).
#   bash lxc-install.sh            instala / actualiza
# Variables: REPO_URL, BRANCH
set -euo pipefail

REPO_URL="${REPO_URL:-https://github.com/NaturalDevCR/m5-stack-assist.git}"
BRANCH="${BRANCH:-main}"
APP_DIR=/opt/voice-agent-bridge
CONF_DIR=/etc/voice-agent-bridge
STATE_DIR=/var/lib/voice-agent-bridge
SERVICE=voice-agent-bridge
export UV_PYTHON_INSTALL_DIR="$APP_DIR/.python"
# `pct exec` usa un PATH mínimo sin /usr/local/bin (donde se instala uv) y hereda el
# LANG del nodo, que el contenedor no tiene generado.
export PATH="/usr/local/bin:/usr/local/sbin:/usr/sbin:/usr/bin:/sbin:/bin"
export LANG=C.UTF-8 LC_ALL=C.UTF-8

msg() { printf '\e[1;36m[voice-agent-bridge]\e[0m %s\n' "$*"; }
die() { printf '\e[1;31m[voice-agent-bridge] ERROR:\e[0m %s\n' "$*" >&2; exit 1; }

[[ $EUID -eq 0 ]] || die "ejecutar como root"
command -v apt-get >/dev/null || die "se requiere Debian/Ubuntu (apt-get)"

msg "Instalando paquetes base…"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq git curl ca-certificates openssl >/dev/null

if ! id voicebridge >/dev/null 2>&1; then
  msg "Creando usuario de servicio 'voicebridge'…"
  useradd --system --home-dir "$STATE_DIR" --shell /usr/sbin/nologin voicebridge
fi

if [[ -d "$APP_DIR/.git" ]]; then
  msg "Actualizando código ($BRANCH)…"
  git -C "$APP_DIR" fetch --quiet origin "$BRANCH"
  git -C "$APP_DIR" checkout --quiet "$BRANCH"
  git -C "$APP_DIR" reset --quiet --hard "origin/$BRANCH"
else
  msg "Descargando código desde $REPO_URL ($BRANCH)…"
  git clone --quiet --branch "$BRANCH" "$REPO_URL" "$APP_DIR"
fi

if ! command -v uv >/dev/null; then
  msg "Instalando uv…"
  curl -LsSf https://astral.sh/uv/install.sh | env UV_INSTALL_DIR=/usr/local/bin INSTALLER_NO_MODIFY_PATH=1 sh >/dev/null
fi

msg "Instalando dependencias de Python…"
(cd "$APP_DIR/bridge" && uv sync --frozen --no-dev --quiet)

msg "Preparando configuración en $CONF_DIR…"
install -d -m 750 -o root -g voicebridge "$CONF_DIR"
install -d -m 750 -o voicebridge -g voicebridge "$STATE_DIR"
if [[ ! -f "$CONF_DIR/config.yaml" ]]; then
  install -m 640 -o root -g voicebridge "$APP_DIR/bridge/config.example.yaml" "$CONF_DIR/config.yaml"
fi
if [[ ! -f "$CONF_DIR/voice-agent-bridge.env" ]]; then
  token="$(openssl rand -hex 24)"
  sed "s/^TERMINAL_TOKEN=.*/TERMINAL_TOKEN=$token/" \
    "$APP_DIR/bridge/voice-agent-bridge.env.example" > "$CONF_DIR/voice-agent-bridge.env"
  chown root:voicebridge "$CONF_DIR/voice-agent-bridge.env"
  chmod 640 "$CONF_DIR/voice-agent-bridge.env"
fi

install -m 644 "$APP_DIR/bridge/systemd/voice-agent-bridge.service" "/etc/systemd/system/$SERVICE.service"
cat > /usr/local/bin/voice-agent-bridge-update <<EOF
#!/usr/bin/env bash
set -euo pipefail
REPO_URL="$REPO_URL" BRANCH="$BRANCH" bash "$APP_DIR/install/lxc-install.sh"
EOF
chmod 755 /usr/local/bin/voice-agent-bridge-update
systemctl daemon-reload
systemctl enable --quiet "$SERVICE"

if grep -q '^ELEVENLABS_API_KEY=.\+' "$CONF_DIR/voice-agent-bridge.env"; then
  msg "Reiniciando servicio…"
  systemctl restart "$SERVICE"
  sleep 2
  systemctl --no-pager --lines=5 status "$SERVICE" || true
else
  cat <<EOF

  ✔ Voice Agent Bridge instalado. Falta configurarlo:

    1. nano $CONF_DIR/voice-agent-bridge.env     (ELEVENLABS_API_KEY y MQTT_PASSWORD)
    2. nano $CONF_DIR/config.yaml          (mqtt.host, mqtt.username…)
    3. runuser -u voicebridge -- $APP_DIR/bridge/.venv/bin/voice-agent-bridge check \\
         --config $CONF_DIR/config.yaml --env $CONF_DIR/voice-agent-bridge.env
    4. systemctl start $SERVICE && journalctl -u $SERVICE -f

  Token para el AtomS3R (TERMINAL_TOKEN) ya generado en $CONF_DIR/voice-agent-bridge.env
  Actualizar más adelante:  voice-agent-bridge-update

EOF
fi
