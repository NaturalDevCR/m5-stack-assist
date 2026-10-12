"""Configuración: YAML (no secreto) + variables de entorno (secretos).

Secretos (nunca en el YAML ni en el repositorio):
  ELEVENLABS_API_KEY     clave con permiso ElevenAgents: Escribir
  TERMINAL_TOKEN   token que deben presentar los terminales WebSocket
  MQTT_PASSWORD          contraseña del usuario MQTT
"""

from __future__ import annotations

import os
import re
import unicodedata
from dataclasses import MISSING, dataclass, field, fields, is_dataclass
from pathlib import Path
from typing import Any

import yaml


@dataclass
class ElevenLabsConfig:
    agent_id: str = ""
    base_url: str = "https://api.elevenlabs.io"
    greetings_file: str = ""
    cost_per_min_usd: float = 0.08
    # Sólo para pruebas: URL WebSocket directa (sin signed URL).
    ws_url: str = ""
    api_key: str = field(default="", repr=False)


@dataclass
class SessionConfig:
    max_session_s: float = 240.0
    idle_timeout_s: float = 20.0
    farewell_timeout_s: float = 12.0
    cooldown_s: float = 30.0
    playback_tail_ms: int = 300
    input_chunk_ms: int = 100
    error_retry_s: float = 5.0
    # Se envía como mensaje del visitante para provocar la despedida + end_call.
    # Una "nota del sistema" activa la protección anti prompt-injection del agente
    # y ElevenLabs corta la llamada (1008) sin despedida.
    farewell_note: str = "Bueno, muchas gracias, ya nos tenemos que ir."


@dataclass
class WebSocketTerminalConfig:
    host: str = "0.0.0.0"
    port: int = 8765
    token: str = field(default="", repr=False)


@dataclass
class LocalTerminalConfig:
    input_device: str | int | None = None
    output_device: str | int | None = None


@dataclass
class TerminalConfig:
    type: str = "websocket"  # websocket | local
    websocket: WebSocketTerminalConfig = field(default_factory=WebSocketTerminalConfig)
    local: LocalTerminalConfig = field(default_factory=LocalTerminalConfig)


@dataclass
class MqttConfig:
    enabled: bool = False
    host: str = "localhost"
    port: int = 1883
    username: str = ""
    password: str = field(default="", repr=False)
    discovery_prefix: str = "homeassistant"
    # Vacíos = derivados del nombre del agente (ver Config.finalize):
    #   device_name "Danny" → entity_prefix "danny", base_topic "voice_agent/danny",
    #   device_id "voice_agent_danny"
    device_name: str = ""
    entity_prefix: str = ""
    base_topic: str = ""
    device_id: str = ""


def slugify(text: str) -> str:
    """'Recepción Norte' → 'recepcion_norte' (para entity_id y topics)."""
    ascii_text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    slug = re.sub(r"[^a-z0-9]+", "_", ascii_text.lower()).strip("_")
    return slug or "agente"


@dataclass
class Config:
    # Nombre del agente/personaje: dispositivo en HA, prefijo de entidades y registros.
    name: str = "Asistente"
    timezone: str = "America/Costa_Rica"
    state_file: str = "/var/lib/voice-agent-bridge/state.json"
    log_level: str = "INFO"
    elevenlabs: ElevenLabsConfig = field(default_factory=ElevenLabsConfig)
    session: SessionConfig = field(default_factory=SessionConfig)
    terminal: TerminalConfig = field(default_factory=TerminalConfig)
    mqtt: MqttConfig = field(default_factory=MqttConfig)

    def finalize(self) -> "Config":
        """Completa los valores derivados del nombre del agente."""
        slug = slugify(self.name)
        m = self.mqtt
        m.device_name = m.device_name or self.name
        m.entity_prefix = m.entity_prefix or slug
        m.base_topic = m.base_topic or f"voice_agent/{slug}"
        m.device_id = m.device_id or f"voice_agent_{slug}"
        return self


class ConfigError(ValueError):
    pass


def _build(cls, data: dict[str, Any], path: str):
    if data is None:
        data = {}
    if not isinstance(data, dict):
        raise ConfigError(f"{path}: se esperaba un mapa")
    known = {f.name: f for f in fields(cls)}
    unknown = set(data) - set(known)
    if unknown:
        raise ConfigError(f"{path}: claves desconocidas {sorted(unknown)}")
    kwargs = {}
    for name, f in known.items():
        if name not in data:
            continue
        value = data[name]
        default = f.default_factory() if f.default_factory is not MISSING else f.default
        if is_dataclass(default):
            kwargs[name] = _build(type(default), value, f"{path}.{name}")
        else:
            kwargs[name] = value
    return cls(**kwargs)



def load_dotenv(path: Path) -> None:
    """KEY=VALUE sin pisar variables ya definidas (systemd usa EnvironmentFile)."""
    if not path.is_file():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def load_config(path: str | Path, env: dict[str, str] | None = None) -> Config:
    env = os.environ if env is None else env
    path = Path(path)
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except FileNotFoundError as exc:
        raise ConfigError(f"no existe el archivo de configuración {path}") from exc
    except yaml.YAMLError as exc:
        raise ConfigError(f"{path}: YAML inválido: {exc}") from exc
    cfg = _build(Config, data, "config")
    cfg.elevenlabs.api_key = env.get("ELEVENLABS_API_KEY", "")
    cfg.terminal.websocket.token = env.get("TERMINAL_TOKEN", "")
    cfg.mqtt.password = env.get("MQTT_PASSWORD", "")
    return cfg.finalize()


def validate(cfg: Config) -> list[str]:
    """Problemas que impiden arrancar (lista vacía = OK)."""
    problems = []
    el = cfg.elevenlabs
    if not el.ws_url:
        if not el.agent_id:
            problems.append("elevenlabs.agent_id vacío")
        if not el.api_key:
            problems.append("falta ELEVENLABS_API_KEY en el entorno")
    if cfg.terminal.type not in ("websocket", "local"):
        problems.append("terminal.type debe ser 'websocket' o 'local'")
    if cfg.terminal.type == "websocket" and not cfg.terminal.websocket.token:
        problems.append("falta TERMINAL_TOKEN en el entorno")
    s = cfg.session
    if s.idle_timeout_s <= 0 or s.max_session_s <= s.idle_timeout_s:
        problems.append("session: se requiere 0 < idle_timeout_s < max_session_s")
    if cfg.mqtt.enabled and not cfg.mqtt.host:
        problems.append("mqtt.host vacío")
    return problems
