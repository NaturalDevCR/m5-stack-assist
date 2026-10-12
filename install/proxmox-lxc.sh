#!/usr/bin/env bash
# Crea un LXC Debian en Proxmox VE e instala Voice Agent Bridge dentro.
# Ejecutar en la shell del NODO Proxmox (como root):
#
#   bash -c "$(curl -fsSL https://raw.githubusercontent.com/NaturalDevCR/m5-stack-assist/main/install/proxmox-lxc.sh)"
#
# Variables opcionales (anteponer al comando, p. ej. CTID=237 IP=192.168.20.37/24,gw=192.168.20.1):
#   CTID  HOSTNAME_CT  CORES  RAM  SWAP  DISK  STORAGE  TEMPLATE_STORAGE  BRIDGE  IP  VLAN
#   OS_VERSION (13 por defecto; en Proxmox VE 8 se usa 12 automáticamente)
#   AUTOLOGIN (1 por defecto: consola de Proxmox sin contraseña; 0 para desactivar)
#   REPO_URL  BRANCH  YES=1 (sin confirmación)
set -euo pipefail

REPO_URL="${REPO_URL:-https://github.com/NaturalDevCR/m5-stack-assist.git}"
BRANCH="${BRANCH:-main}"
RAW_BASE="${RAW_BASE:-https://raw.githubusercontent.com/NaturalDevCR/m5-stack-assist/$BRANCH}"
HOSTNAME_CT="${HOSTNAME_CT:-voice-agent-bridge}"
CORES="${CORES:-1}"
RAM="${RAM:-512}"
SWAP="${SWAP:-256}"
DISK="${DISK:-4}"
STORAGE="${STORAGE:-local-lvm}"
TEMPLATE_STORAGE="${TEMPLATE_STORAGE:-local}"
BRIDGE="${BRIDGE:-vmbr0}"
IP="${IP:-dhcp}"
VLAN="${VLAN:-}"
OS_VERSION="${OS_VERSION:-13}"
AUTOLOGIN="${AUTOLOGIN:-1}"

msg() { printf '\e[1;36m[voice-agent-bridge]\e[0m %s\n' "$*"; }
die() { printf '\e[1;31m[voice-agent-bridge] ERROR:\e[0m %s\n' "$*" >&2; exit 1; }

[[ $EUID -eq 0 ]] || die "ejecutar como root en el nodo Proxmox"
command -v pct >/dev/null || die "no se encontró 'pct': ejecutar en un nodo Proxmox VE"
CTID="${CTID:-$(pvesh get /cluster/nextid)}"
pct status "$CTID" >/dev/null 2>&1 && die "el CTID $CTID ya existe (usa CTID=<otro>)"
pvesm status --storage "$STORAGE" >/dev/null 2>&1 || die "no existe el almacenamiento '$STORAGE' (usa STORAGE=<nombre>)"

# Debian 13 sólo arranca en Proxmox VE 9+: en PVE 8 el contenedor se crea pero falla
# al iniciar ("unsupported debian version" / "Failed to spawn container").
PVE_MAJOR="$(pveversion | sed -E 's#^pve-manager/([0-9]+).*#\1#')"
if [[ "$OS_VERSION" == "13" && "${PVE_MAJOR:-0}" -lt 9 ]]; then
  msg "Proxmox VE $PVE_MAJOR no soporta Debian 13 en LXC; se usará Debian 12"
  OS_VERSION=12
fi
HOST_ARCH="$(dpkg --print-architecture)"
case "$HOST_ARCH" in amd64|arm64) ;; *) die "arquitectura no soportada: $HOST_ARCH" ;; esac
# pveam ofrece plantillas de varias arquitecturas (p. ej. *_amd64 y *_arm64): hay que
# filtrar por la del nodo o el contenedor falla con "Exec format error" en /sbin/init.
msg "Buscando plantilla Debian $OS_VERSION ($HOST_ARCH)…"
pveam update >/dev/null || true
TEMPLATE="$(pveam available --section system | awk '{print $2}' \
  | grep -E "^debian-${OS_VERSION}-standard_.*_${HOST_ARCH}\.tar\." | sort -V | tail -1)"
LXC_BASE=""
if [[ -z "$TEMPLATE" && "$HOST_ARCH" == "arm64" ]]; then
  # Sin plantilla arm64 en pveam: imagen Debian oficial de linuxcontainers.org.
  CODENAME="trixie"; [[ "$OS_VERSION" == "12" ]] && CODENAME="bookworm"
  LXC_BASE="https://images.linuxcontainers.org/images/debian/$CODENAME/arm64/default"
  BUILD="$(curl -fsSL "$LXC_BASE/" | grep -oE '[0-9]{8}_[0-9]{2}%3A[0-9]{2}' | sort -u | tail -1)"
  [[ -n "$BUILD" ]] || die "no se encontró imagen Debian arm64 en $LXC_BASE"
  TEMPLATE="debian-${OS_VERSION}-lxc_${BUILD%%_*}_arm64.tar.xz"
fi
[[ -n "$TEMPLATE" ]] || die "no se encontró plantilla debian-${OS_VERSION}-standard para $HOST_ARCH"

NET="name=eth0,bridge=$BRIDGE,ip=$IP"
[[ -n "$VLAN" ]] && NET="$NET,tag=$VLAN"

cat <<EOF

  Voice Agent Bridge — nuevo contenedor LXC
  ───────────────────────────────────
  CTID ............ $CTID
  Hostname ........ $HOSTNAME_CT
  Plantilla ....... $TEMPLATE ($HOST_ARCH)
  CPU / RAM / Disco $CORES núcleo(s) / ${RAM} MB / ${DISK} GB en $STORAGE
  Red ............. $NET
  Repositorio ..... $REPO_URL ($BRANCH)
  Tipo ............ sin privilegios, inicio automático

EOF
if [[ "${YES:-0}" != "1" ]]; then
  read -r -p "¿Continuar? [s/N] " ans
  [[ "$ans" =~ ^[sSyY]$ ]] || die "cancelado"
fi

if ! pveam list "$TEMPLATE_STORAGE" | grep -q "$TEMPLATE"; then
  msg "Descargando $TEMPLATE…"
  if [[ -z "$LXC_BASE" ]]; then
    pveam download "$TEMPLATE_STORAGE" "$TEMPLATE" >/dev/null
  else
    DEST="$(pvesm path "$TEMPLATE_STORAGE:vztmpl/$TEMPLATE")"
    mkdir -p "$(dirname "$DEST")"
    curl -fL --progress-bar -o "$DEST.part" "$LXC_BASE/$BUILD/rootfs.tar.xz"
    EXPECTED="$(curl -fsSL "$LXC_BASE/$BUILD/SHA256SUMS" | awk '$2=="rootfs.tar.xz"{print $1}')"
    ACTUAL="$(sha256sum "$DEST.part" | awk '{print $1}')"
    if [[ -z "$EXPECTED" || "$EXPECTED" != "$ACTUAL" ]]; then
      rm -f "$DEST.part"
      die "checksum SHA256 inválido para rootfs.tar.xz"
    fi
    mv "$DEST.part" "$DEST"
  fi
fi

msg "Creando contenedor $CTID…"
pct create "$CTID" "$TEMPLATE_STORAGE:vztmpl/$TEMPLATE" \
  --hostname "$HOSTNAME_CT" --cores "$CORES" --memory "$RAM" --swap "$SWAP" \
  --rootfs "$STORAGE:$DISK" --net0 "$NET" \
  --arch "$HOST_ARCH" --ostype debian \
  --unprivileged 1 --features nesting=1 --onboot 1 --timezone host \
  --description "Voice Agent Bridge — $REPO_URL" >/dev/null
if ! pct start "$CTID"; then
  printf '\n'
  pct start "$CTID" --debug 2>&1 | tail -20 || true
  die "el contenedor $CTID no arrancó (diagnóstico arriba). Para borrarlo: pct destroy $CTID"
fi

msg "Esperando red en el contenedor…"
for _ in $(seq 1 30); do
  if pct exec "$CTID" -- getent hosts deb.debian.org >/dev/null 2>&1; then break; fi
  sleep 2
done
pct exec "$CTID" -- getent hosts deb.debian.org >/dev/null 2>&1 || die "el contenedor no tiene acceso a Internet"

msg "Instalando Voice Agent Bridge dentro del contenedor…"
CT_ENV=(env LANG=C.UTF-8 LC_ALL=C.UTF-8 PATH=/usr/local/bin:/usr/local/sbin:/usr/sbin:/usr/bin:/sbin:/bin)
if [[ "$AUTOLOGIN" == "1" ]]; then
  # Como los community-scripts: la consola de la UI de Proxmox entra como root sin
  # contraseña. La contraseña de root queda bloqueada (sin acceso por red).
  msg "Activando autologin en la consola de Proxmox…"
  # shellcheck disable=SC2016  # $TERM debe quedar literal en el archivo de systemd
  pct exec "$CTID" -- "${CT_ENV[@]}" bash -c 'mkdir -p /etc/systemd/system/container-getty@.service.d && printf "%s\n" "[Service]" "ExecStart=" "ExecStart=-/sbin/agetty --autologin root --noclear --keep-baud tty%I 115200,38400,9600 \$TERM" > /etc/systemd/system/container-getty@.service.d/override.conf && systemctl daemon-reload && systemctl restart "container-getty@*" || true'
fi
pct exec "$CTID" -- "${CT_ENV[@]}" bash -c "apt-get update -qq && DEBIAN_FRONTEND=noninteractive apt-get install -y -qq curl ca-certificates >/dev/null"
pct exec "$CTID" -- "${CT_ENV[@]}" bash -c "curl -fsSL '$RAW_BASE/install/lxc-install.sh' | REPO_URL='$REPO_URL' BRANCH='$BRANCH' bash"

CT_IP="$(pct exec "$CTID" -- hostname -I | awk '{print $1}')"
cat <<EOF

  ✔ Contenedor $CTID listo. IP: ${CT_IP:-desconocida}

  Configurar:   consola del contenedor en la UI de Proxmox (o pct enter $CTID)
                nano /etc/voice-agent-bridge/voice-agent-bridge.env
                nano /etc/voice-agent-bridge/config.yaml
                systemctl start voice-agent-bridge && journalctl -u voice-agent-bridge -f

  El AtomS3R se conectará a:  ws://${CT_IP:-<ip>}:8765/v0/terminal

EOF
