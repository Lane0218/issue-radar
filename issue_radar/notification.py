from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .analysis import issue_fingerprint, issue_sort_key
from .utils import dump_json, load_json, now_iso


BEIJING_TZ = timezone(timedelta(hours=8))
FIT_LABELS = {
    "good_fit": "很适合",
    "possible_fit": "可以尝试",
    "poor_fit": "不太适合",
}
CATEGORY_LABELS = {
    "compiler": "编译器",
    "mlir": "MLIR",
    "llvm": "LLVM",
    "frontend": "前端",
    "docs": "文档",
    "tests": "测试",
    "other": "其他",
}
DIFFICULTY_LABELS = {
    "low": "低",
    "medium_low": "中低",
    "unclear": "待确认",
    "too_hard": "偏难",
}


def pick_notification_candidates(items: list[dict[str, Any]], state: dict[str, Any]) -> list[dict[str, Any]]:
    notified = state.get("issues", {})
    candidates = []
    for item in sorted(items, key=issue_sort_key):
        if not item.get("should_notify"):
            continue
        key = f"{item['repository']}#{item['number']}"
        previous = notified.get(key)
        if previous and previous.get("updated_at") == item.get("updated_at"):
            continue
        fingerprint = issue_fingerprint(item)
        if previous and previous.get("fingerprint") == fingerprint:
            continue
        candidates.append(item)
    return candidates


def _render_created_at(created_at: str | None) -> str:
    if not created_at:
        return "未知"
    try:
        parsed = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
    except ValueError:
        return created_at
    return parsed.astimezone(BEIJING_TZ).strftime("%Y-%m-%d %H:%M")


def _render_fit(fit_for_user: str | None) -> str:
    return FIT_LABELS.get(str(fit_for_user or "").strip().lower(), fit_for_user or "未知")


def _render_difficulty(difficulty: str | None) -> str:
    return DIFFICULTY_LABELS.get(str(difficulty or "").strip().lower(), difficulty or "未知")


def _render_category(category: str | None) -> str:
    return CATEGORY_LABELS.get(str(category or "").strip().lower(), category or "未知")


def render_email(candidates: list[dict[str, Any]]) -> tuple[str, str]:
    subject = f"[issue-radar] 发现 {len(candidates)} 个值得关注的 issue"
    lines = [
        "issue-radar 为你筛到了以下值得关注的 issue。",
        "已按适配度、难度和创建时间排序：",
        "",
    ]
    for index, item in enumerate(candidates, start=1):
        meta_line = (
            f"适配度：{_render_fit(item.get('fit_for_user'))} | "
            f"难度：{_render_difficulty(item.get('difficulty'))} | "
            f"类别：{_render_category(item.get('category'))} | "
            f"创建时间：{_render_created_at(item.get('created_at'))}"
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
