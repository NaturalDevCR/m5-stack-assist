# danny-bridge — puente de voz

Servicio Python que conecta un **terminal de audio** (AtomS3R o el micrófono/parlante del
equipo) con **ElevenLabs Agents** y lo expone en **Home Assistant** por MQTT.

```
Terminal (AtomS3R / audio local) ⇄ danny-bridge ⇄ ElevenLabs Agents
                                        ⇅ MQTT Discovery
                                  Home Assistant
```

## Qué hace

- Una conversación por grupo de visitantes: saludo variado (`{{saludo}}` desde
  `personas/danny/saludos.txt`), conversación, despedida y cierre.
- **Half-duplex anti-eco:** mientras Danny habla (más 300 ms) envía silencio en lugar del
  micrófono, para que nunca se escuche a sí mismo.
- **Inactividad:** se cuenta desde que Danny termina su respuesta a algo que dijo el
  visitante. Sus "¿siguen ahí?" no reinician el contador. Al vencer, provoca la despedida
  enviando una frase de visitante ("ya nos tenemos que ir"). Una "nota del sistema" activa
  la protección anti prompt-injection del agente y corta la llamada.
- **Límites de costo:** duración máxima por sesión, una sola sesión a la vez, enfriamiento
  entre grupos, interruptor maestro, minutos y costo estimado del día.
- **Reconexión:** MQTT con reintentos; los terminales se reconectan solos; el servicio
  systemd se reinicia si falla.

## Instalación en Proxmox (LXC)

En la shell del nodo Proxmox:

```bash
bash -c "$(curl -fsSL https://raw.githubusercontent.com/NaturalDevCR/m5-stack-assist/main/install/proxmox-lxc.sh)"
```

Crea un LXC Debian 13 sin privilegios (Debian 12 en Proxmox VE 8) (1 núcleo, 512 MB, 4 GB, DHCP en `vmbr0`), instala el
servicio y genera el token del terminal. Para personalizar, antepone variables, por ejemplo:

```bash
CTID=237 IP=192.168.20.37/24,gw=192.168.20.1 VLAN=20 STORAGE=local-zfs bash -c "$(curl -fsSL https://raw.githubusercontent.com/NaturalDevCR/m5-stack-assist/main/install/proxmox-lxc.sh)"
```

Después, dentro del contenedor (`pct enter <CTID>`):

1. `nano /etc/danny-bridge/danny-bridge.env`: `ELEVENLABS_API_KEY` (permiso *ElevenAgents:
   Escribir*) y `MQTT_PASSWORD`.
2. `nano /etc/danny-bridge/config.yaml`: `mqtt.host` y `mqtt.username`.
3. Verificar:

   ```bash
   runuser -u danny -- /opt/danny-bridge/bridge/.venv/bin/danny-bridge check --config /etc/danny-bridge/config.yaml --env /etc/danny-bridge/danny-bridge.env
   ```

4. `systemctl start danny-bridge && journalctl -u danny-bridge -f`

Actualizar: `danny-bridge-update`. En cualquier Debian/Ubuntu sin Proxmox se puede usar
`install/lxc-install.sh` directamente.

## Home Assistant

Requisito: integración MQTT con el broker (p. ej. Mosquitto) y un usuario MQTT para el puente.
Las entidades aparecen solas en el dispositivo **Danny**:

| Entidad | Uso |
|---|---|
| `sensor.danny_estado` | disabled · idle · connecting · listening · speaking · farewell · cooldown · error |
| `binary_sensor.danny_hablando` / `_escuchando` / `_terminal` | estado en vivo y conexión del Atom |
| `button.danny_iniciar_conversacion` | inicio manual (ignora el enfriamiento) |
| `button.danny_visitante_detectado` | para automatizaciones de presencia (respeta el enfriamiento) |
| `button.danny_terminar_conversacion` | pide la despedida y cierra |
| `switch.danny_activo` | interruptor maestro (apagado = nunca abre sesiones) |
| `number.danny_max_session_s` / `_idle_timeout_s` / `_cooldown_s` | ajustes en vivo (persisten) |
| `sensor.danny_minutos_hoy` / `_costo_hoy` / `_sesiones_hoy` | control de costos |
| `event.danny_sesion` | `session_started`, `session_ended`, `error` para automatizaciones |

Ejemplo: un sensor de presencia inicia la conversación.

```yaml
alias: Danny - visitante detectado
triggers:
  - trigger: state
    entity_id: binary_sensor.presencia_entrada   # tu sensor
    to: "on"
conditions:
  - condition: state
    entity_id: switch.danny_activo
    state: "on"
actions:
  - action: button.press
    target:
      entity_id: button.danny_visitante_detectado
mode: single
```

Ejemplo: luz roja mientras Danny habla.

```yaml
alias: Danny - luz al hablar
triggers:
  - trigger: state
    entity_id: binary_sensor.danny_hablando
actions:
  - action: "light.turn_{{ 'on' if trigger.to_state.state == 'on' else 'off' }}"
    target:
      entity_id: light.foco_danny     # tu luz
mode: restart
```

## Prueba local (sin Atom)

Con el micrófono y el parlante de tu computadora. Usa audífonos o baja el volumen: el
half-duplex evita el eco, pero así es más cómodo.

```bash
cd bridge && cp config.local.example.yaml config.local.yaml && uv sync --extra local
```

```bash
cd bridge && uv run danny-bridge run --config config.local.yaml --env ../tools/el_spike/.env --enter
```

Enter inicia la conversación; Enter de nuevo hace que Danny se despida.

## Terminal WebSocket (protocolo v0)

`ws://<puente>:8765/v0/terminal?id=<nombre>` con `Authorization: Bearer <DANNY_TERMINAL_TOKEN>`.
Audio PCM s16le 16 kHz mono en tramas binarias (ambos sentidos); control en JSON:
`{"t":"mic","on":true}`, `{"t":"flush"}`, `{"t":"state","s":"speaking"}` (bajada) y
`{"t":"hello"}`, `{"t":"button","ev":"short"}`, `{"t":"stats",...}` (subida). Detalle en
[`danny_bridge/terminals/websocket.py`](danny_bridge/terminals/websocket.py).

## Desarrollo

```bash
cd bridge && uv run pytest -q
```

Las pruebas usan un servidor que imita el protocolo de ElevenLabs y un terminal en memoria;
no usan red ni credenciales.
