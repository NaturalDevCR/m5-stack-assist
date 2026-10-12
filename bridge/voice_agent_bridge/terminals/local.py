"""Terminal con el micrófono y el parlante del equipo donde corre el puente.

Sirve para probar de punta a punta sin el Atom y como plan B del evento
(mini PC + micrófono USB + parlante). Requiere el extra `local` (sounddevice).
"""

from __future__ import annotations

import asyncio
import logging
import threading

from ..audio import SAMPLE_RATE
from ..config import LocalTerminalConfig
from . import Terminal

LOG = logging.getLogger(__name__)
BLOCK = SAMPLE_RATE * 20 // 1000  # 20 ms


class LocalTerminal(Terminal):
    def __init__(self, cfg: LocalTerminalConfig):
        super().__init__()
        self.cfg = cfg
        self._loop: asyncio.AbstractEventLoop | None = None
        self._in = None
        self._out = None
        self._play_buf = bytearray()
        self._lock = threading.Lock()
        self._mic_on = False

    @property
    def connected(self) -> bool:
        return self._in is not None

    async def start(self) -> None:
        try:
            import sounddevice as sd
        except ImportError as exc:  # pragma: no cover - depende del equipo
            raise RuntimeError("terminal local requiere: uv sync --extra local") from exc
        self._loop = asyncio.get_running_loop()
        self._in = sd.RawInputStream(samplerate=SAMPLE_RATE, channels=1, dtype="int16",
                                     blocksize=BLOCK, device=self.cfg.input_device,
                                     callback=self._on_input)
        self._out = sd.RawOutputStream(samplerate=SAMPLE_RATE, channels=1, dtype="int16",
                                       blocksize=BLOCK, device=self.cfg.output_device,
                                       callback=self._on_output)
        self._in.start()
        self._out.start()
        LOG.info("terminal local activo (entrada=%s salida=%s)",
                 self.cfg.input_device or "predeterminada",
                 self.cfg.output_device or "predeterminada")
        self.on_connection(True)

    async def stop(self) -> None:
        for stream in (self._in, self._out):
            if stream is not None:
                stream.stop()
                stream.close()
        self._in = self._out = None

    # Hilos de audio de PortAudio: nada bloqueante aquí.
    def _on_input(self, indata, frames, time_info, status) -> None:  # pragma: no cover
        if self._mic_on and self._loop is not None:
            self._loop.call_soon_threadsafe(self.on_frame, bytes(indata))

    def _on_output(self, outdata, frames, time_info, status) -> None:  # pragma: no cover
        need = len(outdata)
        with self._lock:
            chunk = bytes(self._play_buf[:need])
            del self._play_buf[:need]
        outdata[:len(chunk)] = chunk
        if len(chunk) < need:
            outdata[len(chunk):] = bytes(need - len(chunk))

    async def play(self, pcm: bytes) -> None:
        with self._lock:
            self._play_buf.extend(pcm)

    async def flush(self) -> None:
        with self._lock:
            self._play_buf.clear()

    async def set_mic(self, on: bool) -> None:
        self._mic_on = on

    async def set_state(self, state: str) -> None:
        LOG.debug("estado: %s", state)
