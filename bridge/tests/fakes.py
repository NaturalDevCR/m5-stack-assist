"""Dobles de prueba: servidor tipo ElevenLabs y terminal en memoria."""

from __future__ import annotations

import asyncio
import base64
import json
import math
import struct

from websockets.asyncio.server import serve

from voice_agent_bridge.terminals import Terminal

RATE = 16000


def tone(ms: int, freq: float = 440.0, amp: float = 0.3) -> bytes:
    n = RATE * ms // 1000
    return b"".join(struct.pack("<h", int(amp * 32767 * math.sin(2 * math.pi * freq * i / RATE)))
                    for i in range(n))


def is_silent(pcm: bytes) -> bool:
    return not any(pcm)


def loud(pcm: bytes) -> bool:
    samples = struct.unpack(f"<{len(pcm) // 2}h", pcm)
    return max((abs(s) for s in samples), default=0) > 2000


class FakeAgent:
    """Imita la forma de los mensajes de ElevenLabs Agents (no su comportamiento real)."""

    def __init__(self, greeting_ms: int = 400, reply_ms: int = 300):
        self.greeting_ms = greeting_ms
        self.reply_ms = reply_ms
        self.init_messages: list[dict] = []
        self.received_chunks: list[bytes] = []
        self.user_messages: list[str] = []
        self.connections = 0

    async def _speak(self, ws, text: str, ms: int) -> None:
        await ws.send(json.dumps({"type": "agent_response",
                                  "agent_response_event": {"agent_response": text}}))
        pcm = tone(ms)
        for i in range(0, len(pcm), 3200):
            await ws.send(json.dumps({"type": "audio", "audio_event": {
                "audio_base_64": base64.b64encode(pcm[i:i + 3200]).decode()}}))

    async def handler(self, ws) -> None:
        self.connections += 1
        self.init_messages.append(json.loads(await ws.recv()))
        await ws.send(json.dumps({"type": "conversation_initiation_metadata",
                                  "conversation_initiation_metadata_event": {
                                      "conversation_id": f"conv_fake_{self.connections}",
                                      "agent_output_audio_format": "pcm_16000",
                                      "user_input_audio_format": "pcm_16000"}}))
        await ws.send(json.dumps({"type": "ping", "ping_event": {"event_id": 1, "ping_ms": 10}}))
        saludo = (self.init_messages[-1].get("dynamic_variables") or {}).get("saludo", "")
        await self._speak(ws, saludo, self.greeting_ms)
        voiced, silent = False, 0
        async for raw in ws:
            msg = json.loads(raw)
            if msg.get("type") == "user_message":
                self.user_messages.append(msg["text"])
                await self._speak(ws, "Buenas noches.", 200)
                await asyncio.sleep(0.05)
                await ws.close(code=1000)  # como end_call
                return
            if "user_audio_chunk" not in msg:
                continue
            pcm = base64.b64decode(msg["user_audio_chunk"])
            self.received_chunks.append(pcm)
            if loud(pcm):
                voiced, silent = True, 0
            elif voiced:
                silent += 1
                if silent >= 2:
                    voiced = False
                    await ws.send(json.dumps({"type": "user_transcript",
                                              "user_transcription_event": {
                                                  "user_transcript": "¿hay fantasmas?"}}))
                    await self._speak(ws, "Muchos.", self.reply_ms)


async def start_fake_agent(agent: FakeAgent):
    server = await serve(agent.handler, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    return server, f"ws://127.0.0.1:{port}/v1/convai/conversation?agent_id=fake"


class FakeTerminal(Terminal):
    def __init__(self) -> None:
        super().__init__()
        self._connected = True
        self.played = bytearray()
        self.flushes = 0
        self.mic = False
        self.states: list[str] = []

    @property
    def connected(self) -> bool:
        return self._connected

    def disconnect(self) -> None:
        self._connected = False
        self.on_connection(False)

    async def play(self, pcm: bytes) -> None:
        self.played.extend(pcm)

    async def flush(self) -> None:
        self.flushes += 1

    async def set_mic(self, on: bool) -> None:
        self.mic = on

    async def set_state(self, state: str) -> None:
        self.states.append(state)

    async def stream(self, script: list[tuple[str, int]], stop: asyncio.Event) -> None:
        """Envía tramas de 20 ms en tiempo real: [('voice', ms), ('silence', ms)…] y luego silencio."""
        frame = RATE * 20 // 1000 * 2
        voice = tone(20, freq=300)
        loop = asyncio.get_running_loop()
        t = loop.time()
        queue = [kind for kind, ms in script for _ in range(ms // 20)]
        while not stop.is_set():
            kind = queue.pop(0) if queue else "silence"
            self.on_frame(voice if kind == "voice" else bytes(frame))
            t += 0.02
            await asyncio.sleep(max(0, t - loop.time()))
