from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .analysis import issue_fingerprint
from .utils import dump_json, load_json, now_iso


def pick_notification_candidates(items: list[dict[str, Any]], state: dict[str, Any]) -> list[dict[str, Any]]:
    notified = state.get("issues", {})
    candidates = []
    for item in sorted(items, key=lambda issue: issue.get("recommend_score", 0), reverse=True):
        if not item.get("should_notify"):
            continue
        key = f"{item['repository']}#{item['number']}"
        fingerprint = issue_fingerprint(item)
        previous = notified.get(key)
        if previous and previous.get("fingerprint") == fingerprint:
            continue
        candidates.append(item)
    return candidates


def render_email(candidates: list[dict[str, Any]]) -> tuple[str, str]:
    subject = f"[issue-radar] 发现 {len(candidates)} 个适合你的 issue"
    lines = [
        "issue-radar 发现以下 issue 值得关注：",
        "",
    ]
    for item in candidates:
        lines.extend(
            [
                f"- {item['repository']} #{item['number']} {item['title']}",
                f"  链接: {item['html_url']}",
                f"  推荐指数: {item['recommend_score']}",
                f"  认领状态: {item['claim_state']}",
                f"  难度: {item['difficulty']}",
                f"  类别: {item['category']}",
                f"  适配度: {item['fit_for_user']}",
                f"  认领依据: {item['claim_reason']}",
                f"  适合原因: {item['fit_reason']}",
                f"  推荐原因: {item['recommend_reason']}",
            ]
        )
        if item["claim_state"] == "maybe_claimed":
            lines.append("  提醒: 存在非机器人评论，建议先人工确认是否已有人跟进。")
        lines.append("")
    return subject, "\n".join(lines).strip() + "\n"


def update_state(state: dict[str, Any], candidates: list[dict[str, Any]]) -> dict[str, Any]:
    updated = dict(state)
    issues = dict(updated.get("issues", {}))
    sent_at = now_iso()
    for item in candidates:
        key = f"{item['repository']}#{item['number']}"
        issues[key] = {
            "fingerprint": issue_fingerprint(item),
            "sent_at": sent_at,
            "recommend_score": item["recommend_score"],
            "updated_at": item.get("updated_at"),
        }
    updated["issues"] = issues
    return updated


def write_github_output(path: str | None, outputs: dict[str, str]) -> None:
    if not path:
        return
    output_path = Path(path)
    chunks = []
    for key, value in outputs.items():
        chunks.append(f"{key}<<__ISSUE_RADAR__\n{value}\n__ISSUE_RADAR__\n")
    output_path.write_text("".join(chunks), encoding="utf-8")


def write_notification_files(output_dir: Path, payload: dict[str, Any]) -> None:
    dump_json(output_dir / "notification.json", payload)
    (output_dir / "email_subject.txt").write_text(payload["subject"], encoding="utf-8")
    (output_dir / "email_body.txt").write_text(payload["body"], encoding="utf-8")


def load_state_file(path: Path) -> dict[str, Any]:
    return load_json(path, default={})
