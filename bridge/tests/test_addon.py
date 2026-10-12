from pathlib import Path

from danny_bridge.addon import build_config, sync_form_settings
from danny_bridge.config import validate
from danny_bridge.state import StateStore

OPTIONS = {
    "elevenlabs_api_key": "clave",
    "agent_id": "agent_x",
    "terminal_token": "",
    "max_session_s": 200,
    "idle_timeout_s": 15,
    "cooldown_s": 20,
    "farewell_phrase": "Gracias, ya nos vamos.",
    "greetings": [],
    "log_level": "info",
}


def test_build_config_from_options_and_mqtt_service(tmp_path: Path):
    env = {"MQTT_HOST": "core-mosquitto", "MQTT_PORT": "1883",
           "MQTT_USERNAME": "addons", "MQTT_PASSWORD": "pw"}
    cfg = build_config(OPTIONS, env, tmp_path)
    assert validate(cfg) == []
    assert cfg.elevenlabs.api_key == "clave" and cfg.elevenlabs.agent_id == "agent_x"
    assert cfg.session.idle_timeout_s == 15 and cfg.session.farewell_note == "Gracias, ya nos vamos."
    assert cfg.terminal.type == "websocket" and cfg.terminal.websocket.port == 8765
    assert cfg.mqtt.enabled and cfg.mqtt.host == "core-mosquitto" and cfg.mqtt.password == "pw"
    assert cfg.state_file == str(tmp_path / "state.json")


def test_terminal_token_is_generated_once_and_persisted(tmp_path: Path):
    a = build_config(OPTIONS, {}, tmp_path).terminal.websocket.token
    b = build_config(OPTIONS, {}, tmp_path).terminal.websocket.token
    assert len(a) == 48 and a == b
    assert (tmp_path / "terminal_token").stat().st_mode & 0o077 == 0
    explicit = build_config({**OPTIONS, "terminal_token": "mio"}, {}, tmp_path)
    assert explicit.terminal.websocket.token == "mio"


def test_without_mqtt_service_mqtt_is_disabled(tmp_path: Path):
    assert build_config(OPTIONS, {}, tmp_path).mqtt.enabled is False


def test_greetings_from_form_override_bundled_file(tmp_path: Path):
    cfg = build_config({**OPTIONS, "greetings": ["Hola, huéspedes.", "  ", "Bienvenidos."]},
                       {}, tmp_path)
    lines = Path(cfg.elevenlabs.greetings_file).read_text(encoding="utf-8").splitlines()
    assert lines == ["Hola, huéspedes.", "Bienvenidos."]


def test_missing_api_key_is_reported(tmp_path: Path):
    cfg = build_config({**OPTIONS, "elevenlabs_api_key": ""}, {}, tmp_path)
    assert any("ELEVENLABS_API_KEY" in p for p in validate(cfg))


def test_form_change_discards_overrides_from_ha(tmp_path: Path):
    cfg = build_config(OPTIONS, {}, tmp_path)
    store = StateStore(cfg.state_file)
    sync_form_settings(cfg, store)
    store.state.overrides["cooldown_s"] = 99          # cambiado desde HA
    store.save()
    sync_form_settings(cfg, StateStore(cfg.state_file))  # reinicio sin cambios en el formulario
    assert StateStore(cfg.state_file).state.overrides == {"cooldown_s": 99}
    cfg2 = build_config({**OPTIONS, "cooldown_s": 40}, {}, tmp_path)
    store2 = StateStore(cfg2.state_file)
    sync_form_settings(cfg2, store2)                   # el usuario cambió el formulario
    assert store2.state.overrides == {}
