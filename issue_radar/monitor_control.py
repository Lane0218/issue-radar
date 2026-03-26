from __future__ import annotations

from pathlib import Path
from typing import Any

from .utils import dump_json, load_json, now_iso

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MONITOR_CONTROL_FILE = ROOT / "data" / "state" / "monitor_control.json"

RUNNING = "running"
PAUSED = "paused"
VALID_STATUSES = {RUNNING, PAUSED}


def default_monitor_control() -> dict[str, Any]:
    return {
        "status": RUNNING,
        "updated_at": None,
        "paused_at": None,
        "resumed_at": None,
    }


def load_monitor_control(path: Path = DEFAULT_MONITOR_CONTROL_FILE) -> dict[str, Any]:
    if not path.exists() or path.stat().st_size == 0:
        return default_monitor_control()

    payload = load_json(path, default=None)
    if not isinstance(payload, dict):
        return default_monitor_control()

    control = default_monitor_control() | payload
    status = str(control.get("status") or RUNNING).strip().lower()
    if status not in VALID_STATUSES:
        status = RUNNING
    control["status"] = status
    control.pop("reason", None)
    return control


def save_monitor_control(control: dict[str, Any], path: Path = DEFAULT_MONITOR_CONTROL_FILE) -> dict[str, Any]:
    normalized = default_monitor_control() | control
    status = str(normalized.get("status") or RUNNING).strip().lower()
    if status not in VALID_STATUSES:
        raise ValueError(f"Unsupported monitor status: {status}")
    normalized["status"] = status
    normalized.pop("reason", None)
    dump_json(path, normalized)
    return normalized


def pause_monitor(
    path: Path = DEFAULT_MONITOR_CONTROL_FILE,
) -> dict[str, Any]:
    current = load_monitor_control(path)
    timestamp = now_iso()
    updated = current | {
        "status": PAUSED,
        "updated_at": timestamp,
    }
    if current.get("status") != PAUSED or not current.get("paused_at"):
        updated["paused_at"] = timestamp
    return save_monitor_control(updated, path)


def resume_monitor(path: Path = DEFAULT_MONITOR_CONTROL_FILE) -> dict[str, Any]:
    current = load_monitor_control(path)
    timestamp = now_iso()
    updated = current | {
        "status": RUNNING,
        "updated_at": timestamp,
        "resumed_at": timestamp,
    }
    return save_monitor_control(updated, path)


def is_monitor_paused(control: dict[str, Any]) -> bool:
    return control.get("status") == PAUSED


def write_monitor_github_output(path: str | None, control: dict[str, Any]) -> None:
    if not path:
        return

    output_path = Path(path)
    outputs = {
        "status": str(control.get("status") or RUNNING),
        "should_run": "false" if is_monitor_paused(control) else "true",
    }
    chunks = []
    for key, value in outputs.items():
        chunks.append(f"{key}<<__ISSUE_RADAR__\n{value}\n__ISSUE_RADAR__\n")
    output_path.write_text("".join(chunks), encoding="utf-8")
