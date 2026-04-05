#!/usr/bin/env python3

from __future__ import annotations

import argparse
import os
from pathlib import Path

from issue_radar.config import DEFAULT_ANALYZED_OUTPUT, DEFAULT_STATE_FILE
from issue_radar.notification import (
    load_state_file,
    pick_notification_candidates,
    render_email,
    update_state,
    write_github_output,
    write_notification_files,
)
from issue_radar.utils import dump_json, load_json, setup_logging


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Prepare notification content for high-score issues.")
    parser.add_argument("--input", type=Path, default=DEFAULT_ANALYZED_OUTPUT)
    parser.add_argument("--state", type=Path, default=DEFAULT_STATE_FILE)
    parser.add_argument("--output-dir", type=Path, default=Path("data/out"))
    return parser.parse_args()


def main() -> int:
    logger = setup_logging("notify")
    args = _parse_args()
    logger.info("Loading analyzed issues from %s", args.input)
    analyzed = load_json(args.input, default=[])
    logger.info("Loaded %s analyzed issues", len(analyzed))
    state = load_state_file(args.state)
    logger.info("Loaded notification state from %s", args.state)
    candidates = pick_notification_candidates(analyzed, state)
    logger.info("Found %s notification candidates", len(candidates))

    if not candidates:
        payload = {
            "should_send": False,
            "subject": "[issue-radar] 本轮没有新的高分 issue",
            "body": "本轮没有新的高分 issue 需要通知。\n",
            "html_body": "<!DOCTYPE html><html lang=\"zh-CN\"><body><p>本轮没有新的高分 issue 需要通知。</p></body></html>\n",
            "candidates": [],
        }
        write_notification_files(args.output_dir, payload)
        write_github_output(
            os.environ.get("GITHUB_OUTPUT"),
            {
                "should_send": "false",
                "subject": payload["subject"],
                "body": payload["body"],
                "html_body": payload["html_body"],
            },
        )
        logger.info("No new notification candidates. Wrote no-op notification payload to %s", args.output_dir)
        return 0

    subject, body, html_body = render_email(candidates)
    payload = {
        "should_send": True,
        "subject": subject,
        "body": body,
        "html_body": html_body,
        "candidates": candidates,
    }
    write_notification_files(args.output_dir, payload)
    write_github_output(
        os.environ.get("GITHUB_OUTPUT"),
        {
            "should_send": "true",
            "subject": subject,
            "body": body,
            "html_body": html_body,
        },
    )
    updated_state = update_state(state, candidates)
    dump_json(args.state, updated_state)
    logger.info("Prepared notification for %s issues and updated %s", len(candidates), args.state)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
