"""Servidor WebSocket para terminales hardware (AtomS3R + Atomic Audio-3.5 Base).

Protocolo v0 (docs/01 §3.1):
  conexión  ws://<puente>:8765/v0/terminal?id=<nombre>
            cabecera "Authorization: Bearer <token>" (o ?token=<token>)
  subida    binario: PCM s16le 16 kHz mono (20 ms recomendado)
            texto:   {"t":"hello",...} {"t":"button","ev":"short"} {"t":"stats",...}
  bajada    binario: PCM s16le 16 kHz mono
            texto:   {"t":"mic","on":bool} {"t":"flush"} {"t":"state","s":"..."}

Un solo terminal activo: una conexión nueva reemplaza a la anterior.
"""

from __future__ import annotations

import hmac
import json
import logging
import urllib.parse

from websockets.asyncio.server import ServerConnection, serve
from websockets.exceptions import ConnectionClosed
from websockets.http11 import Request, Response

from ..config import WebSocketTerminalConfig
from . import Terminal

LOG = logging.getLogger(__name__)
PATH = "/v0/terminal"


class WebSocketTerminalServer(Terminal):
    def __init__(self, cfg: WebSocketTerminalConfig):
        super().__init__()
        self.cfg = cfg
        self._server = None
        self._conn: ServerConnection | None = None
        self.terminal_id: str | None = None
        self.last_stats: dict = {}
        self._mic_on = False
        self._state = "idle"

    @property
    def connected(self) -> bool:
        return self._conn is not None

    @property
    def port(self) -> int:
        return self._server.sockets[0].getsockname()[1] if self._server else self.cfg.port

    async def start(self) -> None:
        self._server = await serve(self._handler, self.cfg.host, self.cfg.port,
                                   process_request=self._authorize, max_size=2**20)
        LOG.info("esperando terminales en ws://%s:%s%s", self.cfg.host, self.port, PATH)

    async def stop(self) -> None:
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()

    def _authorize(self, connection: ServerConnection, request: Request) -> Response | None:
        parsed = urllib.parse.urlparse(request.path)
        if parsed.path != PATH:
            return connection.respond(404, "ruta desconocida\n")
        token = ""
        auth = request.headers.get("Authorization", "")
        if auth.startswith("Bearer "):
            token = auth.removeprefix("Bearer ").strip()
        if not token:
            token = urllib.parse.parse_qs(parsed.query).get("token", [""])[0]
        if not self.cfg.token or not hmac.compare_digest(token, self.cfg.token):
            LOG.warning("terminal rechazado (token inválido) desde %s", connection.remote_address)
            return connection.respond(401, "token inválido\n")
        return None

    async def _handler(self, conn: ServerConnection) -> None:
        query = urllib.parse.parse_qs(urllib.parse.urlparse(conn.request.path).query)
        terminal_id = query.get("id", ["terminal"])[0]
        old = self._conn
        self._conn, self.terminal_id = conn, terminal_id
        if old is not None:
            LOG.info("nuevo terminal reemplaza al anterior")
            await old.close(code=4000, reason="reemplazado")
        LOG.info("terminal %s conectado desde %s", terminal_id, conn.remote_address)
        self.on_connection(True)
        await self._send_json({"t": "mic", "on": self._mic_on})
        await self._send_json({"t": "state", "s": self._state})
        try:
            async for msg in conn:
                if isinstance(msg, bytes):
                    if self._mic_on:
                        self.on_frame(msg)
                else:
                    self._on_text(msg)
        except ConnectionClosed:
            pass
        finally:
            if self._conn is conn:
                self._conn = None
                LOG.info("terminal %s desconectado", terminal_id)
                self.on_connection(False)

    def _on_text(self, raw: str) -> None:
        try:
            msg = json.loads(raw)
        except json.JSONDecodeError:
            return
        kind = msg.get("t")
        if kind == "hello":
            LOG.info("terminal: %s", msg)
        elif kind == "button":
            self.on_button(str(msg.get("ev", "short")))
        elif kind == "stats":
            self.last_stats = msg

    async def _send_json(self, obj: dict) -> None:
        conn = self._conn
        if conn is None:
            return
        try:
            await conn.send(json.dumps(obj))
        except ConnectionClosed:
            pass

    async def play(self, pcm: bytes) -> None:
        conn = self._conn
        if conn is None:
            return
        try:
            await conn.send(pcm)
        except ConnectionClosed:
            pass

    async def flush(self) -> None:
        await self._send_json({"t": "flush"})

    async def set_mic(self, on: bool) -> None:
        self._mic_on = on
        await self._send_json({"t": "mic", "on": on})

    async def set_state(self, state: str) -> None:
        self._state = state
        await self._send_json({"t": "state", "s": state})
