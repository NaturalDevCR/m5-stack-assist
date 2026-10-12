# Danny — asistente de voz interactivo (Room 237 Halloween Party)

Personaje conversacional con ElevenLabs Agents en un M5Stack AtomS3R + Atomic Audio-3.5 Base,
integrado con Home Assistant. Arquitectura reutilizable: terminal de audio ⇄ puente ⇄ agente.

## Estado

| Hito | Estado |
|---|---|
| Investigación y arquitectura | Decidida → [docs/01](docs/01-investigacion-y-decision-arquitectura.md) |
| Agente ElevenLabs (prompt, voz, base de conocimiento) | Publicado v1.7 → [docs/02](docs/02-configuracion-agente-elevenlabs.md) |
| H2/H4/H5: puente (sesiones, ElevenLabs, Home Assistant) | Listo como app de HA → [voice_agent_bridge](voice_agent_bridge/DOCS.md) · código en [bridge](bridge/README.md) |
| H1 hardware (AtomS3R) | Pendiente de recibir el hardware |
| H6–H7 | Pendiente |

## Instalar el puente como app de Home Assistant (recomendado)

1. En Home Assistant: **Ajustes → Aplicaciones (antes "Complementos") → Tienda → ⋮ → Repositorios** y agrega:

   ```
   https://github.com/NaturalDevCR/m5-stack-assist
   ```

2. Instala **Voice Agent Bridge**. En la pestaña **Configuración**: nombre del agente `Danny`,
   clave API de ElevenLabs (permiso *ElevenAgents: Escribir*) e inicia la app.
3. MQTT se configura solo con la app Mosquitto; Danny aparece como dispositivo en
   *Dispositivos → MQTT*. El token para el AtomS3R aparece en la pestaña **Registro**.

Detalle: [voice_agent_bridge/DOCS.md](voice_agent_bridge/DOCS.md). La app es genérica: sirve para
cualquier agente de ElevenLabs (recepción, guía, otro personaje) cambiando nombre, agente y saludos.

### Alternativa: LXC en Proxmox

Para correr el puente fuera de Home Assistant, en la shell del nodo Proxmox:

```bash
bash -c "$(curl -fsSL https://raw.githubusercontent.com/NaturalDevCR/m5-stack-assist/main/install/proxmox-lxc.sh)"
```

Opciones y configuración en [bridge/README.md](bridge/README.md#instalación-en-proxmox-lxc).

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
bridge/                     puente Python: sesiones, ElevenLabs, MQTT/HA, terminales
voice_agent_bridge/         app de Home Assistant "Voice Agent Bridge" (empaqueta bridge/)
repository.yaml             repositorio de apps de Home Assistant
install/                    instaladores (Proxmox LXC one-liner, Debian)
```

## Seguridad

Ninguna credencial va al repositorio: `.env`, `secrets.yaml` y `sdkconfig.local` están en
`.gitignore`; se versionan sólo plantillas `*.example`.
