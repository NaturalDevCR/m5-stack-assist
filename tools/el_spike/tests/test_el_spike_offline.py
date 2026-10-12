"""Prueba de extremo a extremo contra un servidor simulado (sin red ni credenciales)."""

import asyncio
import json
import struct

import audio_utils as au
import el_spike
from tests.fake_agent_server import FakeAgent, start, tone


def make_question(tmp_path, name="q.wav"):
    pcm = tone(400, freq=300) + bytes(16000 * 2 // 2)  # 0,4 s voz + 0,5 s silencio
    path = tmp_path / name
    au.write_wav_pcm16_mono(path, pcm, 16000)
    return path


def run_spike(tmp_path, agent: FakeAgent, extra=()):
    async def go():
        server, url = await start(agent)
        try:
            args = el_spike.parse_args([
                "--ws-url", url, "--out", str(tmp_path / "out"),
                "--chunk-ms", "20", "--idle-s", "0.3", "--gap-s", "0.1",
                "--greeting-timeout-s", "2", "--response-timeout-s", "3",
                "--max-seconds", "15", *extra,
            ])
            return await el_spike.run(args)
        finally:
            server.close()
            await server.wait_closed()
    return asyncio.run(go())


def load_summary(tmp_path):
    (session_dir,) = (tmp_path / "out").iterdir()
    return session_dir, json.loads((session_dir / "summary.json").read_text())


def test_full_conversation(tmp_path):
    q1 = make_question(tmp_path, "q1.wav")
    q2 = make_question(tmp_path, "q2.wav")
    agent = FakeAgent()
    rc = run_spike(tmp_path, agent, ["--wav", str(q1), "--wav", str(q2),
                                     "--var", "saludo=Hola, huéspedes."])
    session_dir, s = load_summary(tmp_path)

    assert rc == 0, s
    assert s["conversation_id"] == "conv_fake_123"
    assert agent.init_message["type"] == "conversation_initiation_client_data"
    assert agent.init_message["dynamic_variables"] == {"saludo": "Hola, huéspedes."}
    assert agent.pongs == [1], "debe responder pong con el mismo event_id"
    assert s["greeting_text"] == "Hola, huéspedes."
    assert len(s["turns"]) == 2
    for t in s["turns"]:
        assert t["user_transcript"] == "¿hay fantasmas?"
        lat = t["latency_from_speech_end"]
        assert lat["first_audio_ms"] is not None and lat["first_audio_ms"] >= 0
        assert t["response_audio_s"] > 0.2
    assert (session_dir / "danny.wav").stat().st_size > 44
    # el audio nunca se registra en events.jsonl
    events = (session_dir / "events.jsonl").read_text()
    assert "audio_base_64" not in events and '"audio_bytes"' in events
    assert s["close"]["by_server"] is False


def test_rejects_wrong_input_format(tmp_path):
    q1 = make_question(tmp_path)
    rc = run_spike(tmp_path, FakeAgent(input_format="pcm_24000"), ["--wav", str(q1)])
    _, s = load_summary(tmp_path)
    assert rc == 1
    assert any("pcm_24000" in e for e in s["errors"])


def test_redact_hides_token():
    url = "wss://api.elevenlabs.io/v1/convai/conversation?agent_id=abc&conversation_signature=SECRET"
    red = el_spike.redact(url)
    assert "SECRET" not in red and "agent_id=abc" in red


def test_silence_source_chunks():
    async def go():
        src = el_spike.RealtimeSource(16000, 20)
        assert src.next_chunk(0.0) == bytes(640)
        started, done = src.play(struct.pack("<h", 1000) * 400)  # 400 muestras
        a = src.next_chunk(1.0)
        b = src.next_chunk(1.02)
        assert started.result() == 1.0 and done.result() == 1.02
        assert len(a) == len(b) == 640 and b[160:] == bytes(480)
        assert src.next_chunk(1.04) == bytes(640)
    asyncio.run(go())


def test_wait_respects_emulated_playback():
    """No se debe hablar antes de que termine de 'sonar' el audio recibido."""
    async def go():
        sess = el_spike.Session()
        stop = asyncio.Event()
        now = el_spike.time.monotonic()
        sess.last_audio_t = now          # llegó todo de golpe...
        sess.play_end = now + 0.6        # ...pero dura 0,6 s en tiempo real
        t0 = el_spike.time.monotonic()
        ok = await el_spike.wait_agent_quiet(sess, now - 1, 0.1, 5, stop)
        return ok, el_spike.time.monotonic() - t0
    ok, waited = asyncio.run(go())
    assert ok and waited >= 0.65
