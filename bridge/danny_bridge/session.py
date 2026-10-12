"""Gestor de sesiones: una conversación con ElevenLabs por grupo de visitantes.

Estados: disabled · idle · connecting · listening · speaking · farewell · cooldown · error

Reglas (docs/01 §3.2):
- Sólo una sesión a la vez; inicio por HA, botón del terminal o automatización.
- Saludo variado: variable dinámica {{saludo}} elegida del banco de saludos.
- Half-duplex (E0): mientras Danny "suena" (+ cola) se envía silencio en lugar del
  micrófono, para que nunca se escuche a sí mismo.
- Fin por inactividad o por duración máxima: se pide al agente que se despida
  (nota del sistema → despedida + end_call); si no cierra, cierra el puente.
- Enfriamiento antes de aceptar un nuevo inicio automático.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import random
import time
from collections import deque
from collections.abc import Callable
from enum import Enum
from pathlib import Path

from . import audio
from .config import Config
from .elevenlabs import AgentError, AgentSession
from .state import StateStore
from .terminals import Terminal

LOG = logging.getLogger(__name__)

SETTING_KEYS = ("max_session_s", "idle_timeout_s", "cooldown_s")
SETTING_LIMITS = {"max_session_s": (60, 900), "idle_timeout_s": (5, 120), "cooldown_s": (0, 600)}
DEFAULT_GREETING = "Bienvenidos al Hotel Overlook. ¿Es su primera visita?"


class Status(str, Enum):
    DISABLED = "disabled"
    IDLE = "idle"
    CONNECTING = "connecting"
    LISTENING = "listening"
    SPEAKING = "speaking"
    FAREWELL = "farewell"
    COOLDOWN = "cooldown"
    ERROR = "error"


ACTIVE = {Status.LISTENING, Status.SPEAKING, Status.FAREWELL}


def load_greetings(path: str) -> list[str]:
    if not path:
        return []
    try:
        lines = Path(path).read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        LOG.warning("no se pudo leer el banco de saludos %s: %s", path, exc)
        return []
    return [l.strip() for l in lines if l.strip() and not l.strip().startswith("#")]


class SessionManager:
    def __init__(self, cfg: Config, terminal: Terminal, store: StateStore,
                 agent_factory: Callable[..., AgentSession] | None = None):
        self.cfg = cfg
        self.terminal = terminal
        self.store = store
        self.agent_factory = agent_factory or (lambda el_cfg, events: AgentSession(el_cfg, events))
        self.greetings = load_greetings(cfg.elevenlabs.greetings_file)
        self._recent_greetings: deque[str] = deque(maxlen=5)

        self.status = Status.IDLE if store.state.enabled else Status.DISABLED
        self.last_error = ""
        self.last_end_reason = ""
        self.last_session_seconds = 0.0
        self.conversation_id: str | None = None

        self._agent: AgentSession | None = None
        self._session_task: asyncio.Task | None = None
        self._t_start = 0.0
        self._last_user = 0.0
        self._play_end = 0.0
        # Inactividad: se cuenta desde el fin de la última respuesta de Danny a algo que
        # dijo el visitante (o del saludo). Sus "¿siguen ahí?" no reinician el contador.
        self._idle_base = 0.0
        self._reply_turn = True
        self._end_request: str | None = None
        self._farewell_reason: str | None = None
        self._t_farewell = 0.0
        self._aggregator = audio.FrameAggregator(audio.chunk_bytes(cfg.session.input_chunk_ms))
        self._out_q: asyncio.Queue[bytes] | None = None
        self._play_q: asyncio.Queue[bytes | None] | None = None

        self.listeners: list[Callable[[dict], None]] = []
        self.event_listeners: list[Callable[[str, dict], None]] = []

        terminal.on_frame = self._on_frame
        terminal.on_button = self._on_button
        terminal.on_connection = self._on_terminal_connection

    # ------------------------------------------------------------ ajustes

    def setting(self, key: str) -> float:
        return float(self.store.state.overrides.get(key, getattr(self.cfg.session, key)))

    def set_setting(self, key: str, value: float) -> None:
        if key not in SETTING_KEYS:
            raise KeyError(key)
        lo, hi = SETTING_LIMITS[key]
        self.store.state.overrides[key] = max(lo, min(hi, float(value)))
        self.store.save()
        self._notify()

    @property
    def enabled(self) -> bool:
        return self.store.state.enabled

    async def set_enabled(self, enabled: bool) -> None:
        self.store.state.enabled = enabled
        self.store.save()
        if not enabled:
            if self.status in ACTIVE or self.status == Status.CONNECTING:
                await self.end("disabled")
            elif self.status in (Status.IDLE, Status.COOLDOWN, Status.ERROR):
                self._set_status(Status.DISABLED)
        elif self.status == Status.DISABLED:
            self._set_status(Status.IDLE)
        self._notify()

    # ------------------------------------------------------------ API pública

    def snapshot(self) -> dict:
        now = time.monotonic()
        active = self.status in ACTIVE
        t = self.store.state.today
        return {
            "status": self.status.value,
            "enabled": self.enabled,
            "terminal_connected": self.terminal.connected,
            "speaking": active and now < self._play_end,
            "listening": self.status == Status.LISTENING,
            "conversation_id": self.conversation_id if active else None,
            "session_seconds": round(now - self._t_start, 1) if active else 0.0,
            "last_session_seconds": round(self.last_session_seconds, 1),
            "last_end_reason": self.last_end_reason,
            "last_error": self.last_error,
            "sessions_today": t.sessions,
            "minutes_today": round(t.seconds / 60, 2),
            "cost_today_usd": round(t.cost_usd, 3),
            **{k: self.setting(k) for k in SETTING_KEYS},
        }

    async def start(self, source: str = "manual", force: bool = False) -> bool:
        """Iniciar una conversación. `force` ignora el enfriamiento (no el apagado)."""
        if not self.enabled:
            LOG.info("inicio (%s) ignorado: Danny está desactivado", source)
            return False
        if self.status == Status.COOLDOWN and force:
            pass
        elif self.status != Status.IDLE:
            LOG.info("inicio (%s) ignorado: estado %s", source, self.status.value)
            return False
        if not self.terminal.connected:
            LOG.warning("inicio (%s) ignorado: no hay terminal conectado", source)
            return False
        if self._session_task and not self._session_task.done():
            self._session_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._session_task
        self._end_request = None
        self._set_status(Status.CONNECTING)  # síncrono: bloquea un segundo start()
        self._session_task = asyncio.create_task(self._run_session(source), name="session")
        return True

    async def end(self, reason: str = "manual") -> bool:
        if self.status in ACTIVE:
            self._end_request = reason
            return True
        if self.status == Status.CONNECTING and self._session_task:
            self._end_request = reason
            return True
        return False

    async def shutdown(self) -> None:
        if self._session_task and not self._session_task.done():
            self._end_request = "shutdown"
            try:
                await asyncio.wait_for(self._session_task, timeout=5)
            except TimeoutError:
                self._session_task.cancel()
        if self._agent is not None:
            await self._agent.close()

    # ------------------------------------------------------------ eventos del agente

    def on_agent_audio(self, pcm: bytes) -> None:
        now = time.monotonic()
        self._play_end = max(self._play_end, now) + audio.duration_s(pcm)
        if self._reply_turn:
            self._idle_base = max(self._idle_base, self._play_end)
        if self._play_q is not None:
            self._play_q.put_nowait(pcm)

    def on_agent_response(self, text: str) -> None:
        LOG.info("DANNY: %s", text)

    def on_user_transcript(self, text: str) -> None:
        LOG.info("VISITANTE: %s", text)
        # ElevenLabs genera transcripciones vacías ("...") al tomar el turno por silencio:
        # no cuentan como actividad del visitante.
        if any(ch.isalnum() for ch in text):
            self._last_user = time.monotonic()
            self._reply_turn = True
        else:
            self._reply_turn = False

    def on_interruption(self) -> None:
        LOG.info("interrupción")
        self._play_end = time.monotonic()
        if self._play_q is not None:
            while not self._play_q.empty():
                self._play_q.get_nowait()
        asyncio.get_running_loop().create_task(self.terminal.flush())

    # ------------------------------------------------------------ terminal

    def _on_frame(self, pcm: bytes) -> None:
        if self.status not in ACTIVE or self._out_q is None:
            return
        tail = self.cfg.session.playback_tail_ms / 1000
        for chunk in self._aggregator.push(pcm):
            if time.monotonic() < self._play_end + tail:
                chunk = bytes(len(chunk))  # half-duplex: Danny no se oye a sí mismo
            if self._out_q.full():
                self._out_q.get_nowait()  # nunca acumular retraso
            self._out_q.put_nowait(chunk)

    def _on_button(self, event: str) -> None:
        loop = asyncio.get_running_loop()
        if self.status in ACTIVE:
            loop.create_task(self.end("terminal_button"))
        else:
            loop.create_task(self.start("terminal_button", force=True))

    def _on_terminal_connection(self, connected: bool) -> None:
        if not connected and (self.status in ACTIVE or self.status == Status.CONNECTING):
            self._end_request = "terminal_disconnected"
        self._notify()

    # ------------------------------------------------------------ ciclo de una sesión

    def _pick_greeting(self) -> str:
        # Evita repetir los últimos saludos (como máximo len-1, para que siempre haya opción).
        window = min(len(self._recent_greetings), max(len(self.greetings) - 1, 0))
        recent = list(self._recent_greetings)[-window:] if window else []
        options = [g for g in self.greetings if g not in recent] or self.greetings
        greeting = random.choice(options) if options else DEFAULT_GREETING
        self._recent_greetings.append(greeting)
        return greeting

    async def _run_session(self, source: str) -> None:
        self._farewell_reason = None
        self._set_status(Status.CONNECTING)
        # Las colas existen ANTES de conectar: el audio del saludo llega en cuanto
        # el servidor acepta la conversación, antes de que connect() retorne.
        self._play_end = self._idle_base = time.monotonic()
        self._reply_turn = True  # el saludo cuenta como respuesta
        self._aggregator.clear()
        self._out_q = asyncio.Queue(maxsize=50)
        self._play_q = asyncio.Queue()
        player = asyncio.create_task(self._player(), name="player")
        agent = self.agent_factory(self.cfg.elevenlabs, self)
        try:
            self.conversation_id = await agent.connect({"saludo": self._pick_greeting()})
        except (AgentError, OSError) as exc:
            player.cancel()
            self._out_q = self._play_q = None
            await self._fail(str(exc))
            return
        if self._end_request:
            player.cancel()
            self._out_q = self._play_q = None
            await agent.close()
            self._finish_without_session()
            return

        self._agent = agent
        self._t_start = self._last_user = time.monotonic()
        sender = asyncio.create_task(self._sender(agent), name="mic-sender")
        # El saludo llega justo después de iniciar: esperarlo (máx. 3 s) para no
        # anunciar "escuchando" mientras Danny está por hablar.
        deadline = time.monotonic() + 3
        while time.monotonic() >= self._play_end and time.monotonic() < deadline:
            await asyncio.sleep(0.05)
        await self.terminal.set_mic(True)
        self._set_status(Status.SPEAKING if time.monotonic() < self._play_end else Status.LISTENING)
        self._emit("session_started", {"source": source, "conversation_id": self.conversation_id})
        LOG.info("sesión iniciada (%s) conversación=%s", source, self.conversation_id)

        reason = "unknown"
        try:
            reason = await self._supervise(agent)
        finally:
            await self.terminal.set_mic(False)
            if not agent.closed.is_set():
                await agent.close()
            # dejar terminar lo que ya se envió al terminal (p. ej. la despedida)
            deadline = time.monotonic() + 15
            while time.monotonic() < min(self._play_end, deadline):
                await asyncio.sleep(0.05)
            for task in (sender, player):
                task.cancel()
            self._agent = None
            self._out_q = self._play_q = None

        duration = time.monotonic() - self._t_start
        cost = duration / 60 * self.cfg.elevenlabs.cost_per_min_usd
        self.last_session_seconds = duration
        self.last_end_reason = reason
        self.store.add_session(duration, cost)
        LOG.info("sesión terminada: motivo=%s duración=%.1fs costo≈US$%.3f", reason, duration, cost)
        self._emit("session_ended", {"reason": reason, "seconds": round(duration, 1),
                                     "cost_usd": round(cost, 4),
                                     "conversation_id": self.conversation_id})
        await self._cooldown(reason)

    async def _supervise(self, agent: AgentSession) -> str:
        while True:
            await asyncio.sleep(0.1)
            now = time.monotonic()
            speaking = now < self._play_end
            if agent.closed.is_set():
                if self._farewell_reason:
                    return self._farewell_reason
                return "agent_end_call" if agent.closed_by_server else "closed"
            if self._end_request in ("terminal_disconnected", "shutdown"):
                return self._end_request
            if self._farewell_reason is None:
                trigger = None
                if self._end_request:
                    trigger = self._end_request
                elif now - self._t_start >= self.setting("max_session_s"):
                    trigger = "max_duration"
                elif (now - max(self._last_user, self._idle_base)
                      >= self.setting("idle_timeout_s")):
                    trigger = "idle"
                if trigger and not speaking:
                    self._farewell_reason = trigger
                    self._t_farewell = now
                    self._set_status(Status.FAREWELL)
                    LOG.info("pidiendo despedida (%s)", trigger)
                    await agent.send_user_message(self.cfg.session.farewell_note)
                    continue
            elif (now - self._t_farewell >= self.cfg.session.farewell_timeout_s
                  and not speaking):
                return self._farewell_reason
            if self._farewell_reason is None:
                self._set_status(Status.SPEAKING if speaking else Status.LISTENING)

    async def _sender(self, agent: AgentSession) -> None:
        assert self._out_q is not None
        q = self._out_q
        while True:
            chunk = await q.get()
            await agent.send_audio(chunk)

    async def _player(self) -> None:
        assert self._play_q is not None
        q = self._play_q
        while True:
            pcm = await q.get()
            if pcm:
                await self.terminal.play(pcm)

    async def _fail(self, message: str) -> None:
        LOG.error("error de sesión: %s", message)
        self.last_error = message
        self._set_status(Status.ERROR)
        self._emit("error", {"message": message})
        await asyncio.sleep(self.cfg.session.error_retry_s)
        self._set_status(Status.IDLE if self.enabled else Status.DISABLED)

    def _finish_without_session(self) -> None:
        self._set_status(Status.IDLE if self.enabled else Status.DISABLED)

    async def _cooldown(self, reason: str) -> None:
        if not self.enabled:
            self._set_status(Status.DISABLED)
            return
        cooldown = self.setting("cooldown_s")
        if reason in ("terminal_disconnected", "shutdown") or cooldown <= 0:
            self._set_status(Status.IDLE)
            return
        self._set_status(Status.COOLDOWN)
        end = time.monotonic() + cooldown
        while time.monotonic() < end and self.status == Status.COOLDOWN:
            await asyncio.sleep(0.1)
        if self.status == Status.COOLDOWN:
            self._set_status(Status.IDLE if self.enabled else Status.DISABLED)

    # ------------------------------------------------------------ notificaciones

    def _set_status(self, status: Status) -> None:
        if status != self.status:
            self.status = status
            LOG.debug("estado → %s", status.value)
            asyncio.get_running_loop().create_task(self.terminal.set_state(status.value))
            self._notify()

    def _notify(self) -> None:
        snap = self.snapshot()
        for cb in self.listeners:
            try:
                cb(snap)
            except Exception:  # noqa: BLE001
                LOG.exception("listener de estado falló")

    def _emit(self, event: str, data: dict) -> None:
        for cb in self.event_listeners:
            try:
                cb(event, data)
            except Exception:  # noqa: BLE001
                LOG.exception("listener de eventos falló")
