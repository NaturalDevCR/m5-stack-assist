# Danny — asistente de voz interactivo (Room 237 Halloween Party)

Personaje conversacional con ElevenLabs Agents en un M5Stack AtomS3R + Atomic Audio-3.5 Base,
integrado con Home Assistant. Arquitectura reutilizable: terminal de audio ⇄ puente ⇄ agente.

## Estado

| Hito | Estado |
|---|---|
| Investigación y arquitectura | Decidida → [docs/01](docs/01-investigacion-y-decision-arquitectura.md) |
| H2 adelantado: prueba ElevenLabs sin hardware | Listo para ejecutar → [tools/el_spike](tools/el_spike/README.md) |
| H1 hardware | Pendiente de recibir el hardware |
| H3–H7 | Pendiente |

## Estructura

```
docs/                       decisiones y guías
  01-investigacion-y-decision-arquitectura.md
  02-configuracion-agente-elevenlabs.md
personas/danny/             personalidad (independiente del hardware)
  prompt.md                 system prompt (sólo comportamiento)
  kb/                       base de conocimiento del agente (datos)
  saludos.txt               banco de saludos → variable {{saludo}}
  despedidas.txt            despedidas de respaldo
tools/el_spike/             prueba de conversación real sin hardware + pruebas offline
firmware/                   (H1) firmware ESP-IDF del terminal
bridge/                     (H2+) puente Python: sesiones, ElevenLabs, MQTT/HA
```

## Seguridad

Ninguna credencial va al repositorio: `.env`, `secrets.yaml` y `sdkconfig.local` están en
`.gitignore`; se versionan sólo plantillas `*.example`.
