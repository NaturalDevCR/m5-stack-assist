"""Utilidades PCM s16le mono (formato del terminal y del agente: 16 kHz)."""

from __future__ import annotations

SAMPLE_RATE = 16000
SAMPLE_WIDTH = 2


def duration_s(pcm: bytes, rate: int = SAMPLE_RATE) -> float:
    return len(pcm) / (rate * SAMPLE_WIDTH)


def chunk_bytes(ms: int, rate: int = SAMPLE_RATE) -> int:
    samples = rate * ms // 1000
    if samples <= 0:
        raise ValueError("tamaño de trama inválido")
    return samples * SAMPLE_WIDTH


def parse_pcm_format(fmt: str | None) -> int:
    """'pcm_16000' -> 16000; lanza ValueError con otros formatos."""
    if not fmt or not fmt.startswith("pcm_"):
        raise ValueError(f"formato de audio no soportado: {fmt!r}")
    return int(fmt.removeprefix("pcm_"))


class FrameAggregator:
    """Junta tramas de cualquier tamaño en bloques fijos de `chunk` bytes."""

    def __init__(self, chunk: int):
        self.chunk = chunk
        self._buf = bytearray()

    def push(self, data: bytes) -> list[bytes]:
        if len(data) % SAMPLE_WIDTH:
            data = data[: len(data) - 1]  # nunca partir una muestra
        self._buf.extend(data)
        out = []
        while len(self._buf) >= self.chunk:
            out.append(bytes(self._buf[: self.chunk]))
            del self._buf[: self.chunk]
        return out

    def clear(self) -> None:
        self._buf.clear()
