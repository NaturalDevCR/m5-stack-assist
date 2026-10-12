# el_spike — prueba de ElevenLabs Agents sin hardware

Abre una sesión real con el agente Danny, le "habla" con preguntas grabadas en WAV
exactamente como lo hará el terminal (PCM 16 bit, 16 kHz, mono, en tiempo real, con
silencio continuo entre preguntas) y mide latencias.

Usa el protocolo WebSocket documentado directamente, sin el SDK, para registrar y
cronometrar cada evento. No envía ni guarda la API key fuera de `.env`, y no registra el
token de la signed URL.

## Requisitos

- macOS con `uv` (Python ≥ 3.11). Dependencia única: `websockets` 17.x.
- Agente configurado según [docs/02-configuracion-agente-elevenlabs.md](../../docs/02-configuracion-agente-elevenlabs.md).

## Uso

```bash
cd tools/el_spike && cp .env.example .env
```

Editar `.env` (API key y agent_id). Después:

```bash
cd tools/el_spike && ./make_test_wavs.sh
```

```bash
cd tools/el_spike && uv run el_spike.py --saludos ../../personas/danny/saludos.txt --wav wavs/01-primera-visita.wav --wav wavs/02-room-237.wav
```

Comparar región EE. UU.:

```bash
cd tools/el_spike && uv run el_spike.py --base-url https://api.us.elevenlabs.io --saludos ../../personas/danny/saludos.txt --wav wavs/03-pelicula.wav
```

Resultados en `out/session-<fecha>/`:

- `danny.wav`: todo el audio que generó Danny (saludo + respuestas).
- `events.jsonl`: cada evento recibido con su tiempo relativo (el audio se sustituye por su tamaño).
- `summary.json`: formatos, latencias por turno, pings, cierre, errores y costo estimado.

Código de salida: `0` todo correcto · `1` sesión con errores o turnos sin respuesta · `2` WAV inválido · `3` red/credenciales.

## Qué mide

Para cada pregunta, desde el **final real de la voz** (detectado por energía, no por el
final del archivo):

- `transcripción`: llega `user_transcript` (ElevenLabs detectó fin de turno).
- `texto`: llega `agent_response`.
- `PRIMER AUDIO`: llega el primer trozo de audio de Danny. **Esta es la métrica clave.**

No incluye lo que añadirá el hardware (Wi-Fi + jitter buffer, ~150–250 ms estimados).

## Opciones útiles

| Opción | Defecto | Para qué |
|---|---|---|
| `--chunk-ms` | 100 | Tamaño de trama enviada. Probar 50/100/250 y ver efecto en latencia |
| `--idle-s` | 1.5 | Silencio del agente que se toma como fin de su turno |
| `--max-seconds` | 90 | Límite duro de sesión (costos) |
| `--public` | — | Agente sin autenticación (no recomendado) |
| `--var clave=valor` | — | Otras variables dinámicas |
| `-v` | — | Registro detallado |

## Pruebas offline

No usan red ni credenciales: un servidor local imita la forma de los mensajes de ElevenLabs.

```bash
cd tools/el_spike && uv run pytest -q
```
