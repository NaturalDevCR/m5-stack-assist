"""Home Assistant vía MQTT Discovery.

Entidades (un dispositivo "Danny"):
  sensor.danny_estado, binary_sensor.danny_hablando / _escuchando / _terminal,
  button.danny_iniciar_conversacion / _visitante_detectado / _terminar_conversacion,
  switch.danny_activo,
  number.danny_duracion_maxima / _inactividad / _enfriamiento,
  sensores de estadísticas y event.danny_sesion (session_started, session_ended, error).

Topics (base_topic = "danny"):
  danny/availability          online | offline (LWT)
  danny/state                 JSON con el snapshot (retenido)
  danny/event                 JSON {"event_type": ..., ...}
  danny/cmd/start             iniciar ya (manual; ignora el enfriamiento)
  danny/cmd/trigger           visitante detectado (automatizaciones; respeta el enfriamiento)
  danny/cmd/end               pedir despedida y cerrar
  danny/cmd/enabled           ON | OFF
  danny/cmd/set/<ajuste>      número
"""

from __future__ import annotations

import asyncio
import json
import logging

import aiomqtt

from . import __version__
from .config import MqttConfig
from .session import SETTING_KEYS, SETTING_LIMITS, SessionManager

LOG = logging.getLogger(__name__)

SETTING_NAMES = {
    "max_session_s": "Duración máxima de conversación",
    "idle_timeout_s": "Tiempo de inactividad",
    "cooldown_s": "Enfriamiento entre grupos",
}


def discovery_messages(cfg: MqttConfig) -> list[tuple[str, dict]]:
    """(topic, payload) de MQTT Discovery. Función pura para poder probarla."""
    b, dp, dev_id = cfg.base_topic, cfg.discovery_prefix, cfg.device_id
    device = {"identifiers": [dev_id], "name": cfg.device_name,
              "manufacturer": "Room 237", "model": "Voice bridge", "sw_version": __version__}
    common = {"device": device, "availability_topic": f"{b}/availability"}
    state = f"{b}/state"

    def ent(component: str, key: str, payload: dict) -> tuple[str, dict]:
        uid = f"{dev_id}_{key}"
        return (f"{dp}/{component}/{dev_id}/{key}/config",
                {"unique_id": uid, "default_entity_id": f"{component}.danny_{key}",
                 **common, **payload})

    msgs = [
        ent("sensor", "estado", {"name": "Estado", "state_topic": state,
                                 "value_template": "{{ value_json.status }}",
                                 "icon": "mdi:account-voice"}),
        ent("binary_sensor", "hablando", {"name": "Hablando", "state_topic": state,
            "value_template": "{{ 'ON' if value_json.speaking else 'OFF' }}",
            "icon": "mdi:account-voice"}),
        ent("binary_sensor", "escuchando", {"name": "Escuchando", "state_topic": state,
            "value_template": "{{ 'ON' if value_json.listening else 'OFF' }}",
            "icon": "mdi:ear-hearing"}),
        ent("binary_sensor", "terminal", {"name": "Terminal conectado", "state_topic": state,
            "device_class": "connectivity",
            "value_template": "{{ 'ON' if value_json.terminal_connected else 'OFF' }}"}),
        ent("button", "iniciar_conversacion", {"name": "Iniciar conversación",
            "command_topic": f"{b}/cmd/start", "icon": "mdi:play"}),
        ent("button", "visitante_detectado", {"name": "Visitante detectado",
            "command_topic": f"{b}/cmd/trigger", "icon": "mdi:motion-sensor"}),
        ent("button", "terminar_conversacion", {"name": "Terminar conversación",
            "command_topic": f"{b}/cmd/end", "icon": "mdi:stop"}),
        ent("switch", "activo", {"name": "Activo", "state_topic": state,
            "value_template": "{{ 'ON' if value_json.enabled else 'OFF' }}",
            "command_topic": f"{b}/cmd/enabled", "icon": "mdi:power"}),
        ent("sensor", "segundos_sesion", {"name": "Duración sesión actual", "state_topic": state,
            "value_template": "{{ value_json.session_seconds }}", "unit_of_measurement": "s",
            "device_class": "duration", "state_class": "measurement"}),
        ent("sensor", "ultima_sesion", {"name": "Duración última sesión", "state_topic": state,
            "value_template": "{{ value_json.last_session_seconds }}", "unit_of_measurement": "s",
            "device_class": "duration"}),
        ent("sensor", "motivo_fin", {"name": "Motivo del último cierre", "state_topic": state,
            "value_template": "{{ value_json.last_end_reason }}", "entity_category": "diagnostic"}),
        ent("sensor", "ultimo_error", {"name": "Último error", "state_topic": state,
            "value_template": "{{ value_json.last_error }}", "entity_category": "diagnostic"}),
        ent("sensor", "sesiones_hoy", {"name": "Sesiones hoy", "state_topic": state,
            "value_template": "{{ value_json.sessions_today }}", "state_class": "total"}),
        ent("sensor", "minutos_hoy", {"name": "Minutos hoy", "state_topic": state,
            "value_template": "{{ value_json.minutes_today }}", "unit_of_measurement": "min",
            "state_class": "total"}),
        ent("sensor", "costo_hoy", {"name": "Costo estimado hoy", "state_topic": state,
            "value_template": "{{ value_json.cost_today_usd }}", "unit_of_measurement": "USD",
            "device_class": "monetary", "state_class": "total"}),
        ent("event", "sesion", {"name": "Sesión", "state_topic": f"{b}/event",
            "event_types": ["session_started", "session_ended", "error"]}),
    ]
    for key in SETTING_KEYS:
        lo, hi = SETTING_LIMITS[key]
        msgs.append(ent("number", key, {
            "name": SETTING_NAMES[key], "state_topic": state,
            "value_template": f"{{{{ value_json.{key} }}}}",
            "command_topic": f"{b}/cmd/set/{key}", "min": lo, "max": hi, "step": 5,
            "unit_of_measurement": "s", "mode": "box", "entity_category": "config"}))
    return msgs


class HomeAssistantMqtt:
    def __init__(self, cfg: MqttConfig, manager: SessionManager):
        self.cfg = cfg
        self.manager = manager
        self._client: aiomqtt.Client | None = None
        self._dirty = asyncio.Event()
        self._events: asyncio.Queue[tuple[str, dict]] = asyncio.Queue(maxsize=100)
        manager.listeners.append(lambda snap: self._dirty.set())
        manager.event_listeners.append(self._queue_event)

    def _queue_event(self, event: str, data: dict) -> None:
        if not self._events.full():
            self._events.put_nowait((event, data))

    async def run(self) -> None:
        b = self.cfg.base_topic
        backoff = 1
        while True:
            try:
                async with aiomqtt.Client(
                    hostname=self.cfg.host, port=self.cfg.port,
                    username=self.cfg.username or None, password=self.cfg.password or None,
                    identifier=f"{self.cfg.device_id}-bridge",
                    will=aiomqtt.Will(f"{b}/availability", "offline", qos=1, retain=True),
                ) as client:
                    self._client = client
                    backoff = 1
                    LOG.info("MQTT conectado a %s:%s", self.cfg.host, self.cfg.port)
                    for topic, payload in discovery_messages(self.cfg):
                        await client.publish(topic, json.dumps(payload), qos=1, retain=True)
                    await client.publish(f"{b}/availability", "online", qos=1, retain=True)
                    await client.subscribe(f"{b}/cmd/#", qos=1)
                    self._dirty.set()
                    try:
                        await asyncio.gather(self._publisher(client), self._commands(client))
                    except asyncio.CancelledError:
                        # apagado ordenado: el LWT sólo cubre caídas
                        await client.publish(f"{b}/availability", "offline", qos=1, retain=True)
                        raise
            except aiomqtt.MqttError as exc:
                self._client = None
                LOG.warning("MQTT desconectado (%s); reintento en %ss", exc, backoff)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 60)

    async def _publisher(self, client: aiomqtt.Client) -> None:
        b = self.cfg.base_topic
        while True:
            try:
                await asyncio.wait_for(self._dirty.wait(), timeout=5)
            except TimeoutError:
                pass  # refresco periódico (duración de sesión en vivo)
            self._dirty.clear()
            await client.publish(f"{b}/state", json.dumps(self.manager.snapshot()), qos=0,
                                 retain=True)
            while not self._events.empty():
                event, data = self._events.get_nowait()
                await client.publish(f"{b}/event", json.dumps({"event_type": event, **data}),
                                     qos=1)

    async def _commands(self, client: aiomqtt.Client) -> None:
        b = self.cfg.base_topic
        async for message in client.messages:
            topic = str(message.topic)
            payload = message.payload.decode() if isinstance(message.payload, bytes) else str(message.payload)
            try:
                await self.handle_command(topic.removeprefix(f"{b}/cmd/"), payload)
            except Exception:  # noqa: BLE001
                LOG.exception("comando MQTT inválido: %s %r", topic, payload)

    async def handle_command(self, command: str, payload: str) -> None:
        m = self.manager
        if command == "start":
            await m.start("home_assistant", force=True)
        elif command == "trigger":
            await m.start("presence")
        elif command == "end":
            await m.end("home_assistant")
        elif command == "enabled":
            await m.set_enabled(payload.strip().upper() in ("ON", "1", "TRUE"))
        elif command.startswith("set/"):
            m.set_setting(command.removeprefix("set/"), float(payload))
        else:
            LOG.warning("comando MQTT desconocido: %s", command)
