from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .analysis import issue_fingerprint
from .utils import dump_json, load_json, now_iso


BEIJING_TZ = timezone(timedelta(hours=8))


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


def _render_claim_state(claim_state: str) -> str:
    mapping = {
        "open": "空闲",
        "claimed": "已认领",
    }
    return mapping.get(claim_state, claim_state)


def _render_created_at(created_at: str | None) -> str:
    if not created_at:
        return "未知"
    try:
        parsed = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
    except ValueError:
        return created_at
    return parsed.astimezone(BEIJING_TZ).strftime("%Y-%m-%d %H:%M 北京时间")


def render_email(candidates: list[dict[str, Any]]) -> tuple[str, str]:
    subject = f"[issue-radar] 发现 {len(candidates)} 个值得关注的 issue"
    lines = [
        "issue-radar 为你筛到了以下值得关注的 issue。",
        "已按推荐指数从高到低排序：",
        "",
    ]
    for index, item in enumerate(candidates, start=1):
        meta_line = (
            f"推荐指数：{item['recommend_score']} | "
            f"创建时间：{_render_created_at(item.get('created_at'))} | "
            f"当前状态：{_render_claim_state(item['claim_state'])}"
        )
        lines.extend(
            [
                f"[{index}] {item['repository']} #{item['number']}",
                f"标题：{item['title']}",
                meta_line,
                f"链接：{item['html_url']}",
                f"问题简介：{item.get('issue_summary_zh', '') or '暂无简介'}",
                f"建议工作：{item.get('work_needed_zh', '') or '暂无建议'}",
                "-" * 64,
            ]
        )
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
