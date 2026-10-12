# Danny — asistente de voz interactivo (Room 237 Halloween Party)

Personaje conversacional con ElevenLabs Agents en un M5Stack AtomS3R + Atomic Audio-3.5 Base,
integrado con Home Assistant. Arquitectura reutilizable: terminal de audio ⇄ puente ⇄ agente.

## Estado

| Hito | Estado |
|---|---|
| Investigación y arquitectura | Decidida → [docs/01](docs/01-investigacion-y-decision-arquitectura.md) |
| Agente ElevenLabs (prompt, voz, base de conocimiento) | Publicado v1.7 → [docs/02](docs/02-configuracion-agente-elevenlabs.md) |
| H2/H4/H5: puente (sesiones, ElevenLabs, Home Assistant) | Listo → [bridge](bridge/README.md) |
| H1 hardware (AtomS3R) | Pendiente de recibir el hardware |
| H6–H7 | Pendiente |

## Instalar el puente en Proxmox (LXC)

En la shell del nodo Proxmox:

```bash
bash -c "$(curl -fsSL https://raw.githubusercontent.com/NaturalDevCR/m5-stack-assist/main/install/proxmox-lxc.sh)"
```

Crea un contenedor Debian sin privilegios e instala `danny-bridge` como servicio. Opciones
y configuración posterior en [bridge/README.md](bridge/README.md#instalación-en-proxmox-lxc).

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
install/                    instaladores (Proxmox LXC one-liner, Debian)
```

## Seguridad

Ninguna credencial va al repositorio: `.env`, `secrets.yaml` y `sdkconfig.local` están en
`.gitignore`; se versionan sólo plantillas `*.example`.
