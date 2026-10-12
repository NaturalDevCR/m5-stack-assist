# Voice Agent Bridge

Convierte un terminal de voz (p. ej. M5Stack AtomS3R + Atomic Audio-3.5 Base) en un
asistente conversacional con un **agente de ElevenLabs**, y lo expone en Home Assistant.
Sirve para cualquier personaje o asistente: el agente (prompt, voz, conocimiento) vive en
ElevenLabs; esta app gestiona el audio, las sesiones, los costos y Home Assistant.

## Configuración

1. **Nombre del agente**: p. ej. `Danny`. Define el dispositivo en Home Assistant y el
   prefijo de las entidades (`Danny` → `sensor.danny_estado`).
2. **Clave API de ElevenLabs**: ElevenLabs → Desarrolladores → API Keys, con permiso
   *ElevenAgents: Escribir* (con *Leído* falla con `missing the permission convai_write`).
3. **ID del agente** de ElevenLabs (`agent_…`).
4. **Token del terminal**: déjalo vacío y la app genera uno; aparece en la pestaña
   *Registro* (`Token del terminal generado…`). Lo necesita el firmware del terminal.
5. Guarda e **inicia** la app.

MQTT se configura solo si tienes la app **Mosquitto broker**. Con otro broker, completa
los campos MQTT opcionales.

### Requisitos del agente en ElevenLabs

- Audio de entrada y salida en **PCM 16000 Hz**.
- Autenticación activada (la app pide una signed URL por sesión).
- Si el primer mensaje usa la variable `{{saludo}}`, la app la rellena con un saludo al
  azar del banco de saludos (campo *Saludos* o los incluidos para ese nombre).
- Herramienta de sistema *End call* habilitada, para que el agente cuelgue al despedirse.

## Entidades (ejemplo con nombre "Danny")

En *Ajustes → Dispositivos → MQTT → Danny*:

- `sensor.danny_estado`: idle · connecting · listening · speaking · farewell · cooldown · error
- `binary_sensor.danny_hablando`, `binary_sensor.danny_escuchando`, `binary_sensor.danny_terminal`
- `button.danny_iniciar_conversacion` (manual) y `button.danny_visitante_detectado`
  (para sensores de presencia: respeta la pausa entre grupos)
- `button.danny_terminar_conversacion`
- `switch.danny_activo`: interruptor maestro
- tiempos ajustables, minutos y costo estimado del día, `event.danny_sesion`

Si cambias los tiempos en el formulario de la app, mandan sobre los ajustados desde HA.

## Terminal

El terminal se conecta a `ws://<IP de Home Assistant>:8765/v0/terminal?id=<nombre>` con la
cabecera `Authorization: Bearer <token>`. Protocolo: audio PCM 16 kHz mono en tramas
binarias y control en JSON (ver el repositorio).

## Problemas frecuentes

- **`falta la clave API de ElevenLabs`**: completa el campo en Configuración.
- **`get-signed-url HTTP 401`**: permiso de la clave insuficiente (ver arriba).
- **`exceeds your quota limit`**: sin créditos en ElevenLabs.
- **El agente corta con `1008 ... 'Prompt Injection' guardrail`**: la *frase de despedida*
  no debe parecer una instrucción ("nota del sistema"); usa una frase natural de usuario.
- **No aparece en HA**: instala la app Mosquitto broker y la integración MQTT.
