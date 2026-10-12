#!/usr/bin/env bash
# Instala o actualiza el puente Danny en Debian 12/13 (dentro del LXC o en cualquier equipo).
#   bash lxc-install.sh            instala / actualiza
# Variables: REPO_URL, BRANCH
set -euo pipefail

REPO_URL="${REPO_URL:-https://github.com/NaturalDevCR/m5-stack-assist.git}"
BRANCH="${BRANCH:-main}"
APP_DIR=/opt/danny-bridge
CONF_DIR=/etc/danny-bridge
STATE_DIR=/var/lib/danny-bridge
SERVICE=danny-bridge
export UV_PYTHON_INSTALL_DIR="$APP_DIR/.python"

msg() { printf '\e[1;36m[danny]\e[0m %s\n' "$*"; }
die() { printf '\e[1;31m[danny] ERROR:\e[0m %s\n' "$*" >&2; exit 1; }

[[ $EUID -eq 0 ]] || die "ejecutar como root"
command -v apt-get >/dev/null || die "se requiere Debian/Ubuntu (apt-get)"

msg "Instalando paquetes base…"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq git curl ca-certificates openssl >/dev/null

if ! id danny >/dev/null 2>&1; then
  msg "Creando usuario de servicio 'danny'…"
  useradd --system --home-dir "$STATE_DIR" --shell /usr/sbin/nologin danny
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
install -d -m 750 -o root -g danny "$CONF_DIR"
install -d -m 750 -o danny -g danny "$STATE_DIR"
if [[ ! -f "$CONF_DIR/config.yaml" ]]; then
  install -m 640 -o root -g danny "$APP_DIR/bridge/config.example.yaml" "$CONF_DIR/config.yaml"
fi
if [[ ! -f "$CONF_DIR/danny-bridge.env" ]]; then
  token="$(openssl rand -hex 24)"
  sed "s/^DANNY_TERMINAL_TOKEN=.*/DANNY_TERMINAL_TOKEN=$token/" \
    "$APP_DIR/bridge/danny-bridge.env.example" > "$CONF_DIR/danny-bridge.env"
  chown root:danny "$CONF_DIR/danny-bridge.env"
  chmod 640 "$CONF_DIR/danny-bridge.env"
fi

install -m 644 "$APP_DIR/bridge/systemd/danny-bridge.service" "/etc/systemd/system/$SERVICE.service"
cat > /usr/local/bin/danny-bridge-update <<EOF
#!/usr/bin/env bash
set -euo pipefail
REPO_URL="$REPO_URL" BRANCH="$BRANCH" bash "$APP_DIR/install/lxc-install.sh"
EOF
chmod 755 /usr/local/bin/danny-bridge-update
systemctl daemon-reload
systemctl enable --quiet "$SERVICE"

if grep -q '^ELEVENLABS_API_KEY=.\+' "$CONF_DIR/danny-bridge.env"; then
  msg "Reiniciando servicio…"
  systemctl restart "$SERVICE"
  sleep 2
  systemctl --no-pager --lines=5 status "$SERVICE" || true
else
  cat <<EOF

  ✔ Danny Bridge instalado. Falta configurarlo:

    1. nano $CONF_DIR/danny-bridge.env     (ELEVENLABS_API_KEY y MQTT_PASSWORD)
    2. nano $CONF_DIR/config.yaml          (mqtt.host, mqtt.username…)
    3. runuser -u danny -- $APP_DIR/bridge/.venv/bin/danny-bridge check \\
         --config $CONF_DIR/config.yaml --env $CONF_DIR/danny-bridge.env
    4. systemctl start $SERVICE && journalctl -u $SERVICE -f

  Token para el AtomS3R (DANNY_TERMINAL_TOKEN) ya generado en $CONF_DIR/danny-bridge.env
  Actualizar más adelante:  danny-bridge-update

EOF
fi
