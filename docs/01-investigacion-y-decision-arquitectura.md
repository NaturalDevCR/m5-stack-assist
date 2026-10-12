# Danny — Investigación técnica y decisión de arquitectura

Fecha: 2026-10-10 · Estado: **decisión tomada, pendiente de validar con hardware (Hito 1)**

Combinación evaluada: **M5Stack AtomS3R + Atomic Audio-3.5 Base (A166) + ElevenLabs Agents**.

Leyenda usada en todo el documento:

- **[CONFIRMADO]** = leído en documentación o código fuente oficial/público hoy.
- **[INFERIDO]** = deducción razonable a partir de fuentes, sin confirmación directa.
- **[EXPERIMENTAL]** = posible, pero sin evidencia de que funcione en esta combinación; requiere prueba.
- **[A MEDIR]** = sólo se puede saber con el hardware o con pruebas reales.

---

## 0. Resumen ejecutivo

**Decisión: Opción B — AtomS3R como terminal de audio "tonto" + puente Python en Linux (Proxmox) que gestiona ElevenLabs Agents y Home Assistant.**

| Capa | Elección |
|---|---|
| Firmware del Atom | **ESP-IDF 6.1** propio y mínimo (no ESPHome, no Arduino) |
| Transporte Atom ⇄ puente | WebSocket en LAN, PCM 16 bit / 16 kHz / mono, sin compresión |
| Servicio conversacional | Puente Python asyncio. Cliente: SDK oficial `elevenlabs` (v2.71.0) o protocolo WebSocket directo; se decide con los datos de `tools/el_spike` (que usa el protocolo directo para ver cada evento) |
| Autenticación ElevenLabs | Agente privado + *signed URL* generada por el puente. API key sólo en el puente |
| Home Assistant | MQTT Discovery desde el puente (entidades, botones, eventos) |
| Eco | Fase 1: half-duplex controlado por el Atom (mic silenciado mientras Danny habla). AEC real = experimental, fase posterior |
| Herramientas del agente | *Client tools* ejecutadas por el puente con lista blanca de efectos. HA nunca se expone a Internet |
| Plan de contingencia | El mismo puente acepta un "terminal" local (mini PC + USB) sin cambiar nada de ElevenLabs ni HA |

Razones principales:

1. **La opción A necesita un servidor de todos modos.** Un agente privado exige *signed URL*, y ésta se pide con la API key. La única forma de no tener servidor es un agente público con `agent_id` en el firmware, lo que deja el agente abierto a cualquiera que lo extraiga (riesgo de costos). Si ya hay servidor, darle la sesión completa cuesta poco más.
2. **La latencia añadida por el puente es despreciable** [INFERIDO, A MEDIR]: un salto LAN cableado/Wi-Fi de unos ms contra cientos de ms de ASR + LLM + TTS y ~60–120 ms de RTT Costa Rica ⇄ EE. UU. El puente no reduce latencia; tampoco la empeora de forma apreciable.
3. **Mantenibilidad**: prompt, tiempos, lógica de sesión, costos, registros y HA se cambian en Python sin reflashear. El firmware queda pequeño y estable.
4. **Reutilización**: el puente es multi-terminal y multi-personaje; Danny es sólo una configuración (agent_id + saludos + efectos permitidos).
5. **Calendario**: el evento es en ~3 semanas. Separar firmware simple y lógica en Python reduce riesgo, y el plan de contingencia (mini PC) reutiliza el 100 % del puente.

---

## 1. Hardware: hallazgos verificados

### 1.1 Pinout AtomS3R + Atomic Audio-3.5 Base

| Señal | GPIO AtomS3R | Fuente |
|---|---|---|
| I2S LRCK / WS | GPIO6 | [CONFIRMADO] ejemplo ESPHome oficial M5Stack |
| I2S BCLK (SCLK) | GPIO8 | [CONFIRMADO] ejemplo ESPHome oficial |
| I2S DOUT (ESP → ES8311 DSDIN, altavoz) | GPIO5 | [CONFIRMADO] ejemplo ESPHome oficial |
| I2S DIN (ES8311 ASDOUT → ESP, micrófono) | GPIO7 | [INFERIDO fuerte] mismo pin en YAML voice-assistant M5 para Echo Base y en xiaozhi `atoms3r-echo-base`; el ejemplo del Audio-3.5 no configura micrófono |
| MCLK | no conectado (`use_mclk: false`) | [CONFIRMADO] |
| I2C audio (ES8311 + expansor) | SDA GPIO38 / SCL GPIO39 | [CONFIRMADO] |
| ES8311 dirección I2C | 0x18 | [INFERIDO fuerte] xiaozhi detecta Echo Base por 0x18 + 0x43; doc A166 no lo indica |
| PI4IOE5V6408 (expansor) | 0x43; P0 = PW_AMP (habilita NS4150B) | [CONFIRMADO] doc A166 + YAML |
| I2C interno (LP5562 retroiluminación 0x30) | SDA GPIO45 / SCL GPIO0 | [CONFIRMADO] YAML + xiaozhi |
| LCD 128×128 SPI | CLK 15, MOSI 21, CS 14, DC 42, RST 48 | [CONFIRMADO]. Controlador: ESPHome usa ST7789V, xiaozhi detecta GC9107/ST7735 → [A MEDIR] |
| Botón | GPIO41 | [CONFIRMADO] |

**Conclusión**: a nivel de bus, el Audio-3.5 Base es eléctricamente igual que el *Atomic Echo Base* (mismo ES8311, mismos pines, mismo PI4IOE en 0x43) más el conector TRRS y el selector de micrófono [INFERIDO fuerte]. Esto importa mucho: todo el código existente para "AtomS3R + Echo Base" es reutilizable como referencia. Se confirmará en Hito 1 con un escaneo I2C.

### 1.2 Códec y salida

- ES8311: mono, 24 bit, 16–64 kHz según M5Stack [CONFIRMADO]. **Un solo bus I2S compartido para captura y reproducción ⇒ ambas direcciones deben usar la misma frecuencia de muestreo** (LRCK común) [CONFIRMADO por diseño; xiaozhi hace `assert(input_sample_rate_ == output_sample_rate_)`].
- El ejemplo ESPHome oficial usa 48 kHz sólo salida. Para voz full-duplex usaremos 16 kHz (ver §8).
- Selector de micrófono: ON = siempre micrófono interno; OFF = conmuta a externo al conectar TRRS [CONFIRMADO].
- TRRS CTIA, "reproducción estéreo" en la doc: con códec mono, L y R llevarán la misma señal [INFERIDO].
- **No hay especificación de nivel de salida del jack** ni de si el jack sale del DAC o del amplificador, ni de si PW_AMP (P0) silencia también el jack [A MEDIR]. No tratarlo como salida de línea profesional: empezar con volumen bajo, comprobar offset DC, zumbido y saturación.

### 1.3 Advertencias del ejemplo oficial ESPHome

- El YAML oficial (`examples/sensor/atomic-audio3.5-base.yaml`, rama `main` hoy) **contiene la clave `binary_sensor:` duplicada** (una vacía antes de `button:`). Puede fallar la validación o ignorarse según versión; hay que corregirlo al copiarlo.
- Contiene la contraseña del AP de respaldo en claro y claves vacías de API/OTA: moverlas a `secrets.yaml`.
- Requiere ESPHome ≥ 2026.4.0 (actual: 2026.9.1).

---

## 2. Respuestas a las 10 preguntas

### 2.1 ¿Qué ejemplos oficiales capturan y reproducen audio con el ES8311?

| Ejemplo | Captura | Reproducción | Utilidad |
|---|---|---|---|
| M5Stack ESPHome `atomic-audio3.5-base.yaml` | **No** (`use_microphone: false`, sin `microphone:`) | Sí, 48 kHz, media player HA | Validar salida y jack rápido |
| M5Stack ESPHome `common/atoms3r-with-echo-base.yaml` (voice assistant) | Sí, `i2s_audio` mic GPIO7 16 kHz | Sí, 48 kHz | Validar micrófono rápido (mismo hardware de bus) |
| Arduino `M5Atomic-EchoBase` | Sí | Sí, full-duplex declarado | Referencia; la doc del A166 apunta a esta librería |
| xiaozhi-esp32 `boards/m5stack/atoms3r-echo-base` (ESP-IDF 6, MIT) | Sí, 24 kHz duplex | Sí | **Mejor referencia de bajo nivel**: I2S duplex en un puerto, `esp_codec_dev` ES8311 sin MCLK, PI4IOE, LP5562, LCD |

Ninguno es oficial "para A166 + conversación". Ninguno trae ElevenLabs.

### 2.2 ¿ESPHome cubre nuestro caso completo?

**No.** [CONFIRMADO]

- ESPHome tiene `microphone`, `speaker`, `voice_assistant`, `micro_wake_word`, pero `voice_assistant` sólo habla con el **pipeline Assist de Home Assistant** (STT → agente de conversación → TTS por turnos). No existe componente ESPHome para ElevenLabs Agents ni para enviar audio crudo por WebSocket a un servidor arbitrario.
- Podría usarse HA como intermediario (integración personalizada que reciba audio Assist y lo reenvíe a ElevenLabs), pero el pipeline Assist es por turnos, añade capas y no está pensado para una sesión conversacional continua con interrupciones. Más complejo que la opción B.
- La documentación del micrófono I2S de ESPHome no garantiza full-duplex en un bus compartido [CONFIRMADO: no lo documenta].

**Uso de ESPHome en este proyecto**: sólo como prueba rápida de hardware en Hito 1 (cero código), no como firmware final.

### 2.3 ¿Es preferible ESP-IDF?

**Sí.** ESP-IDF 6.1 (publicado 2026-08-27) con:

- `driver/i2s_std` en modo duplex (TX + RX en el mismo puerto, patrón usado por xiaozhi).
- `espressif/esp_codec_dev ~1.6.2` (estable; la 2.0 está en beta, evitarla) para ES8311 sin MCLK.
- `espressif/esp_websocket_client 1.8.0`.
- `espressif/esp-sr 2.x` sólo si llegamos a la fase AEC experimental.

Arduino descartado: más capas (arduino-esp32 sobre IDF), menos control de I2S/DMA/PSRAM, y las referencias más maduras para este hardware ya están en IDF.

### 2.4 Full-duplex y cancelación de eco

- **Full-duplex de hardware**: sí [CONFIRMADO por M5Stack]. Reproducir y capturar a la vez es viable con un puerto I2S en duplex y la misma frecuencia.
- **AEC**: no viene "gratis".
  - El ES8311 es mono y en esta placa no hay canal de referencia analógico del altavoz hacia el ADC. Xiaozhi, en su placa AtomS3R + Echo Base, define `AUDIO_INPUT_REFERENCE true` en `config.h`, **pero su clase `Es8311AudioCodec` fija `input_reference_ = false` y no usa ese valor** [CONFIRMADO leyendo el código hoy]. Es decir, ni el proyecto open-source más maduro hace AEC por hardware en este diseño.
  - Si el ES8311 tiene un modo de lazo digital DAC→ASDOUT utilizable como referencia: [NO VERIFICADO], requiere leer el datasheet de registros y probar.
  - AEC por software en el ESP32-S3 con `esp-sr` AFE, alimentando como referencia las muestras que el propio firmware envía al I2S (mismo reloj ⇒ retardo casi constante): **[EXPERIMENTAL]**. Viable en teoría con 8 MB de PSRAM; no hay ejemplo para esta combinación.
  - AEC en el puente: mala idea, el jitter de Wi-Fi rompe la alineación referencia/micrófono.
  - ElevenLabs no documenta AEC en servidor para clientes WebSocket (en navegador lo hace el navegador) [INFERIDO de la doc].
- **Estrategia por fases** (decidida):
  - **E0 (base, Halloween)**: half-duplex en el Atom. Mientras haya audio en el búfer de reproducción + una cola de ~250 ms, el Atom envía **silencio digital** (ceros) en lugar del micrófono. El stream hacia ElevenLabs sigue continuo (no confunde al VAD). Danny nunca se oye a sí mismo. Se pierde el *barge-in*; aceptable con respuestas de 1–3 oraciones.
  - **E1 [EXPERIMENTAL]**: barge-in por umbral. Durante la reproducción, el micrófono pasa sólo si su energía supera el nivel de eco calibrado × factor durante >150 ms. Depende de la colocación física.
  - **E2 [EXPERIMENTAL]**: AEC con esp-sr AFE y referencia por software.
  - El modo se elige desde el puente (`duplex_mode: half | threshold | aec`) sin reflashear.
- **Física antes que software**: altavoz dentro/detrás de la figura apuntando lejos del micrófono; micrófono orientado al visitante con espuma antiviento; para ruido de DJ, micrófono externo direccional (cardioide/gooseneck/lavalier) por el TRRS a corta distancia de donde se para el visitante.

### 2.5 ¿Puede el AtomS3R conectarse directamente a ElevenLabs?

**Técnicamente sí** [INFERIDO fuerte]: ESP32-S3 + PSRAM soporta WSS (mbedTLS + `esp_websocket_client`), JSON + base64 de PCM 16 kHz (~43 kB/s de texto por sentido) es asumible. Proyectos como ElatoAI demuestran WSS de voz en tiempo real en ESP32-S3 (aunque ellos pasan por un servidor edge, no directo a ElevenLabs). No hay ejemplo oficial ElevenLabs para ESP32 [CONFIRMADO por búsqueda].

Problemas de la opción A:

- **Autenticación**: agente público (`agent_id` en firmware, cualquiera puede gastar tu saldo) o agente privado con signed URL ⇒ necesitas un servidor que la emita igualmente.
- Firmware mucho más complejo: TLS, certificados CA (y su caducidad), parseo JSON de eventos, base64, ping/pong, reconexión, gestión de sesión, costos, herramientas, HA… todo en C con reflasheo por cada cambio.
- Diagnóstico pobre en el evento (registros sólo por serie/HA).
- Ventaja real casi nula: ahorra un salto LAN de pocos ms.

### 2.6 ¿Sería más estable un puente Linux?

**Sí, por diseño** [INFERIDO]:

- El enlace inestable (Wi-Fi) queda en la LAN, con reconexión simple y sin TLS en el Atom.
- La sesión con ElevenLabs sale por Ethernet desde un servidor con reloj, DNS, CA y librerías actualizadas.
- El SDK oficial maneja ping/pong, signed URL, client tools e interrupciones.
- El puente puede forzar límites de sesión aunque el Atom se cuelgue (y viceversa: el agente tiene `max_duration_seconds` como red de seguridad).
- Un solo punto con registros, métricas y costos.

Riesgo nuevo: el puente es otra pieza que puede caer ⇒ systemd con `Restart=always`, watchdog y entidad de salud en HA.

### 2.7 Formatos de audio y frecuencias

ElevenLabs Agents acepta en entrada y salida: `pcm_8000`, `pcm_16000`, `pcm_22050`, `pcm_24000`, `pcm_44100`, `pcm_48000`, `ulaw_8000` [CONFIRMADO]. El evento `conversation_initiation_metadata` confirma en cada sesión los formatos activos. El SDK Python recomienda trozos de entrada de 4000 muestras (250 ms) [CONFIRMADO en código del SDK].

**Decisión inicial**:

- Agente: entrada `pcm_16000`, salida `pcm_16000`.
- I2S: 16 kHz, 16 bit, mono efectivo (slot estéreo, usar un canal), sin MCLK.
- Atom ⇄ puente: PCM s16le crudo, tramas de 20 ms (640 B) de subida. 256 kbit/s por sentido: trivial en LAN, sin Opus (evita CPU y latencia de códec).
- Puente → ElevenLabs: el puente agrupa a 100–250 ms (a medir; menos tamaño = menor latencia, más mensajes).
- **Cero remuestreo** en toda la cadena.

**Mejora posterior [A MEDIR]**: salida `pcm_24000` e I2S a 24 kHz (como xiaozhi) para voz más brillante; el puente remuestrearía el micrófono 24→16 kHz con `soxr` o enviaría `pcm_24000` si el agente lo acepta como entrada. Sólo si la diferencia de calidad se nota en el parlante real.

### 2.8 Bibliotecas y componentes realmente disponibles (verificado 2026-10-10)

| Pieza | Versión | Estado |
|---|---|---|
| ESP-IDF | v6.1 (2026-08-27) | Estable. xiaozhi exige ≥ 6.0.1 |
| `espressif/esp_codec_dev` | 1.6.2 estable (2.0.0-beta5 existe) | Usar 1.6.x |
| `espressif/esp_websocket_client` | 1.8.0 | Estable |
| `espressif/esp-sr` | 2.6.0 en registry | Sólo fase AEC |
| ESPHome | 2026.9.1 | Sólo pruebas de hardware |
| Componente M5 `es8311` para ESPHome | `github://m5stack/esphome-yaml/components@main` | Externo, rama `main` (no versionado) |
| SDK Python `elevenlabs` | v2.71.0 (2026-10-05) | `Conversation` / `AsyncConversation` con `AudioInterface` personalizable, `end_session()`, client tools, signed URL |
| Python puente | `websockets`, `aiomqtt`, `pydantic-settings`, `soxr` (opcional) | Maduras |

### 2.9 Protección de credenciales

- **ElevenLabs API key**: sólo en el puente, en `/etc/danny/bridge.env` (0600, usuario de servicio dedicado) o `systemd-creds`. Nunca en el repo ni en el Atom.
- **Agente privado** con autenticación activada; el puente pide signed URL por sesión (válida 15 min para iniciar; la sesión puede durar más) [CONFIRMADO]. No combinar signed URL y allowlist en el mismo agente [CONFIRMADO, doc ElevenLabs].
- **Overrides**: desactivados por defecto en el agente; activar sólo los estrictamente necesarios. Para saludos variados usar **variables dinámicas** (`{{saludo}}` en `first_message`), que no requieren abrir overrides.
- **Atom**: Wi-Fi y token de dispositivo en NVS (cargados por `menuconfig`/`sdkconfig.local` excluido del repo o por provisión serie). El puente rechaza conexiones sin token válido.
- **Red**: Atom en VLAN IoT de UniFi; regla de firewall que sólo le permita hablar con el puente (puerto WS) y NTP/DNS. Sin salida a Internet necesaria.
- **HA**: el puente usa un usuario MQTT propio con ACL limitada a sus *topics*. Si en el futuro usa la API REST de HA, un usuario HA dedicado (no admin) y sólo llamadas a scripts de una lista blanca.
- **Herramientas del agente**: *client tools* (las ejecuta el puente por el WebSocket ya abierto) en lugar de *server tools* (exigirían exponer HA a Internet). Parámetros como `enum` cerrado (`parpadeo`, `rojo`, `apagon_breve`…), mapeados a scripts concretos. Nunca entity_ids arbitrarios.
- Repo: `.gitignore` con `*.env`, `secrets.yaml`, `sdkconfig.local`, `credentials*`; plantillas `*.example`.

### 2.10 Implementación más sencilla y robusta

La de §3. En una frase: **firmware que sólo mueve audio y botones, puente que piensa.**

---

## 3. Arquitectura decidida

```
 [Sensor presencia / botón / animatrónico]
              │ (Zigbee/Z-Wave/ESPHome)
              ▼
     ┌──────────────────┐   MQTT (discovery, comandos, estados)
     │  Home Assistant  │◄───────────────────────────┐
     └──────────────────┘                            │
                                                     │
 ┌───────────────────────┐  WS LAN (PCM 16k +   ┌────┴─────────────────────┐   WSS (signed URL)   ┌───────────────────┐
 │ AtomS3R + Audio-3.5   │  JSON de control)    │ Puente "voice-bridge"    │◄────────────────────►│ ElevenLabs Agents │
 │ firmware ESP-IDF      │◄────────────────────►│ Python asyncio, LXC      │  user_audio_chunk /  │ ASR+LLM+TTS+turnos│
 │ I2S duplex, gating,   │                      │ sesiones, costos, HA,    │  audio / interruption│ agente "Danny"    │
 │ jitter buffer, LCD    │                      │ client tools, registros  │                      └───────────────────┘
 └──────────┬────────────┘                      └──────────────────────────┘
            │ jack 3.5 mm
            ▼
      Parlante activo
```

Separación pedida (hardware / transporte / servicio / personalidad / automatización):

- **Hardware de audio**: firmware del terminal (`firmware/terminal`), agnóstico de personaje.
- **Transporte**: protocolo terminal ⇄ puente v0 (abajo), documentado y versionado.
- **Servicio conversacional**: adaptador ElevenLabs dentro del puente (interfaz `ConversationBackend`; mañana podría ser otro proveedor o una cadena STT→LLM→TTS).
- **Personalidad**: en ElevenLabs (prompt, voz) + `personas/danny.yaml` en el puente (agent_id, saludos, despedidas, efectos permitidos, tiempos).
- **Automatización**: MQTT/HA, desacoplado.

### 3.1 Protocolo terminal ⇄ puente v0 (borrador)

- Conexión: `ws://<puente>:8765/v0/terminal?id=danny-01`, cabecera `Authorization: Bearer <token>`. El terminal permanece siempre conectado (barato en LAN); la sesión ElevenLabs sólo existe durante una interacción.
- Binario subida: PCM s16le 16 kHz mono, 20 ms por trama, sólo mientras `mic=on`.
- Binario bajada: PCM s16le 16 kHz mono, tamaño variable → jitter buffer en PSRAM (objetivo 120–200 ms, [A MEDIR]).
- Texto bajada (JSON): `{"t":"mic","on":true}`, `{"t":"flush"}` (interrupción: vaciar búfer), `{"t":"duplex","mode":"half"}`, `{"t":"volume","v":0.7}`, `{"t":"amp","on":false}`, `{"t":"ui","state":"listening"}`.
- Texto subida: `{"t":"hello","fw":"0.1.0","rate":16000,"proto":0}`, `{"t":"button","ev":"short"}`, `{"t":"playback","state":"idle"}`, `{"t":"stats","rssi":-58,"heap":…,"psram":…,"underruns":0,"overruns":0,"mic_rms":…}` cada 5 s.

¿Por qué WebSocket y no UDP/RTP? Orden y fiabilidad gratis, cliente oficial en IDF, mismo canal para control; en LAN la pérdida es baja y el jitter lo absorbe el búfer. Si Wi-Fi resultara problemático se reevalúa con datos.

### 3.2 Máquina de estados de sesión (en el puente)

`DISABLED → IDLE → CONNECTING → GREETING → CONVERSING ⇄ (LISTENING/SPEAKING) → FAREWELL → CLOSING → COOLDOWN → IDLE` · cualquier estado → `ERROR` → reintento con backoff.

- **Inicio**: botón HA, evento de presencia vía MQTT, botón físico del Atom. Rechazado si no está en `IDLE` (una sola sesión por terminal; límite global configurable).
- **Saludo**: `first_message` del agente con `{{saludo}}`; el puente elige al azar de un banco (~15 saludos) sin repetir los últimos N. Inmediato (sólo TTS, sin esperar al LLM).
- **Fin por inactividad** (sin `user_transcript` en X s) o **límite de sesión** (Y s): el puente pide una despedida (mensaje de contexto/texto al agente, mecanismo exacto [A MEDIR] en Hito 4), espera a que se reproduzca con un tope de ~8 s, y cierra el WebSocket. Si falla, cierra igualmente y reproduce una despedida pregrabada con la voz de Danny (banco de 5, generada una vez con TTS y guardada en el puente: determinista y sin costo por sesión).
- **Red de seguridad**: `max_duration_seconds` en el agente (rango 60–7200 s, defecto 600) ligeramente por encima del límite del puente; herramienta de sistema `end_call` habilitada para que Danny pueda terminar cuando la conversación concluye.
- **Cooldown** configurable antes de aceptar un nuevo inicio por presencia (los botones manuales pueden saltarlo).
- **Interruptor maestro** `switch.danny_enabled`: si está apagado nunca se abre sesión (control de costos/emergencias).

### 3.3 Entidades Home Assistant (propuestas, vía MQTT Discovery)

- `sensor.danny_status` (disabled/idle/connecting/listening/speaking/farewell/cooldown/error)
- `binary_sensor.danny_listening`, `binary_sensor.danny_speaking`
- `binary_sensor.danny_terminal_connected`, `binary_sensor.danny_bridge_online` (LWT MQTT)
- `button.danny_start_conversation`, `button.danny_end_conversation`, `button.danny_restart_bridge`
- `switch.danny_enabled`
- `number.danny_max_session_s`, `number.danny_idle_timeout_s`, `number.danny_cooldown_s`, `number.danny_volume`
- `sensor.danny_session_seconds`, `sensor.danny_minutes_today`, `sensor.danny_estimated_cost_today`, `sensor.danny_last_error`, `sensor.danny_wifi_rssi`
- Eventos MQTT para automatizaciones: `session_started`, `session_ended` (con duración y motivo), `effect_requested`.

### 3.4 Requisitos de configuración del agente en ElevenLabs (checklist)

- Idioma: sólo español; desactivar detección/cambio de idioma; voz Voice Design de Danny.
- Modelo TTS: probar el de menor latencia con español (familia Flash) contra uno de mayor calidad; elegir midiendo [A MEDIR — verificar qué modelos ofrece hoy la plataforma para agentes].
- Formatos: entrada `pcm_16000`, salida `pcm_16000`.
- Autenticación activada (signed URL). Overrides desactivados salvo necesidad.
- `first_message`: `{{saludo}}` (variable dinámica con valor por defecto).
- `max_duration_seconds`: p. ej. 300 (red de seguridad).
- `turn_timeout` (1–30 s): ~7 s para no presionar a los visitantes.
- Herramienta de sistema `end_call` habilitada.
- Prompt: reglas de §3 del brief (brevedad, no revelar secretos de Room 237/Sendero, no inventar datos operativos, adaptarse a miedo/incomodidad, abandonar el personaje en emergencia real) + base de conocimiento con datos operativos reales del evento.

---

## 4. Latencia y costos

### 4.1 Latencia (presupuesto estimado, todo [A MEDIR])

Fin de la frase del visitante → primer sonido de Danny:

| Tramo | Estimación |
|---|---|
| Captura + trama + Wi-Fi + LAN | 20–40 ms |
| Puente → ElevenLabs (RTT CR ⇄ EE. UU.) | 60–120 ms |
| Detección de fin de turno (ASR/VAD de ElevenLabs) | 300–800 ms |
| LLM primer token | 300–800 ms |
| TTS primer audio | 150–400 ms |
| Jitter buffer en el Atom | 120–200 ms |
| **Total esperado** | **~1,0–2,3 s** |

Cómo se medirá: el puente registra marcas de tiempo de `user_transcript`, `agent_response`, primer `audio`, `ping_ms`, y el Atom informa cuándo empieza a sonar. Comparar endpoints `wss://api.elevenlabs.io` y `wss://api.us.elevenlabs.io`.

### 4.2 Costos (verificar con el plan contratado)

- Referencia pública 2026: ~US$0,08 por minuto de conversación, LLM aparte; los silencios >10 s se cobran a ~5 % [fuentes secundarias + doc ElevenLabs; verificar en la página de precios].
- Ejemplo: 4 h de evento, una sesión cada ~4 min de 2 min ⇒ ~60 sesiones, ~120 min ⇒ **~US$10 + LLM**. El riesgo real no es el promedio sino una sesión que queda abierta: por eso límite en puente + límite en agente + interruptor maestro + cálculo de minutos del día en HA con automatización de alerta.

---

## 5. Riesgos principales y mitigación

| Riesgo | Prob. | Mitigación |
|---|---|---|
| Ruido del DJ/visitantes degrada el reconocimiento | Alta | Micrófono externo direccional cerca del visitante; ubicación lejos de bocinas del DJ; ganancia ES8311 ajustable; prueba con música real en Hito 6 |
| Eco/bucle de Danny consigo mismo | Media | E0 half-duplex en el Atom (diseñado para que sea imposible) |
| Jack 3.5 mm con ruido/zumbido o nivel inadecuado | Media | Medir en Hito 1; aislador de lazo de tierra; misma regleta para Atom y parlante; volumen digital moderado |
| Wi-Fi saturado por cientos de teléfonos en el evento | Media | SSID/VLAN dedicado en 5 GHz si el Atom lo soporta… **el ESP32-S3 sólo tiene 2,4 GHz** ⇒ canal 2,4 GHz limpio, AP cercano, medir RSSI/pérdidas |
| Calendario (≈3 semanas) | Alta | Hitos pequeños; contingencia con mini PC + USB usando el mismo puente |
| Despedida en sesión poco fiable | Media | Despedidas pregrabadas como respaldo |
| Componente ESPHome M5 en rama `main` cambia | Baja | Sólo se usa para pruebas; fijar commit si se usa |

---

## 6. Plan de pruebas

### 6.0 Antes de que llegue el hardware (se puede empezar ya)

1. **Spike ElevenLabs sin hardware** (Hito 2 adelantado): script Python que con el SDK oficial abre una sesión con signed URL, envía un WAV en español 16 kHz con una pregunta, guarda la respuesta en WAV, y registra latencias, formatos de `conversation_initiation_metadata` y `conversation_id`. Valida agente, voz, español y costos sin tocar el Atom.
2. Crear el LXC/VM Debian para el puente (Python 3.12+, usuario de servicio, `/etc/danny/`).
3. Confirmar que existe un broker MQTT (Mosquitto, ya usado por Zigbee2MQTT) y crear usuario `danny-bridge` con ACL.
4. Instalar ESP-IDF 6.1 en el Mac (o contenedor) y ESPHome 2026.9.x disponible en HA.
5. Configurar el agente según §3.4.

### 6.1 Hito 1 — Hardware (al llegar)

Cada prueba con criterio de aceptación explícito.

| # | Prueba | Cómo | Aceptación |
|---|---|---|---|
| 1.1 | Arranque + Wi-Fi + salida | Flashear YAML oficial ESPHome del media player (corrigiendo la clave duplicada) y enviar TTS/MP3 desde HA | Se oye por altavoz interno y por el jack; HA ve el media player |
| 1.2 | Bus I2C | Firmware IDF `hw_test`: escaneo en GPIO38/39 y GPIO45/0 | 0x18 (ES8311) y 0x43 (PI4IOE) en bus externo; 0x30 (LP5562) en interno |
| 1.3 | Tono por jack | `hw_test`: seno 1 kHz a −12 dBFS, 16 kHz | Sin distorsión audible ni zumbido con el parlante activo; anotar nivel útil de volumen |
| 1.4 | PW_AMP | Alternar P0 del PI4IOE con jack conectado | Saber si P0 silencia sólo el altavoz interno o también el jack |
| 1.5 | Micrófono | `hw_test`: grabar 5 s a PSRAM y reproducir; medidor RMS por serie; selector ON/OFF | Voz inteligible; RMS de reposo y de voz a 50 cm anotados |
| 1.6 | Micrófono → PC | `hw_test`: enviar PCM por WebSocket a un script en el Mac que guarda WAV | WAV limpio, sin saltos, 16 kHz exactos |
| 1.7 | Duplex | Reproducir tono continuo mientras se graba | Sin errores I2S; mide acoplamiento de eco (dB) altavoz interno vs parlante externo en 2–3 posiciones |
| 1.8 | Estabilidad | Duplex + WebSocket 60 min | 0 reinicios, heap estable, underruns/overruns = 0 o cerca, RSSI registrado |
| 1.9 | Micrófono externo | Si hay micrófono TRRS: repetir 1.5 con selector OFF | Comparar relación señal/ruido con música de fondo |

Entregable de código para Hito 1: `firmware/hw_test` (ESP-IDF 6.1, pequeño, con registros) + `tools/pcm_receiver.py`. Es la semilla del firmware del terminal; no se escribe nada de ElevenLabs en el Atom.

### 6.2 Hitos siguientes (resumen)

- **H2**: puente mínimo: terminal conecta, audio sube/baja, sesión ElevenLabs manual.
- **H3**: conversación real con Danny en E0 half-duplex; medir latencias.
- **H4**: máquina de estados completa, tiempos, despedidas, reconexión, registros de costos.
- **H5**: MQTT Discovery + automatizaciones (presencia → inicio, luces).
- **H6**: instalación física, posición de micrófono/parlante, prueba con música real, ajustes de ganancia; client tools de efectos.
- **H7**: systemd, watchdog del Atom, OTA, panel de diagnóstico, alertas de costos, ensayo de 4 h.

---

## 7. Alternativas consideradas y descartadas (por ahora)

- **Opción A directa**: ver §2.5. Se reconsiderará sólo si el puente demuestra latencia apreciable (no esperado).
- **Firmware xiaozhi sin modificar + puente que traduzca su protocolo (Opus) a ElevenLabs**: tentador porque ya soporta este pinout, pantalla, OTA y modo de escucha por turnos. Descartado como base porque: el dispositivo inicia las sesiones (no HA), base de código enorme (171 variantes), dependencia de su protocolo y servidor OTA, y aun así habría que modificarlo. Se usa como **referencia de código** (licencia a verificar antes de copiar fragmentos).
- **ESPHome + pipeline Assist de HA**: ver §2.2.
- **Cadena propia STT → LLM → TTS**: fuera de alcance por decisión del brief; la interfaz `ConversationBackend` del puente deja la puerta abierta.

---

## 8. Puntos a confirmar más adelante (no bloquean)

- Fecha y horario exactos del evento (asumido ~31 de octubre de 2026).
- Plan de ElevenLabs contratado (concurrencia, precio por minuto, modelos disponibles).
- Sensor de presencia a usar y su ubicación.
- Parlante activo concreto (entrada RCA/3.5 mm, alimentación).
- Si se compra micrófono externo TRRS (recomendado tenerlo a mano dado el ruido del evento).

---

## Fuentes

- [M5Stack — Atomic Audio-3.5 Base](https://docs.m5stack.com/en/atom/Atomic_Audio-3.5_Base)
- [M5Stack — Componente ESPHome Atomic Audio-3.5 Base](https://docs.m5stack.com/en/homeassistant/components/audio/atomic_audio_3.5_base)
- [M5Stack — Aplicación HA AtomS3R + Audio-3.5 Base](https://docs.m5stack.com/en/homeassistant/applications/audio/atoms3r_atomic_audio_3.5_base)
- [m5stack/esphome-yaml — atomic-audio3.5-base.yaml](https://github.com/m5stack/esphome-yaml/blob/main/examples/sensor/atomic-audio3.5-base.yaml)
- [m5stack/esphome-yaml — common/atoms3r-with-echo-base.yaml](https://github.com/m5stack/esphome-yaml/blob/main/common/atoms3r-with-echo-base.yaml)
- [m5stack/esphome-yaml — componente es8311](https://github.com/m5stack/esphome-yaml/tree/main/components/es8311)
- [m5stack/M5Atomic-EchoBase](https://github.com/m5stack/M5Atomic-EchoBase)
- [78/xiaozhi-esp32 — placa atoms3r-echo-base](https://github.com/78/xiaozhi-esp32/tree/main/main/boards/m5stack/atoms3r-echo-base) y [es8311_audio_codec.cc](https://github.com/78/xiaozhi-esp32/blob/main/main/audio/codecs/es8311_audio_codec.cc)
- [ElevenLabs — Agents WebSocket API](https://elevenlabs.io/docs/eleven-agents/api-reference/eleven-agents/websocket)
- [ElevenLabs — Guía WebSockets](https://elevenlabs.io/docs/eleven-agents/libraries/web-sockets)
- [ElevenLabs — Autenticación de agentes (signed URL)](https://elevenlabs.io/docs/eleven-agents/customization/authentication)
- [ElevenLabs — Overrides](https://elevenlabs.io/docs/eleven-agents/customization/personalization/overrides)
- [ElevenLabs — Conversation flow (max duration, turn timeout)](https://elevenlabs.io/docs/eleven-agents/customization/conversation-flow)
- [ElevenLabs — Burst pricing](https://elevenlabs.io/docs/eleven-agents/guides/burst-pricing.md)
- [elevenlabs/elevenlabs-python — conversation.py](https://github.com/elevenlabs/elevenlabs-python/blob/main/src/elevenlabs/conversational_ai/conversation.py)
- [ESPHome — I2S Audio Microphone](https://esphome.io/components/microphone/i2s_audio/)
- [akdeb/ElatoAI](https://github.com/akdeb/ElatoAI) · [Jitendra2603/elevenlabs-convai-cpp](https://github.com/Jitendra2603/elevenlabs-convai-cpp)
- Precios (secundarias, verificar): [getmacha.com](https://www.getmacha.com/blog/elevenlabs-agents-pricing-explained), [layer3labs.io](https://www.layer3labs.io/guides/elevenlabs-agents-review)
