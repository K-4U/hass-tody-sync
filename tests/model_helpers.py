"""Shared helpers for model tests: import model.py without the HA package, load the fixture."""

from __future__ import annotations

import importlib.util
import json
import re
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
MODEL_PATH = ROOT / "custom_components" / "tody" / "model.py"
FIXTURE_PATH = ROOT / "tests" / "fixtures" / "snapshot_2026-10-07.json"

FIXTURE_NOW = datetime(2026, 10, 7, 13, 53, tzinfo=UTC)
AMS = ZoneInfo("Europe/Amsterdam")

_ISO_Z = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?Z$")


def load_model():
    """Load model.py directly, so custom_components/tody/__init__.py (HA imports) is never run."""
    name = "tody_model_under_test"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, MODEL_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _convert(value: Any) -> Any:
    if isinstance(value, str) and _ISO_Z.match(value):
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)
    if isinstance(value, list):
        return [_convert(v) for v in value]
    if isinstance(value, dict):
        return {k: _convert(v) for k, v in value.items()}
    return value


def load_fixture(path: Path = FIXTURE_PATH) -> dict[str, list[dict[str, Any]]]:
    """Fixture in fetch_snapshot() shape, ISO-Z strings converted to aware UTC datetimes."""
    return _convert(json.loads(path.read_text()))


def dt(text: str) -> datetime:
    """Aware UTC datetime from 'YYYY-MM-DDTHH:MM' (or with seconds)."""
    return datetime.fromisoformat(text).replace(tzinfo=UTC)


def due_table(now: datetime = FIXTURE_NOW, tz=AMS) -> str:
    model = load_model()
    data = model.parse_snapshot(load_fixture(), now=now, tz=tz)
    rows = []
    for t in sorted(data.tasks.values(), key=lambda t: (t.due is None, t.due, t.name)):
        area = data.areas[t.area_id].name if t.area_id in data.areas else "-"
        turn = ", ".join(sorted(data.participants[p].name for p in t.turn_participant_ids))
        last = t.last_done.strftime("%Y-%m-%d %H:%M") if t.last_done else "-"
        by = data.participants[t.last_done_by].name if t.last_done_by in data.participants else "-"
        rows.append((t.name, area, str(t.due), "yes" if t.paused else "", "yes" if t.archived else "", last, by, turn))
    header = ("Task", "Area", "Due", "Paused", "Archived", "Last done (local)", "By", "Turn")
    widths = [max(len(r[i]) for r in [header, *rows]) for i in range(len(header))]
    fmt = " | ".join(f"{{:{w}}}" for w in widths)
    lines = [fmt.format(*header), "-+-".join("-" * w for w in widths)]
    lines += [fmt.format(*r) for r in rows]
    lines.append(f"sync={data.sync_name!r} on_vacation={data.on_vacation} now={now.isoformat()} tz={tz}")
    return "\n".join(lines)
