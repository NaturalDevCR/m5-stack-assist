"""Cliente del WebSocket de ElevenLabs Agents (protocolo documentado, sin SDK).

Una instancia = una conversación. El gestor de sesiones crea una nueva por cada
grupo de visitantes y la cierra al terminar.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import urllib.error
import urllib.parse
import urllib.request
from typing import Protocol

from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed, WebSocketException

from .audio import parse_pcm_format
from .config import ElevenLabsConfig

LOG = logging.getLogger(__name__)


class AgentEvents(Protocol):
    """Lo que el gestor de sesiones recibe de la conversación."""

    def on_agent_audio(self, pcm: bytes) -> None: ...
    def on_agent_response(self, text: str) -> None: ...
    def on_user_transcript(self, text: str) -> None: ...
    def on_interruption(self) -> None: ...


class AgentError(RuntimeError):
    pass


def get_signed_url(base_url: str, agent_id: str, api_key: str, timeout: float = 10.0) -> str:
    """GET /v1/convai/conversation/get-signed-url (requiere permiso convai_write)."""
    query = urllib.parse.urlencode({"agent_id": agent_id})
    url = f"{base_url.rstrip('/')}/v1/convai/conversation/get-signed-url?{query}"
    req = urllib.request.Request(url, headers={"xi-api-key": api_key})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.load(resp)
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "replace")[:300]
        raise AgentError(f"get-signed-url HTTP {exc.code}: {body}") from exc
    except OSError as exc:
        raise AgentError(f"get-signed-url: {exc}") from exc
    signed = data.get("signed_url")
    if not signed:
        raise AgentError("respuesta sin signed_url")
    return signed


def redact(url: str) -> str:
    parsed = urllib.parse.urlparse(url)
    params = urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
    safe = [(k, v if k == "agent_id" else "***") for k, v in params]
    return urllib.parse.urlunparse(parsed._replace(query=urllib.parse.urlencode(safe)))


class AgentSession:
    def __init__(self, cfg: ElevenLabsConfig, events: AgentEvents, input_rate: int = 16000):
        self.cfg = cfg
        self.events = events
        self.input_rate = input_rate
        self.conversation_id: str | None = None
        self.output_rate: int | None = None
        self.close_code: int | None = None
        self.close_reason: str = ""
        self.closed = asyncio.Event()
        self._ws = None
        self._recv_task: asyncio.Task | None = None
        self._ready = asyncio.Event()
        self._closing_locally = False

    @property
    def closed_by_server(self) -> bool:
        return self.closed.is_set() and not self._closing_locally

    async def connect(self, dynamic_variables: dict[str, str] | None = None,
                      timeout: float = 15.0) -> str:
        if self.cfg.ws_url:
            url = self.cfg.ws_url
        else:
            url = await asyncio.to_thread(get_signed_url, self.cfg.base_url,
                                          self.cfg.agent_id, self.cfg.api_key)
        LOG.info("conectando a ElevenLabs %s", redact(url))
        try:
            self._ws = await connect(url, open_timeout=timeout, max_size=2**23)
        except (OSError, WebSocketException, TimeoutError) as exc:
            raise AgentError(f"no se pudo abrir el WebSocket: {exc}") from exc
        init: dict = {"type": "conversation_initiation_client_data"}
        if dynamic_variables:
            init["dynamic_variables"] = dynamic_variables
        await self._ws.send(json.dumps(init))
        self._recv_task = asyncio.create_task(self._receiver(), name="elevenlabs-recv")
        try:
            await asyncio.wait_for(self._ready.wait(), timeout=timeout)
        except TimeoutError as exc:
            await self.close()
            raise AgentError("sin conversation_initiation_metadata") from exc
        if self.closed.is_set():
            raise AgentError(f"el servidor cerró al iniciar: {self.close_code} {self.close_reason}")
        return self.conversation_id or ""

    async def send_audio(self, pcm: bytes) -> None:
        if self._ws is None or self.closed.is_set():
            return
        try:
            await self._ws.send(json.dumps({"user_audio_chunk": base64.b64encode(pcm).decode()}))
        except ConnectionClosed:
            pass

    async def send_user_message(self, text: str) -> None:
        if self._ws is None or self.closed.is_set():
            return
        try:
            await self._ws.send(json.dumps({"type": "user_message", "text": text}))
        except ConnectionClosed:
            pass

    async def close(self) -> None:
        self._closing_locally = not self.closed.is_set()
        if self._ws is not None:
            try:
                await self._ws.close(code=1000, reason="fin de sesión")
            except Exception:  # noqa: BLE001 - cerrar nunca debe propagar
                pass
        if self._recv_task is not None:
            try:
                await asyncio.wait_for(self._recv_task, timeout=5)
            except (TimeoutError, asyncio.CancelledError):
                self._recv_task.cancel()
        self.closed.set()

    # ------------------------------------------------------------- recepción

    async def _receiver(self) -> None:
        ws = self._ws
        try:
            async for raw in ws:
                if isinstance(raw, bytes):
                    continue
                try:
                    msg = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                await self._handle(msg)
        except ConnectionClosed:
            pass
        except Exception:  # noqa: BLE001
            LOG.exception("error procesando eventos de ElevenLabs")
        finally:
            self.close_code = ws.close_code
            self.close_reason = ws.close_reason or ""
            self.closed.set()
            self._ready.set()
            LOG.info("conversación %s cerrada (code=%s reason=%r local=%s)",
                     self.conversation_id, self.close_code, self.close_reason,
                     self._closing_locally)

    async def _handle(self, msg: dict) -> None:
        etype = msg.get("type")
        if etype == "conversation_initiation_metadata":
            ev = msg.get("conversation_initiation_metadata_event", {})
            self.conversation_id = ev.get("conversation_id")
            try:
                self.output_rate = parse_pcm_format(ev.get("agent_output_audio_format"))
                in_rate = parse_pcm_format(ev.get("user_input_audio_format"))
            except ValueError as exc:
                LOG.error("%s; configura PCM 16000 Hz en el agente", exc)
                await self._ws.close(code=1000, reason="formato no soportado")
                return
            if in_rate != self.input_rate or self.output_rate != self.input_rate:
                LOG.error("el agente usa entrada pcm_%s / salida pcm_%s; se requiere pcm_%s",
                          in_rate, self.output_rate, self.input_rate)
                await self._ws.close(code=1000, reason="formato no soportado")
                return
            LOG.info("conversación %s iniciada", self.conversation_id)
            self._ready.set()
        elif etype == "ping":
            ev = msg.get("ping_event", {})
            await self._ws.send(json.dumps({"type": "pong", "event_id": ev.get("event_id")}))
        elif etype == "audio":
            b64 = msg.get("audio_event", {}).get("audio_base_64")
            if b64:
                self.events.on_agent_audio(base64.b64decode(b64))
        elif etype == "agent_response":
            self.events.on_agent_response(
                msg.get("agent_response_event", {}).get("agent_response", ""))
        elif etype == "user_transcript":
            self.events.on_user_transcript(
                msg.get("user_transcription_event", {}).get("user_transcript", ""))
        elif etype == "interruption":
            self.events.on_interruption()
        elif etype == "client_tool_call":
            call = msg.get("client_tool_call", {})
            LOG.warning("herramienta de cliente no implementada: %s", call.get("tool_name"))
            await self._ws.send(json.dumps({
                "type": "client_tool_result",
                "tool_call_id": call.get("tool_call_id"),
                "result": "Herramienta no disponible.",
                "is_error": True,
            }))
        elif etype in ("error", "queue_status"):
            LOG.warning("ElevenLabs %s: %s", etype, msg)
