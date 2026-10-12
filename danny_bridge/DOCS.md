# Danny Bridge

Conecta el terminal de voz (M5Stack AtomS3R + Atomic Audio-3.5 Base) con el agente
**Danny** de ElevenLabs Agents y lo expone en Home Assistant.

## Configuración

1. **Clave API de ElevenLabs**: créala en ElevenLabs → Desarrolladores → API Keys con
   el permiso *ElevenAgents: Escribir* (con *Leído* falla con `missing the permission
   convai_write`).
2. **ID del agente**: ya viene el de Danny.
3. **Token del terminal**: déjalo vacío y la app genera uno; lo verás en la pestaña
   *Registro* (`Token del terminal generado…`). Lo necesita el firmware del Atom.
4. Guarda e **inicia** la app.

MQTT se configura solo si tienes la app **Mosquitto broker**. Si usas otro broker,
completa los campos MQTT opcionales.

## Entidades

En *Ajustes → Dispositivos → MQTT → Danny*:

- `sensor.danny_estado`: idle · connecting · listening · speaking · farewell · cooldown · error
- `binary_sensor.danny_hablando`, `binary_sensor.danny_escuchando`, `binary_sensor.danny_terminal`
- `button.danny_iniciar_conversacion` (manual) y `button.danny_visitante_detectado`
  (para sensores de presencia: respeta la pausa entre grupos)
- `button.danny_terminar_conversacion`
- `switch.danny_activo`: interruptor maestro
- tiempos ajustables, minutos y costo estimado del día, `event.danny_sesion`

Los tiempos se pueden ajustar desde el dispositivo en HA; si cambias esos valores en el
formulario de la app, el formulario manda.

## Terminal

El Atom se conecta a `ws://<IP de Home Assistant>:8765/v0/terminal?id=danny-01` con la
cabecera `Authorization: Bearer <token>`.

## Problemas frecuentes

- **`falta la clave API de ElevenLabs`**: completa el campo en Configuración.
- **`get-signed-url HTTP 401`**: permiso de la clave insuficiente (ver arriba).
- **`exceeds your quota limit`**: sin créditos en ElevenLabs.
- **Danny no aparece en HA**: instala la app Mosquitto broker y la integración MQTT.
