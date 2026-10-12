"""CLI del puente.

  danny-bridge run   [--config FILE] [--terminal local] [--enter]
  danny-bridge check [--config FILE]
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import logging
import signal
import sys
import threading
from pathlib import Path

from . import __version__
from .config import ConfigError, load_config, load_dotenv, validate
from .elevenlabs import AgentError, get_signed_url
from .session import SessionManager
from .state import StateStore
from .terminals import create_terminal

LOG = logging.getLogger("danny_bridge")
DEFAULT_CONFIG = "/etc/danny-bridge/config.yaml"
DEFAULT_ENV = "/etc/danny-bridge/danny-bridge.env"


def _setup_logging(level: str) -> None:
    logging.basicConfig(level=getattr(logging, level.upper(), logging.INFO),
                        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
                        datefmt="%Y-%m-%d %H:%M:%S")
    logging.getLogger("websockets").setLevel(logging.WARNING)


def _load(args):
    load_dotenv(Path(args.env))
    cfg = load_config(args.config)
    if getattr(args, "terminal", None):
        cfg.terminal.type = args.terminal
    return cfg


async def _run(cfg, enter: bool) -> int:
    store = StateStore(cfg.state_file, cfg.timezone)
    terminal = create_terminal(cfg)
    manager = SessionManager(cfg, terminal, store)
    tasks: list[asyncio.Task] = []
    await terminal.start()
    if cfg.mqtt.enabled:
        from .ha_mqtt import HomeAssistantMqtt
        tasks.append(asyncio.create_task(HomeAssistantMqtt(cfg.mqtt, manager).run(), name="mqtt"))
    if enter:
        tasks.append(asyncio.create_task(_enter_loop(manager), name="enter"))

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        with contextlib.suppress(NotImplementedError):
            loop.add_signal_handler(sig, stop.set)
    LOG.info("danny-bridge %s listo (terminal=%s, mqtt=%s)", __version__, cfg.terminal.type,
             "sí" if cfg.mqtt.enabled else "no")
    await stop.wait()
    LOG.info("deteniendo…")
    await manager.shutdown()
    for t in tasks:
        t.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)
    await terminal.stop()
    return 0


async def _enter_loop(manager: SessionManager) -> None:
    """Modo de prueba: Enter inicia la conversación o pide la despedida."""
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue[str] = asyncio.Queue()

    def reader() -> None:
        for line in sys.stdin:
            loop.call_soon_threadsafe(queue.put_nowait, line)

    threading.Thread(target=reader, daemon=True).start()
    print("\n>>> Pulsa Enter para hablar con Danny (Enter de nuevo para que se despida). Ctrl+C para salir.\n")
    while True:
        await queue.get()
        if manager.status.value in ("listening", "speaking", "farewell"):
            await manager.end("enter")
        else:
            await manager.start("enter", force=True)


def cmd_check(args) -> int:
    cfg = _load(args)
    problems = validate(cfg)
    for p in problems:
        print(f"✗ {p}")
    if problems:
        return 1
    print("✓ configuración válida")
    if cfg.elevenlabs.ws_url:
        return 0
    try:
        get_signed_url(cfg.elevenlabs.base_url, cfg.elevenlabs.agent_id, cfg.elevenlabs.api_key)
    except AgentError as exc:
        print(f"✗ ElevenLabs: {exc}")
        return 1
    print("✓ ElevenLabs: signed URL obtenida (clave y agente correctos)")
    return 0


def cmd_run(args) -> int:
    cfg = _load(args)
    _setup_logging(cfg.log_level)
    problems = validate(cfg)
    if problems:
        for p in problems:
            LOG.error("configuración: %s", p)
        return 1
    return asyncio.run(_run(cfg, args.enter))


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="danny-bridge", description=__doc__.splitlines()[0])
    p.add_argument("--version", action="version", version=__version__)
    sub = p.add_subparsers(dest="cmd", required=True)
    for name in ("run", "check"):
        sp = sub.add_parser(name)
        sp.add_argument("--config", default=DEFAULT_CONFIG)
        sp.add_argument("--env", default=DEFAULT_ENV, help="archivo de secretos KEY=VALUE")
    run = sub.choices["run"]
    run.add_argument("--terminal", choices=["websocket", "local"])
    run.add_argument("--enter", action="store_true",
                     help="Enter inicia/termina la conversación (pruebas locales)")
    args = p.parse_args(argv)
    try:
        return cmd_run(args) if args.cmd == "run" else cmd_check(args)
    except ConfigError as exc:
        print(f"✗ {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
