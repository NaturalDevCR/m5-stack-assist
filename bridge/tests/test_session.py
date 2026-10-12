import asyncio
import time

from voice_agent_bridge.audio import duration_s
from voice_agent_bridge.config import Config
from voice_agent_bridge.session import SessionManager, Status
from voice_agent_bridge.state import StateStore
from tests.fakes import FakeAgent, FakeTerminal, is_silent, loud, start_fake_agent


def make_cfg(tmp_path, ws_url: str, **session) -> Config:
    cfg = Config()
    cfg.state_file = str(tmp_path / "state.json")
    cfg.elevenlabs.ws_url = ws_url
    greetings = tmp_path / "saludos.txt"
    greetings.write_text("# comentario\nSaludo A\nSaludo B\n", encoding="utf-8")
    cfg.elevenlabs.greetings_file = str(greetings)
    s = cfg.session
    s.idle_timeout_s, s.max_session_s = 1.5, 20
    s.farewell_timeout_s, s.cooldown_s, s.error_retry_s = 2, 0.4, 0.2
    s.playback_tail_ms, s.input_chunk_ms = 100, 40
    for k, v in session.items():
        setattr(s, k, v)
    return cfg


async def wait_for(pred, timeout: float = 10.0) -> None:
    end = time.monotonic() + timeout
    while not pred():
        if time.monotonic() > end:
            raise AssertionError("tiempo de espera agotado")
        await asyncio.sleep(0.02)


def run(coro):
    return asyncio.run(asyncio.wait_for(coro, timeout=30))


def test_full_session_idle_farewell_and_cooldown(tmp_path):
    async def go():
        agent = FakeAgent()
        server, url = await start_fake_agent(agent)
        cfg = make_cfg(tmp_path, url)
        term = FakeTerminal()
        store = StateStore(cfg.state_file, cfg.timezone)
        mgr = SessionManager(cfg, term, store)
        events = []
        mgr.event_listeners.append(lambda e, d: events.append((e, d)))
        stop = asyncio.Event()
        try:
            assert await mgr.start("test")
            await wait_for(lambda: mgr.status in (Status.LISTENING, Status.SPEAKING))
            assert term.mic is True
            assert agent.init_messages[0]["dynamic_variables"]["saludo"] in ("Saludo A", "Saludo B")
            # el saludo suena primero; el visitante habla cuando termina
            assert mgr.status == Status.SPEAKING
            await wait_for(lambda: mgr.status == Status.LISTENING)
            feeder = asyncio.create_task(term.stream([("voice", 400)], stop))
            await wait_for(lambda: len(term.played) > duration_bytes(0.4) + duration_bytes(0.2))
            # silencio → despedida por inactividad → el agente cuelga → enfriamiento → idle
            await wait_for(lambda: mgr.status == Status.FAREWELL)
            await wait_for(lambda: mgr.status == Status.COOLDOWN)
            assert term.mic is False
            await wait_for(lambda: mgr.status == Status.IDLE)
        finally:
            stop.set()
            server.close()
        assert [e for e, _ in events] == ["session_started", "session_ended"]
        ended = events[-1][1]
        assert ended["reason"] == "idle"
        assert agent.user_messages == ["Bueno, muchas gracias, ya nos tenemos que ir."]
        assert store.state.today.sessions == 1 and store.state.today.seconds > 1
        snap = mgr.snapshot()
        assert snap["last_end_reason"] == "idle" and snap["sessions_today"] == 1
        await feeder
    run(go())


def duration_bytes(seconds: float) -> int:
    return int(seconds * 16000) * 2


def test_half_duplex_sends_silence_while_agent_speaks(tmp_path):
    async def go():
        agent = FakeAgent(greeting_ms=1000)
        server, url = await start_fake_agent(agent)
        cfg = make_cfg(tmp_path, url, idle_timeout_s=5)
        term = FakeTerminal()
        mgr = SessionManager(cfg, term, StateStore(cfg.state_file))
        stop = asyncio.Event()
        try:
            await mgr.start("test")
            await wait_for(lambda: mgr.status in (Status.LISTENING, Status.SPEAKING))
            # el visitante "habla" encima del saludo: no debe llegar al agente
            feeder = asyncio.create_task(term.stream([("voice", 600)], stop))
            await asyncio.sleep(0.5)
            assert mgr.snapshot()["speaking"] is True
            assert agent.received_chunks and all(is_silent(c) for c in agent.received_chunks)
            await mgr.end("test")
            await wait_for(lambda: mgr.status in (Status.COOLDOWN, Status.IDLE))
        finally:
            stop.set()
            server.close()
        await feeder
        assert not any(loud(c) for c in agent.received_chunks[:5])
    run(go())


def test_start_rules_disabled_cooldown_and_terminal(tmp_path):
    async def go():
        agent = FakeAgent(greeting_ms=100)
        server, url = await start_fake_agent(agent)
        cfg = make_cfg(tmp_path, url, cooldown_s=5)
        term = FakeTerminal()
        mgr = SessionManager(cfg, term, StateStore(cfg.state_file))
        try:
            await mgr.set_enabled(False)
            assert mgr.status == Status.DISABLED
            assert not await mgr.start("presence")
            await mgr.set_enabled(True)
            assert mgr.status == Status.IDLE
            assert await mgr.start("presence")
            assert not await mgr.start("presence"), "no dos sesiones a la vez"
            await wait_for(lambda: mgr.status in (Status.LISTENING, Status.SPEAKING))
            await mgr.end("manual")
            await wait_for(lambda: mgr.status == Status.COOLDOWN)
            assert not await mgr.start("presence"), "el enfriamiento bloquea inicios automáticos"
            assert await mgr.start("home_assistant", force=True), "el inicio manual lo ignora"
            await wait_for(lambda: mgr.status in (Status.LISTENING, Status.SPEAKING))
            term.disconnect()
            await wait_for(lambda: mgr.status == Status.IDLE)
            assert mgr.last_end_reason == "terminal_disconnected"
            assert not await mgr.start("presence"), "sin terminal no se abre sesión"
        finally:
            server.close()
        assert agent.connections == 2
    run(go())


def test_connection_error_goes_to_error_then_idle(tmp_path):
    async def go():
        cfg = make_cfg(tmp_path, "ws://127.0.0.1:1/x")
        term = FakeTerminal()
        mgr = SessionManager(cfg, term, StateStore(cfg.state_file))
        events = []
        mgr.event_listeners.append(lambda e, d: events.append(e))
        assert await mgr.start("test")
        await wait_for(lambda: mgr.status == Status.ERROR)
        assert mgr.last_error
        await wait_for(lambda: mgr.status == Status.IDLE)
        assert events == ["error"]
    run(go())


def test_settings_are_clamped_and_persisted(tmp_path):
    async def go():
        cfg = make_cfg(tmp_path, "ws://unused")
        store = StateStore(cfg.state_file)
        mgr = SessionManager(cfg, FakeTerminal(), store)
        mgr.set_setting("idle_timeout_s", 9999)
        assert mgr.setting("idle_timeout_s") == 120
        again = StateStore(cfg.state_file)
        assert again.state.overrides["idle_timeout_s"] == 120
    run(go())


def test_greetings_rotate_without_immediate_repeats(tmp_path):
    async def go():
        cfg = make_cfg(tmp_path, "ws://unused")
        mgr = SessionManager(cfg, FakeTerminal(), StateStore(cfg.state_file))
        picks = [mgr._pick_greeting() for _ in range(6)]
        assert all(a != b for a, b in zip(picks, picks[1:]))
    run(go())


def test_empty_transcripts_do_not_count_as_activity(tmp_path):
    async def go():
        cfg = make_cfg(tmp_path, "ws://unused")
        mgr = SessionManager(cfg, FakeTerminal(), StateStore(cfg.state_file))
        mgr._last_user = 1.0
        mgr.on_user_transcript("...")
        assert mgr._last_user == 1.0
        mgr.on_user_transcript("¿Hay fantasmas?")
        assert mgr._last_user > 1.0


def test_idle_counts_from_end_of_reply_but_not_from_nags(tmp_path):
    async def go():
        cfg = make_cfg(tmp_path, "ws://unused")
        mgr = SessionManager(cfg, FakeTerminal(), StateStore(cfg.state_file))
        mgr._idle_base = 0.0
        mgr.on_user_transcript("¿Hay fantasmas?")
        mgr.on_agent_audio(bytes(32000))          # respuesta de 1 s
        reply_end = mgr._idle_base
        assert reply_end >= mgr._last_user + 0.99
        mgr.on_user_transcript("...")             # silencio → "¿siguen ahí?"
        mgr.on_agent_audio(bytes(32000))
        assert mgr._idle_base == reply_end, "el recordatorio no reinicia la inactividad"
    run(go())
