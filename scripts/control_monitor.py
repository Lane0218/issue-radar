#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from issue_radar.monitor_control import (
    DEFAULT_MONITOR_CONTROL_FILE,
    RUNNING,
    VALID_STATUSES,
    default_monitor_control,
    load_monitor_control,
    pause_monitor,
    resume_monitor,
    write_monitor_github_output,
)
from issue_radar.utils import setup_logging


def _run_git(*args: str, capture_output: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=ROOT,
        check=True,
        text=True,
        capture_output=capture_output,
    )


def _git_stdout(*args: str) -> str:
    return _run_git(*args).stdout.strip()


def _ensure_sync_target(state_path: Path) -> Path:
    resolved = state_path.resolve()
    default_path = DEFAULT_MONITOR_CONTROL_FILE.resolve()
    if resolved != default_path:
        raise RuntimeError(
            f"Automatic git sync only supports the default state file: {default_path}"
        )
    return resolved


def _normalize_control(payload: object) -> dict[str, object]:
    if not isinstance(payload, dict):
        return default_monitor_control()

    control = default_monitor_control() | payload
    status = str(control.get("status") or RUNNING).strip().lower()
    if status not in VALID_STATUSES:
        status = RUNNING
    control["status"] = status
    control.pop("reason", None)
    return control


def _relative_to_root(path: Path) -> str:
    return str(path.resolve().relative_to(ROOT))


def _tracked_branch() -> tuple[str, str]:
    upstream = _git_stdout("rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}")
    remote, branch = upstream.split("/", 1)
    return remote, branch


def _ahead_behind(upstream: str) -> tuple[int, int]:
    counts = _git_stdout("rev-list", "--left-right", "--count", f"HEAD...{upstream}")
    ahead_text, behind_text = counts.split()
    return int(ahead_text), int(behind_text)


def _sync_with_remote(logger, state_path: Path) -> tuple[str, str, str]:
    resolved = _ensure_sync_target(state_path)
    state_rel = _relative_to_root(resolved)
    remote, branch = _tracked_branch()
    upstream = f"{remote}/{branch}"

    logger.info("Fetching latest changes from %s", upstream)
    _run_git("fetch", remote, branch, capture_output=False)

    ahead, _ = _ahead_behind(upstream)
    if ahead:
        raise RuntimeError(
            f"Local branch has {ahead} unpushed commit(s); refusing to push only {state_rel}."
        )

    logger.info("Rebasing current branch onto %s", upstream)
    _run_git("pull", "--rebase", "--autostash", remote, branch, capture_output=False)
    return remote, branch, state_rel


def _commit_and_push_state(logger, *, state_rel: str, remote: str, branch: str, command: str) -> None:
    status = _git_stdout("status", "--short", "--", state_rel)
    if not status:
        logger.info("No changes detected for %s; skipping commit and push", state_rel)
        return

    commit_message = f"chore: {command} issue-radar monitor"
    logger.info("Committing %s", state_rel)
    _run_git("add", "--", state_rel, capture_output=False)
    _run_git("commit", "--only", "-m", commit_message, "--", state_rel, capture_output=False)
    logger.info("Pushing %s to %s/%s", state_rel, remote, branch)
    _run_git("push", remote, f"HEAD:{branch}", capture_output=False)


def _load_remote_control(logger, state_path: Path) -> dict[str, object]:
    resolved = _ensure_sync_target(state_path)
    state_rel = _relative_to_root(resolved)
    remote, branch = _tracked_branch()
    upstream = f"{remote}/{branch}"
    logger.info("Fetching latest status from %s", upstream)
    _run_git("fetch", remote, branch, capture_output=False)
    raw = _git_stdout("show", f"{upstream}:{state_rel}")
    return _normalize_control(json.loads(raw))


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
    status_scope = status_parser.add_mutually_exclusive_group()
    status_scope.add_argument("--local", action="store_true", help="Read status from the local working tree.")
    status_scope.add_argument("--remote", action="store_true", help="Read status from the tracked remote branch.")
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
        remote, branch, state_rel = _sync_with_remote(logger, args.state)
        control = pause_monitor(args.state)
        _commit_and_push_state(
            logger,
            state_rel=state_rel,
            remote=remote,
            branch=branch,
            command="pause",
        )
        logger.info("Monitoring paused and synced. state=%s", args.state)
        _print_status(control, as_json=False)
        return 0

    if args.command == "resume":
        remote, branch, state_rel = _sync_with_remote(logger, args.state)
        control = resume_monitor(args.state)
        _commit_and_push_state(
            logger,
            state_rel=state_rel,
            remote=remote,
            branch=branch,
            command="resume",
        )
        logger.info("Monitoring resumed and synced. state=%s", args.state)
        _print_status(control, as_json=False)
        return 0

    output_path = args.github_output if args.github_output is not None else os.environ.get("GITHUB_OUTPUT")
    if args.local:
        control = load_monitor_control(args.state)
        logger.info("Loaded local monitoring status=%s from %s", control["status"], args.state)
    elif args.remote or not output_path:
        control = _load_remote_control(logger, args.state)
        logger.info("Loaded remote monitoring status=%s", control["status"])
    else:
        control = load_monitor_control(args.state)
        logger.info("Loaded local monitoring status=%s from %s", control["status"], args.state)

    write_monitor_github_output(output_path, control)
    _print_status(control, as_json=args.json)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
