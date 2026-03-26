#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from issue_radar.monitor_control import (
    DEFAULT_MONITOR_CONTROL_FILE,
    load_monitor_control,
    pause_monitor,
    resume_monitor,
    write_monitor_github_output,
)
from issue_radar.utils import setup_logging


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Pause, resume, or inspect issue-radar monitoring.")
    parser.add_argument("--state", type=Path, default=DEFAULT_MONITOR_CONTROL_FILE)

    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("pause", help="Pause monitoring.")

    subparsers.add_parser("resume", help="Resume monitoring.")

    status_parser = subparsers.add_parser("status", help="Show current monitoring status.")
    status_parser.add_argument(
        "--github-output",
        default=None,
        help="Explicit GitHub Actions output file path. Defaults to $GITHUB_OUTPUT when omitted.",
    )
    status_parser.add_argument("--json", action="store_true", help="Print status as JSON.")
    return parser.parse_args()


def _print_status(control: dict[str, object], *, as_json: bool) -> None:
    if as_json:
        print(json.dumps(control, ensure_ascii=False, indent=2))
        return

    status = control["status"]
    updated_at = control.get("updated_at") or "-"
    paused_at = control.get("paused_at") or "-"
    resumed_at = control.get("resumed_at") or "-"
    print(f"status={status}")
    print(f"updated_at={updated_at}")
    print(f"paused_at={paused_at}")
    print(f"resumed_at={resumed_at}")


def main() -> int:
    logger = setup_logging("control_monitor")
    args = _parse_args()

    if args.command == "pause":
        control = pause_monitor(args.state)
        logger.info("Monitoring paused. state=%s", args.state)
        _print_status(control, as_json=False)
        return 0

    if args.command == "resume":
        control = resume_monitor(args.state)
        logger.info("Monitoring resumed. state=%s", args.state)
        _print_status(control, as_json=False)
        return 0

    control = load_monitor_control(args.state)
    output_path = args.github_output if args.github_output is not None else os.environ.get("GITHUB_OUTPUT")
    write_monitor_github_output(output_path, control)
    logger.info("Loaded monitoring status=%s from %s", control["status"], args.state)
    _print_status(control, as_json=args.json)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
