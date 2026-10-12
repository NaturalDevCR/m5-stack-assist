"""Modo app de Home Assistant: configuración desde /data/options.json.

El formulario de la app (pestaña Configuración) escribe /data/options.json. Las
credenciales MQTT llegan por variables de entorno desde run.sh (servicio MQTT del
Supervisor o campos manuales del formulario).
"""

from __future__ import annotations

import json
import logging
import os
import secrets
from pathlib import Path

from .config import Config, ConfigError, slugify
from .state import StateStore

LOG = logging.getLogger(__name__)
DATA = Path(os.environ.get("BRIDGE_DATA_DIR", "/data"))
# Saludos incluidos en la imagen: personas/<prefijo del nombre>/saludos.txt (p. ej. danny).
PERSONAS_DIR = Path(os.environ.get("BRIDGE_PERSONAS_DIR", "/opt/voice-agent-bridge/personas"))
SESSION_KEYS = ("max_session_s", "idle_timeout_s", "cooldown_s")


def terminal_token(options: dict, data_dir: Path) -> str:
    """Token del formulario o, si está vacío, uno generado y guardado en /data."""
    token = (options.get("terminal_token") or "").strip()
    if token:
        return token
    path = data_dir / "terminal_token"
    if path.is_file():
        return path.read_text(encoding="utf-8").strip()
    token = secrets.token_hex(24)
    path.write_text(token, encoding="utf-8")
    path.chmod(0o600)
    LOG.warning("Token del terminal generado (guárdalo para el firmware del terminal): %s", token)
    return token


def build_config(options: dict, env: dict[str, str], data_dir: Path = DATA) -> Config:
    cfg = Config()
    cfg.name = (options.get("name") or "Asistente").strip()
    cfg.state_file = str(data_dir / "state.json")
    cfg.log_level = str(options.get("log_level", "info")).upper()

    el = cfg.elevenlabs
    el.api_key = (options.get("elevenlabs_api_key") or "").strip()
    el.agent_id = (options.get("agent_id") or "").strip()
    greetings = [g.strip() for g in options.get("greetings") or [] if g and g.strip()]
    if greetings:
        path = data_dir / "saludos.txt"
        path.write_text("\n".join(greetings) + "\n", encoding="utf-8")
        el.greetings_file = str(path)
    else:
        bundled = PERSONAS_DIR / slugify(cfg.name) / "saludos.txt"
        el.greetings_file = str(bundled) if bundled.is_file() else ""

    s = cfg.session
    for key in SESSION_KEYS:
        if key in options:
            setattr(s, key, float(options[key]))
    if options.get("farewell_phrase"):
        s.farewell_note = str(options["farewell_phrase"])

    cfg.terminal.type = "websocket"
    cfg.terminal.websocket.host = "0.0.0.0"
    cfg.terminal.websocket.port = 8765
    cfg.terminal.websocket.token = terminal_token(options, data_dir)

    m = cfg.mqtt
    m.host = env.get("MQTT_HOST", "")
    m.enabled = bool(m.host)
    if m.enabled:
        m.port = int(env.get("MQTT_PORT") or 1883)
        m.username = env.get("MQTT_USERNAME", "")
        m.password = env.get("MQTT_PASSWORD", "")
    return cfg.finalize()


def load_options(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ConfigError(f"no existe {path}") from exc
    except json.JSONDecodeError as exc:
        raise ConfigError(f"{path}: JSON inválido: {exc}") from exc


def sync_form_settings(cfg: Config, store: StateStore) -> None:
    """Si cambian los tiempos en el formulario, mandan sobre los ajustes hechos desde HA.

    Los números de HA (MQTT) guardan "overrides" que sobreviven reinicios; cuando el
    usuario cambia el formulario, esos overrides se descartan para que el valor nuevo
    del formulario tenga efecto.
    """
    current = {k: getattr(cfg.session, k) for k in SESSION_KEYS}
    if store.state.form_settings != current:
        if store.state.overrides:
            LOG.info("tiempos del formulario cambiaron: se descartan ajustes hechos desde HA")
        store.state.overrides = {}
        store.state.form_settings = current
        store.save()
