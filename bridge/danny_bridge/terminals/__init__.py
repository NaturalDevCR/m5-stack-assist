"""Terminales de audio: algo que captura voz (micrófono) y reproduce a Danny.

Todas hablan PCM s16le mono 16 kHz con el gestor de sesiones.
"""

from __future__ import annotations

from collections.abc import Callable

FrameCallback = Callable[[bytes], None]
ButtonCallback = Callable[[str], None]
ConnectionCallback = Callable[[bool], None]


class Terminal:
    """Interfaz común. Las implementaciones llaman a los callbacks asignados."""

    def __init__(self) -> None:
        self.on_frame: FrameCallback = lambda pcm: None
        self.on_button: ButtonCallback = lambda event: None
        self.on_connection: ConnectionCallback = lambda connected: None

    @property
    def connected(self) -> bool:
        raise NotImplementedError

    async def start(self) -> None:
        """Abrir dispositivos o empezar a escuchar conexiones."""

    async def stop(self) -> None:
        """Liberar recursos."""

    async def play(self, pcm: bytes) -> None:
        """Encolar audio de Danny para reproducir."""
        raise NotImplementedError

    async def flush(self) -> None:
        """Vaciar el audio pendiente (interrupción)."""

    async def set_mic(self, on: bool) -> None:
        """Pedir al terminal que envíe (o deje de enviar) audio del micrófono."""

    async def set_state(self, state: str) -> None:
        """Informar el estado (para pantalla/LED del terminal)."""


def create_terminal(cfg) -> Terminal:
    if cfg.terminal.type == "local":
        from .local import LocalTerminal
        return LocalTerminal(cfg.terminal.local)
    from .websocket import WebSocketTerminalServer
    return WebSocketTerminalServer(cfg.terminal.websocket)
