"""Servidor WebSocket que imita el protocolo de ElevenLabs Agents (subconjunto).

Sólo para pruebas offline de el_spike.py: no reproduce el comportamiento real
de ElevenLabs (VAD, LLM, voz), sólo la forma de los mensajes documentados.
"""

from __future__ import annotations

import asyncio
import base64
import json
import math
import struct

from websockets.asyncio.server import serve

import audio_utils as au

RATE = 16000


def tone(ms: int, freq: float = 440.0, amp: float = 0.3) -> bytes:
    n = RATE * ms // 1000
    return b"".join(struct.pack("<h", int(amp * 32767 * math.sin(2 * math.pi * freq * i / RATE)))
                    for i in range(n))


class FakeAgent:
    def __init__(self, input_format: str = "pcm_16000", greeting: bool = True):
        self.input_format = input_format
        self.greeting = greeting
        self.init_message: dict | None = None
        self.pongs: list[int] = []
        self.audio_chunks = 0

    async def send_speech(self, ws, text: str, event_id: int) -> None:
        await ws.send(json.dumps({"type": "agent_response",
                                  "agent_response_event": {"agent_response": text,
                                                           "event_id": event_id}}))
        pcm = tone(300)
        for i in range(0, len(pcm), 3200):
            await ws.send(json.dumps({"type": "audio", "audio_event": {
                "audio_base_64": base64.b64encode(pcm[i:i + 3200]).decode(),
                "event_id": event_id}}))

    async def handler(self, ws) -> None:
        self.init_message = json.loads(await ws.recv())
        await ws.send(json.dumps({
            "type": "conversation_initiation_metadata",
            "conversation_initiation_metadata_event": {
                "conversation_id": "conv_fake_123",
                "agent_output_audio_format": "pcm_16000",
                "user_input_audio_format": self.input_format,
            }}))
        await ws.send(json.dumps({"type": "ping", "ping_event": {"event_id": 1, "ping_ms": 42}}))
        if self.greeting:
            saludo = (self.init_message.get("dynamic_variables") or {}).get("saludo", "Bienvenidos.")
            await self.send_speech(ws, saludo, 2)

        voiced = False
        silent_after_voice = 0
        event_id = 10
        async for raw in ws:
            msg = json.loads(raw)
            if msg.get("type") == "pong":
                self.pongs.append(msg["event_id"])
                continue
            if "user_audio_chunk" not in msg:
                continue
            self.audio_chunks += 1
            pcm = base64.b64decode(msg["user_audio_chunk"])
            if au.rms_dbfs(pcm) > -40:
                voiced, silent_after_voice = True, 0
            elif voiced:
                silent_after_voice += 1
                if silent_after_voice >= 3:  # "fin de turno" simplificado
                    voiced = False
                    event_id += 1
                    await ws.send(json.dumps({"type": "user_transcript",
                                              "user_transcription_event": {
                                                  "user_transcript": "¿hay fantasmas?",
                                                  "event_id": event_id}}))
                    await self.send_speech(ws, "Sólo los que pagan su cuenta.", event_id)


async def start(agent: FakeAgent):
    server = await serve(agent.handler, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    return server, f"ws://127.0.0.1:{port}/v1/convai/conversation?agent_id=fake"


if __name__ == "__main__":  # servidor manual: python tests/fake_agent_server.py
    async def _main():
        server, url = await start(FakeAgent())
        print("escuchando en", url)
        await asyncio.Future()
    asyncio.run(_main())
