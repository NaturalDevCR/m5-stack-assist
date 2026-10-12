"""Utilidades de audio PCM s16le mono para las pruebas con ElevenLabs Agents.

Sólo biblioteca estándar: el mismo formato que usará el terminal AtomS3R
(PCM 16 bit, little-endian, mono).
"""

from __future__ import annotations

import array
import math
import sys
import wave
from collections.abc import Iterator
from pathlib import Path

SAMPLE_WIDTH = 2  # bytes por muestra (16 bit)


class AudioFormatError(ValueError):
    """El archivo o el formato anunciado no es el esperado."""


def bytes_per_ms(sample_rate: int) -> float:
    return sample_rate * SAMPLE_WIDTH / 1000.0


def chunk_size_bytes(sample_rate: int, chunk_ms: int) -> int:
    """Tamaño en bytes de una trama de `chunk_ms`, alineado a muestra."""
    if chunk_ms <= 0:
        raise ValueError("chunk_ms debe ser > 0")
    samples = sample_rate * chunk_ms // 1000
    if samples == 0:
        raise ValueError("chunk_ms demasiado pequeño para la frecuencia")
    return samples * SAMPLE_WIDTH


def silence(sample_rate: int, ms: int) -> bytes:
    return bytes(sample_rate * ms // 1000 * SAMPLE_WIDTH)


def duration_s(pcm: bytes, sample_rate: int) -> float:
    return len(pcm) / (sample_rate * SAMPLE_WIDTH)


def read_wav_pcm16_mono(path: str | Path, expected_rate: int) -> bytes:
    """Lee un WAV PCM 16 bit mono y devuelve los bytes crudos.

    No remuestrea a propósito: si el archivo no coincide, falla con un mensaje
    claro en lugar de enviar audio a una frecuencia incorrecta.
    """
    path = Path(path)
    try:
        with wave.open(str(path), "rb") as wf:
            channels = wf.getnchannels()
            width = wf.getsampwidth()
            rate = wf.getframerate()
            comp = wf.getcomptype()
            frames = wf.readframes(wf.getnframes())
    except wave.Error as exc:
        raise AudioFormatError(f"{path}: no es un WAV PCM válido ({exc})") from exc

    problems = []
    if comp != "NONE":
        problems.append(f"compresión {comp}")
    if channels != 1:
        problems.append(f"{channels} canales (se requiere mono)")
    if width != SAMPLE_WIDTH:
        problems.append(f"{width * 8} bit (se requiere 16 bit)")
    if rate != expected_rate:
        problems.append(f"{rate} Hz (se requiere {expected_rate} Hz)")
    if problems:
        raise AudioFormatError(
            f"{path}: " + ", ".join(problems)
            + f". Convierte con: afconvert -f WAVE -d LEI16@{expected_rate} -c 1 entrada salida.wav"
        )
    return frames


def write_wav_pcm16_mono(path: str | Path, pcm: bytes, sample_rate: int) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(SAMPLE_WIDTH)
        wf.setframerate(sample_rate)
        wf.writeframes(pcm)


def iter_chunks(pcm: bytes, sample_rate: int, chunk_ms: int) -> Iterator[bytes]:
    """Divide en tramas de `chunk_ms`; la última se rellena con silencio."""
    size = chunk_size_bytes(sample_rate, chunk_ms)
    for i in range(0, len(pcm), size):
        chunk = pcm[i:i + size]
        if len(chunk) < size:
            chunk += bytes(size - len(chunk))
        yield chunk


def _samples(pcm: bytes) -> array.array:
    if len(pcm) % SAMPLE_WIDTH:
        raise AudioFormatError("longitud PCM impar: no es s16le")
    samples = array.array("h")
    samples.frombytes(pcm)
    if sys.byteorder == "big":
        samples.byteswap()
    return samples


def rms_dbfs(pcm: bytes) -> float:
    """Nivel RMS en dBFS (−inf para silencio digital)."""
    samples = _samples(pcm)
    if not samples:
        return float("-inf")
    acc = sum(s * s for s in samples)
    rms = math.sqrt(acc / len(samples))
    if rms == 0:
        return float("-inf")
    return 20 * math.log10(rms / 32768.0)


def speech_end_s(pcm: bytes, sample_rate: int, threshold_dbfs: float = -45.0,
                 window_ms: int = 20) -> float:
    """Instante (s) en que termina la última ventana con energía > umbral.

    Sirve para medir latencia desde el final real de la voz, no desde el final
    del archivo (que suele traer silencio de cola).
    """
    size = chunk_size_bytes(sample_rate, window_ms)
    last_end = 0
    for i in range(0, len(pcm), size):
        if rms_dbfs(pcm[i:i + size]) > threshold_dbfs:
            last_end = min(i + size, len(pcm))
    return last_end / (sample_rate * SAMPLE_WIDTH)


def parse_pcm_format(fmt: str) -> int:
    """'pcm_16000' -> 16000. Otros formatos (ulaw_8000…) no se aceptan."""
    if not isinstance(fmt, str) or not fmt.startswith("pcm_"):
        raise AudioFormatError(f"formato no soportado por esta prueba: {fmt!r}")
    try:
        return int(fmt.removeprefix("pcm_"))
    except ValueError as exc:
        raise AudioFormatError(f"formato PCM ilegible: {fmt!r}") from exc
