#!/usr/bin/env bash
# Genera preguntas de prueba en español (WAV PCM 16 bit, mono, 16 kHz) con la
# voz del sistema de macOS. Sólo para pruebas: la voz sintética es más limpia
# que un visitante real en el evento.
set -euo pipefail

VOICE="${VOICE:-Paulina}"          # es_MX; ver: say -v '?' | grep es_
OUT_DIR="${1:-$(dirname "$0")/wavs}"
mkdir -p "$OUT_DIR"

command -v say >/dev/null || { echo "Se requiere macOS (say)"; exit 1; }
command -v afconvert >/dev/null || { echo "Se requiere afconvert (macOS)"; exit 1; }

gen() {
  local name="$1" text="$2" tmp
  tmp="$(mktemp -t elspike).aiff"
  say -v "$VOICE" -o "$tmp" "$text"
  afconvert -f WAVE -d LEI16@16000 -c 1 "$tmp" "$OUT_DIR/$name.wav"
  rm -f "$tmp"
  echo "  $OUT_DIR/$name.wav  «$text»"
}

echo "Generando con la voz $VOICE:"
gen 01-primera-visita   "Sí, es nuestra primera vez aquí. ¿Qué nos recomienda?"
gen 02-room-237         "¿Qué hay dentro de la habitación doscientos treinta y siete?"
gen 03-pelicula         "¿Cuál es su película de terror favorita?"
gen 04-sendero          "¿El sendero del terror da mucho miedo?"
gen 05-operativa        "¿A qué hora es el concurso de disfraces?"
gen 06-miedo            "La verdad, me da un poco de miedo todo esto."
gen 07-ingles           "Do you speak English?"
gen 08-despedida        "Bueno, muchas gracias, ya nos vamos."
