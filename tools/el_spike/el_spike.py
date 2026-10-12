#!/usr/bin/env python3
"""Prueba de conversación con un agente de ElevenLabs Agents, sin hardware.

Emula lo que hará el terminal AtomS3R + puente:
  * pide una signed URL con la API key (agente privado) o usa agent_id (público);
  * abre el WebSocket de Agents y envía audio PCM en tiempo real (silencio
    continuo + preguntas grabadas en WAV);
  * guarda todo el audio de Danny en WAV y todos los eventos en JSONL;
  * mide latencias desde el final real de cada pregunta.

Usa el protocolo WebSocket documentado directamente (sin SDK) para ver y
cronometrar cada evento. Ver README.md de esta carpeta.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import json
import logging
import os
import random
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field, asdict
from pathlib import Path

from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed, WebSocketException

import audio_utils as au

LOG = logging.getLogger("el_spike")
DEFAULT_BASE_URL = "https://api.elevenlabs.io"
INPUT_RATE = 16000  # el terminal captura a 16 kHz (ver docs/01)
# El agente usa first_message = {{saludo}} y rechaza sesiones sin esa variable.
DEFAULT_SALUDOS = Path(__file__).resolve().parents[2] / "personas" / "danny" / "saludos.txt"


# ---------------------------------------------------------------- configuración

def load_dotenv(path: Path) -> None:
    """Carga KEY=VALUE de un .env sin pisar variables ya definidas."""
    if not path.is_file():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


def get_signed_url(base_url: str, agent_id: str, api_key: str, timeout: float = 10.0) -> str:
    """GET /v1/convai/conversation/get-signed-url (doc ElevenLabs, autenticación)."""
    query = urllib.parse.urlencode({"agent_id": agent_id})
    url = f"{base_url.rstrip('/')}/v1/convai/conversation/get-signed-url?{query}"
    req = urllib.request.Request(url, headers={"xi-api-key": api_key})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.load(resp)
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "replace")[:500]
        raise RuntimeError(f"get-signed-url HTTP {exc.code}: {body}") from exc
    signed = data.get("signed_url")
    if not signed:
        raise RuntimeError(f"respuesta sin signed_url: {data}")
    return signed


def public_ws_url(base_url: str, agent_id: str) -> str:
    parsed = urllib.parse.urlparse(base_url)
    scheme = "wss" if parsed.scheme == "https" else "ws"
    query = urllib.parse.urlencode({"agent_id": agent_id})
    return f"{scheme}://{parsed.netloc}/v1/convai/conversation?{query}"


def redact(url: str) -> str:
    """Nunca registrar el token de la signed URL."""
    parsed = urllib.parse.urlparse(url)
    params = urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
    safe = [(k, v if k == "agent_id" else "***") for k, v in params]
    return urllib.parse.urlunparse(parsed._replace(query=urllib.parse.urlencode(safe)))


# ---------------------------------------------------------------- estado

@dataclass
class Turn:
    wav: str
    speech_s: float
    t_send_start: float = 0.0
    t_speech_end: float = 0.0
    t_user_transcript: float | None = None
    t_agent_response: float | None = None
    t_first_audio: float | None = None
    t_last_audio: float | None = None
    user_transcript: str = ""
    agent_response: str = ""
    response_audio_s: float = 0.0
    interrupted: bool = False

    def latencies_ms(self) -> dict[str, float | None]:
        def ms(t: float | None) -> float | None:
            return None if t is None else round((t - self.t_speech_end) * 1000)
        return {
            "transcript_ms": ms(self.t_user_transcript),
            "agent_text_ms": ms(self.t_agent_response),
            "first_audio_ms": ms(self.t_first_audio),
        }


@dataclass
class Session:
    t0: float = field(default_factory=time.monotonic)
    conversation_id: str | None = None
    input_format: str | None = None
    output_format: str | None = None
    output_rate: int | None = None
    t_signed_url: float | None = None
    t_ws_open: float | None = None
    t_metadata: float | None = None
    t_greeting_first_audio: float | None = None
    greeting_text: str = ""
    last_audio_t: float = 0.0
    # Reloj de reproducción emulado: el servidor asume que el cliente reproduce
    # el audio en tiempo real; si hablamos antes de que "termine", nos interrumpe.
    play_end: float = 0.0
    audio_events: int = 0
    agent_pcm: bytearray = field(default_factory=bytearray)
    ping_ms: list[int] = field(default_factory=list)
    event_counts: dict[str, int] = field(default_factory=dict)
    turns: list[Turn] = field(default_factory=list)
    current_turn: Turn | None = None
    close_code: int | None = None
    close_reason: str = ""
    closed_by_server: bool = False
    errors: list[str] = field(default_factory=list)

    def rel(self, t: float | None) -> float | None:
        return None if t is None else round(t - self.t0, 3)


# ---------------------------------------------------------------- envío de audio

class RealtimeSource:
    """Entrega una trama por período: el clip en curso o silencio.

    Igual que el terminal: el micrófono nunca deja de enviar mientras la sesión
    está abierta; cuando no hay pregunta se envía silencio digital.
    """

    def __init__(self, rate: int, chunk_ms: int):
        self.chunk_bytes = au.chunk_size_bytes(rate, chunk_ms)
        self._silence = bytes(self.chunk_bytes)
        self._clip = b""
        self._pos = 0
        self._done: asyncio.Future[float] | None = None
        self._started: asyncio.Future[float] | None = None

    def play(self, pcm: bytes) -> tuple[asyncio.Future[float], asyncio.Future[float]]:
        if self._done is not None and not self._done.done():
            raise RuntimeError("ya hay un clip en curso")
        loop = asyncio.get_running_loop()
        self._clip, self._pos = pcm, 0
        self._started, self._done = loop.create_future(), loop.create_future()
        return self._started, self._done

    def next_chunk(self, now: float) -> bytes:
        if self._pos >= len(self._clip):
            return self._silence
        if self._started is not None and not self._started.done():
            # La trama se envía al terminar de "capturarse": su inicio fue hace 1 período.
            self._started.set_result(now)
        chunk = self._clip[self._pos:self._pos + self.chunk_bytes]
        self._pos += self.chunk_bytes
        if len(chunk) < self.chunk_bytes:
            chunk += bytes(self.chunk_bytes - len(chunk))
        if self._pos >= len(self._clip) and self._done is not None and not self._done.done():
            self._done.set_result(now)
        return chunk


async def sender(ws, source: RealtimeSource, chunk_ms: int, stop: asyncio.Event) -> None:
    loop = asyncio.get_running_loop()
    period = chunk_ms / 1000.0
    next_t = loop.time() + period
    while not stop.is_set():
        delay = next_t - loop.time()
        if delay > 0:
            await asyncio.sleep(delay)
        elif delay < -period:
            LOG.warning("envío retrasado %.0f ms; resincronizando", -delay * 1000)
            next_t = loop.time()
        chunk = source.next_chunk(time.monotonic())
        msg = json.dumps({"user_audio_chunk": base64.b64encode(chunk).decode("ascii")})
        try:
            await ws.send(msg)
        except ConnectionClosed:
            return
        next_t += period


# ---------------------------------------------------------------- recepción

async def receiver(ws, sess: Session, events_file, stop: asyncio.Event,
                   metadata_ready: asyncio.Event) -> None:
    try:
        async for raw in ws:
            now = time.monotonic()
            if isinstance(raw, bytes):
                LOG.warning("mensaje binario inesperado (%d bytes)", len(raw))
                continue
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                LOG.warning("JSON inválido: %.120s", raw)
                continue
            etype = msg.get("type", "<sin type>")
            sess.event_counts[etype] = sess.event_counts.get(etype, 0) + 1
            await handle_event(ws, sess, etype, msg, now, metadata_ready)
            log_event(events_file, sess, etype, msg, now)
    except ConnectionClosed:
        pass
    finally:
        sess.close_code = ws.close_code
        sess.close_reason = ws.close_reason or ""
        if not stop.is_set():
            sess.closed_by_server = True
            LOG.info("el servidor cerró la conexión: code=%s reason=%r",
                     sess.close_code, sess.close_reason)
        stop.set()
        metadata_ready.set()


async def handle_event(ws, sess: Session, etype: str, msg: dict, now: float,
                       metadata_ready: asyncio.Event) -> None:
    turn = sess.current_turn
    if etype == "conversation_initiation_metadata":
        ev = msg.get("conversation_initiation_metadata_event", {})
        sess.conversation_id = ev.get("conversation_id")
        sess.input_format = ev.get("user_input_audio_format")
        sess.output_format = ev.get("agent_output_audio_format")
        sess.t_metadata = now
        LOG.info("conversación %s  entrada=%s salida=%s", sess.conversation_id,
                 sess.input_format, sess.output_format)
        metadata_ready.set()
    elif etype == "ping":
        ev = msg.get("ping_event", {})
        if ev.get("ping_ms") is not None:
            sess.ping_ms.append(int(ev["ping_ms"]))
        await ws.send(json.dumps({"type": "pong", "event_id": ev.get("event_id")}))
    elif etype == "audio":
        ev = msg.get("audio_event", {})
        b64 = ev.get("audio_base_64")
        if b64:
            pcm = base64.b64decode(b64)
            sess.agent_pcm.extend(pcm)
            sess.audio_events += 1
            sess.last_audio_t = now
            if sess.output_rate:
                sess.play_end = max(sess.play_end, now) + au.duration_s(pcm, sess.output_rate)
            if turn is None:
                if sess.t_greeting_first_audio is None:
                    sess.t_greeting_first_audio = now
            else:
                if turn.t_first_audio is None:
                    turn.t_first_audio = now
                turn.t_last_audio = now
                if sess.output_rate:
                    turn.response_audio_s += au.duration_s(pcm, sess.output_rate)
    elif etype == "user_transcript":
        text = msg.get("user_transcription_event", {}).get("user_transcript", "")
        LOG.info("VISITANTE: %s", text)
        if turn is not None:
            turn.user_transcript += (" " if turn.user_transcript else "") + text
            if turn.t_user_transcript is None:
                turn.t_user_transcript = now
    elif etype == "agent_response":
        text = msg.get("agent_response_event", {}).get("agent_response", "")
        LOG.info("DANNY: %s", text)
        if turn is None:
            sess.greeting_text += text
        else:
            turn.agent_response += (" " if turn.agent_response else "") + text
            if turn.t_agent_response is None:
                turn.t_agent_response = now
    elif etype == "interruption":
        LOG.warning("evento interruption recibido: %s", msg.get("interruption_event"))
        sess.play_end = now  # el terminal vacía su búfer de reproducción
        if turn is not None:
            turn.interrupted = True
    elif etype == "client_tool_call":
        call = msg.get("client_tool_call", {})
        LOG.warning("client_tool_call %s(%s): no hay herramientas en esta prueba",
                    call.get("tool_name"), call.get("parameters"))
        await ws.send(json.dumps({
            "type": "client_tool_result",
            "tool_call_id": call.get("tool_call_id"),
            "result": "Herramienta no disponible en esta prueba.",
            "is_error": True,
        }))
    elif etype in ("error", "queue_status"):
        LOG.warning("%s: %s", etype, msg)
        sess.errors.append(json.dumps(msg)[:500])


def log_event(events_file, sess: Session, etype: str, msg: dict, now: float) -> None:
    record = dict(msg)
    if etype == "audio":
        ev = dict(record.get("audio_event", {}))
        b64 = ev.pop("audio_base_64", "")
        ev["audio_bytes"] = len(b64) * 3 // 4
        ev.pop("alignment", None)
        record["audio_event"] = ev
    events_file.write(json.dumps({"t": sess.rel(now), **record}, ensure_ascii=False) + "\n")


# ---------------------------------------------------------------- guion

async def wait_agent_quiet(sess: Session, since: float, idle_s: float, timeout_s: float,
                           stop: asyncio.Event) -> bool:
    """True cuando el agente habló después de `since`, dejó de enviar audio y
    la reproducción emulada en tiempo real terminó hace `idle_s`."""
    deadline = time.monotonic() + timeout_s
    while not stop.is_set():
        now = time.monotonic()
        if (sess.last_audio_t > since and now - sess.last_audio_t >= 0.3
                and now >= sess.play_end + idle_s):
            return True
        # Una respuesta larga puede seguir "sonando" después del timeout base.
        if now >= max(deadline, sess.play_end + idle_s + 1.0):
            return False
        await asyncio.sleep(0.05)
    return False


async def script(args, sess: Session, source: RealtimeSource, clips: list[tuple[str, bytes]],
                 stop: asyncio.Event) -> None:
    # 1) Saludo (first_message). Si el agente no tiene, se espera poco y se sigue.
    if not await wait_agent_quiet(sess, sess.t0, args.idle_s, args.greeting_timeout_s, stop):
        if stop.is_set():
            return
        LOG.warning("no llegó saludo en %.0f s (¿first_message vacío?)", args.greeting_timeout_s)
    else:
        LOG.info("saludo terminado")

    # 2) Preguntas.
    for name, pcm in clips:
        if stop.is_set():
            return
        await asyncio.sleep(args.gap_s)
        turn = Turn(wav=name, speech_s=au.speech_end_s(pcm, INPUT_RATE))
        sess.turns.append(turn)
        sess.current_turn = turn
        LOG.info("enviando %s (%.2f s de voz)", name, turn.speech_s)
        started, done = source.play(pcm)
        t_first_chunk_sent = await started
        # La primera trama sale al final de su período de captura.
        turn.t_send_start = t_first_chunk_sent - args.chunk_ms / 1000.0
        turn.t_speech_end = turn.t_send_start + turn.speech_s
        await done
        ok = await wait_agent_quiet(sess, turn.t_speech_end, args.idle_s,
                                    args.response_timeout_s, stop)
        if not ok and not stop.is_set():
            LOG.warning("sin respuesta de audio en %.0f s para %s", args.response_timeout_s, name)

    if not stop.is_set():
        await asyncio.sleep(args.gap_s)


async def hard_limit(seconds: float, stop: asyncio.Event) -> None:
    try:
        await asyncio.wait_for(stop.wait(), timeout=seconds)
    except TimeoutError:
        LOG.warning("límite duro de %.0f s alcanzado: cerrando sesión", seconds)


# ---------------------------------------------------------------- principal

def build_dynamic_variables(args) -> dict[str, str]:
    dyn: dict[str, str] = {}
    if args.saludos:
        lines = [l.strip() for l in Path(args.saludos).read_text(encoding="utf-8").splitlines()]
        lines = [l for l in lines if l and not l.startswith("#")]
        if not lines:
            raise SystemExit(f"{args.saludos}: no contiene saludos")
        dyn["saludo"] = random.choice(lines)
    for item in args.var or []:
        key, sep, value = item.partition("=")
        if not sep or not key:
            raise SystemExit(f"--var debe ser clave=valor: {item!r}")
        dyn[key] = value
    return dyn


async def run(args) -> int:
    clips = [(Path(p).name, au.read_wav_pcm16_mono(p, INPUT_RATE)) for p in args.wav]
    dyn = build_dynamic_variables(args)
    out_dir = Path(args.out) / time.strftime("session-%Y%m%d-%H%M%S")
    out_dir.mkdir(parents=True, exist_ok=True)

    sess = Session()
    if args.ws_url:
        url = args.ws_url
    elif args.public:
        url = public_ws_url(args.base_url, args.agent_id)
    else:
        url = await asyncio.to_thread(get_signed_url, args.base_url, args.agent_id, args.api_key)
        sess.t_signed_url = time.monotonic()
    LOG.info("conectando a %s", redact(url))

    stop = asyncio.Event()
    metadata_ready = asyncio.Event()
    source = RealtimeSource(INPUT_RATE, args.chunk_ms)

    with open(out_dir / "events.jsonl", "w", encoding="utf-8") as events_file:
        async with connect(url, open_timeout=15, max_size=2**23) as ws:
            sess.t_ws_open = time.monotonic()
            init = {"type": "conversation_initiation_client_data"}
            if dyn:
                init["dynamic_variables"] = dyn
                LOG.info("variables dinámicas: %s", dyn)
            await ws.send(json.dumps(init))

            recv_task = asyncio.create_task(receiver(ws, sess, events_file, stop, metadata_ready))
            try:
                await asyncio.wait_for(metadata_ready.wait(), timeout=15)
            except TimeoutError:
                sess.errors.append("sin conversation_initiation_metadata en 15 s")
                stop.set()
            if sess.output_format and not stop.is_set():
                try:
                    sess.output_rate = au.parse_pcm_format(sess.output_format)
                    if au.parse_pcm_format(sess.input_format or "") != INPUT_RATE:
                        raise au.AudioFormatError(
                            f"el agente espera entrada {sess.input_format}; "
                            f"esta prueba y el terminal envían pcm_{INPUT_RATE}")
                except au.AudioFormatError as exc:
                    sess.errors.append(str(exc))
                    LOG.error("%s. Cambia el formato en la configuración del agente.", exc)
                    stop.set()

            send_task = asyncio.create_task(sender(ws, source, args.chunk_ms, stop))
            script_task = asyncio.create_task(script(args, sess, source, clips, stop))
            limit_task = asyncio.create_task(hard_limit(args.max_seconds, stop))
            await asyncio.wait({script_task, limit_task, recv_task},
                               return_when=asyncio.FIRST_COMPLETED)
            stop.set()
            for task in (script_task, limit_task, send_task):
                task.cancel()
            await ws.close(code=1000, reason="fin de prueba")
            await recv_task

    t_end = time.monotonic()
    return write_outputs(args, sess, out_dir, t_end)


def write_outputs(args, sess: Session, out_dir: Path, t_end: float) -> int:
    if sess.agent_pcm and sess.output_rate:
        au.write_wav_pcm16_mono(out_dir / "danny.wav", bytes(sess.agent_pcm), sess.output_rate)
    duration = t_end - sess.t0
    summary = {
        "conversation_id": sess.conversation_id,
        "formats": {"input": sess.input_format, "output": sess.output_format},
        "duration_s": round(duration, 2),
        "estimated_cost_usd": round(duration / 60 * args.cost_per_min, 4),
        "cost_per_min_assumed": args.cost_per_min,
        "timeline_s": {
            "signed_url": sess.rel(sess.t_signed_url),
            "ws_open": sess.rel(sess.t_ws_open),
            "metadata": sess.rel(sess.t_metadata),
            "greeting_first_audio": sess.rel(sess.t_greeting_first_audio),
        },
        "greeting_text": sess.greeting_text,
        "turns": [
            {**{k: v for k, v in asdict(t).items() if not k.startswith("t_")},
             "latency_from_speech_end": t.latencies_ms()}
            for t in sess.turns
        ],
        "ping_ms": {
            "n": len(sess.ping_ms),
            "min": min(sess.ping_ms, default=None),
            "max": max(sess.ping_ms, default=None),
        },
        "event_counts": sess.event_counts,
        "close": {"code": sess.close_code, "reason": sess.close_reason,
                  "by_server": sess.closed_by_server},
        "errors": sess.errors,
    }
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False),
                                          encoding="utf-8")
    print_summary(summary, out_dir)
    ok = sess.conversation_id is not None and not sess.errors and all(
        t.t_first_audio is not None for t in sess.turns)
    return 0 if ok else 1


def print_summary(s: dict, out_dir: Path) -> None:
    print("\n========== RESUMEN ==========")
    print(f"conversation_id : {s['conversation_id']}")
    print(f"formatos        : entrada={s['formats']['input']} salida={s['formats']['output']}")
    print(f"duración        : {s['duration_s']} s  (~US${s['estimated_cost_usd']} "
          f"a {s['cost_per_min_assumed']}/min, estimación)")
    tl = s["timeline_s"]
    print(f"línea de tiempo : signed_url={tl['signed_url']} ws_open={tl['ws_open']} "
          f"metadata={tl['metadata']} saludo_audio={tl['greeting_first_audio']} (s)")
    if s["greeting_text"]:
        print(f"saludo          : {s['greeting_text']}")
    for i, t in enumerate(s["turns"], 1):
        lat = t["latency_from_speech_end"]
        print(f"--- turno {i}: {t['wav']}")
        print(f"  oído    : {t['user_transcript'] or '(sin transcripción)'}")
        print(f"  Danny   : {t['agent_response'] or '(sin texto)'}")
        print(f"  latencia desde fin de voz: transcripción={lat['transcript_ms']} ms, "
              f"texto={lat['agent_text_ms']} ms, PRIMER AUDIO={lat['first_audio_ms']} ms")
        print(f"  audio respuesta: {t['response_audio_s']:.1f} s"
              + ("  [INTERRUMPIDO]" if t["interrupted"] else ""))
    print(f"ping_ms         : {s['ping_ms']}")
    print(f"eventos         : {s['event_counts']}")
    print(f"cierre          : {s['close']}")
    if s["errors"]:
        print(f"ERRORES         : {s['errors']}")
    print(f"archivos        : {out_dir}/ (danny.wav, events.jsonl, summary.json)")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--wav", action="append", default=[],
                   help="pregunta en WAV PCM 16 bit mono 16 kHz (repetible, en orden)")
    p.add_argument("--agent-id", default=os.environ.get("ELEVENLABS_AGENT_ID"))
    p.add_argument("--base-url", default=os.environ.get("ELEVENLABS_BASE_URL", DEFAULT_BASE_URL),
                   help="p. ej. https://api.us.elevenlabs.io para comparar regiones")
    p.add_argument("--public", action="store_true",
                   help="agente público: conectar sólo con agent_id (sin API key)")
    p.add_argument("--ws-url", help=argparse.SUPPRESS)  # pruebas con servidor simulado
    p.add_argument("--saludos", default=str(DEFAULT_SALUDOS) if DEFAULT_SALUDOS.is_file() else None,
                   help="archivo con un saludo por línea -> variable {{saludo}} "
                        "(defecto: personas/danny/saludos.txt)")
    p.add_argument("--var", action="append", help="variable dinámica clave=valor (repetible)")
    p.add_argument("--chunk-ms", type=int, default=100, help="tamaño de trama enviada (ms)")
    p.add_argument("--idle-s", type=float, default=0.8,
                   help="pausa tras terminar de reproducir a Danny antes de la siguiente pregunta")
    p.add_argument("--gap-s", type=float, default=1.0, help="pausa antes de cada pregunta")
    p.add_argument("--greeting-timeout-s", type=float, default=10.0)
    p.add_argument("--response-timeout-s", type=float, default=20.0)
    p.add_argument("--max-seconds", type=float, default=90.0,
                   help="límite duro de la sesión (control de costos)")
    p.add_argument("--cost-per-min", type=float,
                   default=float(os.environ.get("ELEVENLABS_COST_PER_MIN", "0.08")))
    p.add_argument("--out", default="out")
    p.add_argument("-v", "--verbose", action="store_true")
    args = p.parse_args(argv)
    args.api_key = os.environ.get("ELEVENLABS_API_KEY")
    if not args.ws_url:
        if not args.agent_id:
            p.error("falta ELEVENLABS_AGENT_ID (en .env) o --agent-id")
        if not args.public and not args.api_key:
            p.error("falta ELEVENLABS_API_KEY en .env (o usa --public con agente público)")
    return args


def main(argv: list[str] | None = None) -> int:
    load_dotenv(Path(__file__).with_name(".env"))
    args = parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s.%(msecs)03d %(levelname)-7s %(message)s",
                        datefmt="%H:%M:%S")
    logging.getLogger("websockets").setLevel(logging.INFO if args.verbose else logging.WARNING)
    try:
        return asyncio.run(run(args))
    except au.AudioFormatError as exc:
        LOG.error("%s", exc)
        return 2
    except (RuntimeError, OSError, WebSocketException) as exc:
        LOG.error("%s: %s", type(exc).__name__, exc)
        return 3
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
