import asyncio
import json
from pathlib import Path

import pytest
from websockets.asyncio.client import connect
from websockets.exceptions import InvalidStatus

from danny_bridge.audio import FrameAggregator, chunk_bytes
from danny_bridge.config import (Config, ConfigError, MqttConfig, WebSocketTerminalConfig,
                                 load_config, validate)
from danny_bridge.ha_mqtt import HomeAssistantMqtt, discovery_messages
from danny_bridge.session import SessionManager
from danny_bridge.state import StateStore
from danny_bridge.terminals.websocket import WebSocketTerminalServer
from tests.fakes import FakeTerminal

ROOT = Path(__file__).resolve().parents[1]


def test_frame_aggregator():
    agg = FrameAggregator(chunk_bytes(40))  # 1280 bytes
    assert agg.push(bytes(640)) == []
    out = agg.push(bytes(1000))
    assert [len(c) for c in out] == [1280]
    assert agg.push(bytes(920)) == [bytes(1280)]


def test_example_config_loads_with_secrets_from_env():
    env = {"ELEVENLABS_API_KEY": "k", "DANNY_TERMINAL_TOKEN": "t", "MQTT_PASSWORD": "p"}
    cfg = load_config(ROOT / "config.example.yaml", env=env)
    assert cfg.elevenlabs.agent_id.startswith("agent_")
    assert cfg.elevenlabs.api_key == "k" and cfg.mqtt.password == "p"
    assert validate(cfg) == []
    assert "k" not in repr(cfg.elevenlabs), "el secreto no debe aparecer en logs"


def test_config_rejects_unknown_keys_and_missing_secrets(tmp_path):
    bad = tmp_path / "c.yaml"
    bad.write_text("session:\n  idle_timeout: 5\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="idle_timeout"):
        load_config(bad, env={})
    ok = tmp_path / "ok.yaml"
    ok.write_text("elevenlabs:\n  agent_id: a\n", encoding="utf-8")
    problems = validate(load_config(ok, env={}))
    assert any("ELEVENLABS_API_KEY" in p for p in problems)
    assert any("DANNY_TERMINAL_TOKEN" in p for p in problems)


def test_state_store_rolls_day(tmp_path):
    store = StateStore(tmp_path / "s.json")
    store.add_session(60, 0.08)
    store.state.today.date = "2000-01-01"
    store.roll_day()
    assert store.state.today.sessions == 0
    assert StateStore(tmp_path / "s.json").state.today.date == store.today_str()


def test_discovery_payloads():
    msgs = discovery_messages(MqttConfig())
    uids = [p["unique_id"] for _, p in msgs]
    assert len(uids) == len(set(uids))
    for topic, payload in msgs:
        component = topic.split("/")[1]
        assert topic.startswith("homeassistant/") and topic.endswith("/config")
        assert payload["default_entity_id"].startswith(f"{component}.danny_")
        assert "object_id" not in payload, "object_id fue retirado en HA 2026.4"
        assert payload["availability_topic"] == "danny/availability"
    ids = {p["default_entity_id"] for _, p in msgs}
    for expected in ("sensor.danny_estado", "binary_sensor.danny_hablando",
                     "button.danny_iniciar_conversacion", "button.danny_visitante_detectado",
                     "switch.danny_activo", "event.danny_sesion", "number.danny_cooldown_s"):
        assert expected in ids


def test_mqtt_commands_drive_session_manager(tmp_path):
    async def go():
        cfg = Config()
        cfg.state_file = str(tmp_path / "s.json")
        mgr = SessionManager(cfg, FakeTerminal(), StateStore(cfg.state_file))
        calls = []

        async def fake_start(source, force=False):
            calls.append((source, force))
            return True

        mgr.start = fake_start
        ha = HomeAssistantMqtt(cfg.mqtt, mgr)
        await ha.handle_command("start", "PRESS")
        await ha.handle_command("trigger", "PRESS")
        await ha.handle_command("enabled", "OFF")
        assert mgr.enabled is False
        await ha.handle_command("set/cooldown_s", "45")
        assert mgr.setting("cooldown_s") == 45
        assert calls == [("home_assistant", True), ("presence", False)]
    asyncio.run(go())


def test_websocket_terminal_auth_frames_and_button():
    async def go():
        term = WebSocketTerminalServer(WebSocketTerminalConfig(host="127.0.0.1", port=0,
                                                               token="secreto"))
        frames, buttons, conns = [], [], []
        term.on_frame = frames.append
        term.on_button = buttons.append
        term.on_connection = conns.append
        await term.start()
        base = f"ws://127.0.0.1:{term.port}/v0/terminal?id=atom-test"
        try:
            with pytest.raises(InvalidStatus):
                async with connect(base):
                    pass
            async with connect(base, additional_headers={"Authorization": "Bearer secreto"}) as ws:
                hello = [json.loads(await ws.recv()) for _ in range(2)]
                assert {"t": "mic", "on": False} in hello
                await ws.send(bytes(640))           # mic apagado: se ignora
                await asyncio.sleep(0.1)
                await term.set_mic(True)
                assert json.loads(await ws.recv()) == {"t": "mic", "on": True}
                await ws.send(b"\x01\x00" * 320)
                await ws.send(json.dumps({"t": "button", "ev": "short"}))
                await term.play(b"\x02\x00" * 10)
                assert await ws.recv() == b"\x02\x00" * 10
                await asyncio.sleep(0.1)
                assert term.connected
        finally:
            await term.stop()
        assert frames == [b"\x01\x00" * 320]
        assert buttons == ["short"]
        assert conns == [True, False]
    asyncio.run(go())
