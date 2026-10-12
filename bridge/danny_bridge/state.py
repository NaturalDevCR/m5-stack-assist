"""Estado que sobrevive reinicios: ajustes de HA y estadísticas del día."""

from __future__ import annotations

import json
import logging
import os
import tempfile
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

LOG = logging.getLogger(__name__)


@dataclass
class DayStats:
    date: str = ""
    sessions: int = 0
    seconds: float = 0.0
    cost_usd: float = 0.0


@dataclass
class PersistentState:
    enabled: bool = True
    overrides: dict[str, float] = field(default_factory=dict)  # max_session_s, idle_timeout_s, cooldown_s
    today: DayStats = field(default_factory=DayStats)


class StateStore:
    def __init__(self, path: str | Path, timezone: str = "America/Costa_Rica"):
        self.path = Path(path)
        self.tz = ZoneInfo(timezone)
        self.state = PersistentState()
        self._load()
        self.roll_day()

    def _load(self) -> None:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return
        except (OSError, json.JSONDecodeError) as exc:
            LOG.warning("no se pudo leer %s (%s); se usan valores por defecto", self.path, exc)
            return
        self.state = PersistentState(
            enabled=bool(data.get("enabled", True)),
            overrides={k: float(v) for k, v in (data.get("overrides") or {}).items()},
            today=DayStats(**(data.get("today") or {})),
        )

    def save(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=self.path.parent, prefix=".state-")
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(asdict(self.state), f, indent=2)
            os.replace(tmp, self.path)
        except OSError as exc:
            LOG.error("no se pudo guardar el estado en %s: %s", self.path, exc)

    def today_str(self) -> str:
        return datetime.now(self.tz).date().isoformat()

    def roll_day(self) -> None:
        today = self.today_str()
        if self.state.today.date != today:
            self.state.today = DayStats(date=today)
            self.save()

    def add_session(self, seconds: float, cost_usd: float) -> None:
        self.roll_day()
        t = self.state.today
        t.sessions += 1
        t.seconds += seconds
        t.cost_usd += cost_usd
        self.save()
