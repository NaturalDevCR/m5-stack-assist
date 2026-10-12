#!/usr/bin/env bash
# Crea un LXC Debian en Proxmox VE e instala el puente Danny dentro.
# Ejecutar en la shell del NODO Proxmox (como root):
#
#   bash -c "$(curl -fsSL https://raw.githubusercontent.com/NaturalDevCR/m5-stack-assist/main/install/proxmox-lxc.sh)"
#
# Variables opcionales (anteponer al comando, p. ej. CTID=237 IP=192.168.20.37/24,gw=192.168.20.1):
#   CTID  HOSTNAME_CT  CORES  RAM  SWAP  DISK  STORAGE  TEMPLATE_STORAGE  BRIDGE  IP  VLAN
#   REPO_URL  BRANCH  YES=1 (sin confirmación)
set -euo pipefail

REPO_URL="${REPO_URL:-https://github.com/NaturalDevCR/m5-stack-assist.git}"
BRANCH="${BRANCH:-main}"
RAW_BASE="${RAW_BASE:-https://raw.githubusercontent.com/NaturalDevCR/m5-stack-assist/$BRANCH}"
HOSTNAME_CT="${HOSTNAME_CT:-danny-bridge}"
CORES="${CORES:-1}"
RAM="${RAM:-512}"
SWAP="${SWAP:-256}"
DISK="${DISK:-4}"
STORAGE="${STORAGE:-local-lvm}"
TEMPLATE_STORAGE="${TEMPLATE_STORAGE:-local}"
BRIDGE="${BRIDGE:-vmbr0}"
IP="${IP:-dhcp}"
VLAN="${VLAN:-}"

msg() { printf '\e[1;36m[danny]\e[0m %s\n' "$*"; }
die() { printf '\e[1;31m[danny] ERROR:\e[0m %s\n' "$*" >&2; exit 1; }

[[ $EUID -eq 0 ]] || die "ejecutar como root en el nodo Proxmox"
command -v pct >/dev/null || die "no se encontró 'pct': ejecutar en un nodo Proxmox VE"
CTID="${CTID:-$(pvesh get /cluster/nextid)}"
pct status "$CTID" >/dev/null 2>&1 && die "el CTID $CTID ya existe (usa CTID=<otro>)"
pvesm status --storage "$STORAGE" >/dev/null 2>&1 || die "no existe el almacenamiento '$STORAGE' (usa STORAGE=<nombre>)"

msg "Buscando plantilla Debian…"
pveam update >/dev/null || true
TEMPLATE="$(pveam available --section system | awk '{print $2}' | grep -E '^debian-13-standard' | sort -V | tail -1)"
[[ -n "$TEMPLATE" ]] || TEMPLATE="$(pveam available --section system | awk '{print $2}' | grep -E '^debian-12-standard' | sort -V | tail -1)"
[[ -n "$TEMPLATE" ]] || die "no se encontró plantilla Debian 12/13 en pveam"

NET="name=eth0,bridge=$BRIDGE,ip=$IP"
[[ -n "$VLAN" ]] && NET="$NET,tag=$VLAN"

cat <<EOF

  Danny Bridge — nuevo contenedor LXC
  ───────────────────────────────────
  CTID ............ $CTID
  Hostname ........ $HOSTNAME_CT
  Plantilla ....... $TEMPLATE
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
  pveam download "$TEMPLATE_STORAGE" "$TEMPLATE" >/dev/null
fi

msg "Creando contenedor $CTID…"
pct create "$CTID" "$TEMPLATE_STORAGE:vztmpl/$TEMPLATE" \
  --hostname "$HOSTNAME_CT" --cores "$CORES" --memory "$RAM" --swap "$SWAP" \
  --rootfs "$STORAGE:$DISK" --net0 "$NET" \
  --unprivileged 1 --features nesting=1 --onboot 1 --timezone host \
  --description "Danny voice bridge — $REPO_URL" >/dev/null
pct start "$CTID"

msg "Esperando red en el contenedor…"
for _ in $(seq 1 30); do
  if pct exec "$CTID" -- getent hosts deb.debian.org >/dev/null 2>&1; then break; fi
  sleep 2
done
pct exec "$CTID" -- getent hosts deb.debian.org >/dev/null 2>&1 || die "el contenedor no tiene acceso a Internet"

msg "Instalando Danny Bridge dentro del contenedor…"
pct exec "$CTID" -- bash -c "apt-get update -qq && DEBIAN_FRONTEND=noninteractive apt-get install -y -qq curl ca-certificates >/dev/null"
pct exec "$CTID" -- bash -c "curl -fsSL '$RAW_BASE/install/lxc-install.sh' | REPO_URL='$REPO_URL' BRANCH='$BRANCH' bash"

CT_IP="$(pct exec "$CTID" -- hostname -I | awk '{print $1}')"
cat <<EOF

  ✔ Contenedor $CTID listo. IP: ${CT_IP:-desconocida}

  Configurar:   pct enter $CTID
                nano /etc/danny-bridge/danny-bridge.env
                nano /etc/danny-bridge/config.yaml
                systemctl start danny-bridge && journalctl -u danny-bridge -f

  El AtomS3R se conectará a:  ws://${CT_IP:-<ip>}:8765/v0/terminal

EOF
