from __future__ import annotations

from html import escape
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .analysis import issue_fingerprint, issue_sort_key
from .utils import dump_json, load_json, now_iso


BEIJING_TZ = timezone(timedelta(hours=8))


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


def _normalize_labels(labels: Any) -> list[str]:
    if not isinstance(labels, list):
        return []
    normalized = []
    for item in labels:
        value = str(item).strip()
        if value:
            normalized.append(value)
    return normalized


def _render_labels_text(labels: Any) -> str:
    normalized = _normalize_labels(labels)
    if not normalized:
        return "暂无 Label"
    return ", ".join(normalized)


def _render_labels_html(labels: Any) -> str:
    normalized = _normalize_labels(labels)
    if not normalized:
        return '<span class="issue-card__tag issue-card__tag--empty">暂无 Label</span>'
    return "".join(f'<span class="issue-card__tag">{escape(label)}</span>' for label in normalized)


def render_email_text(candidates: list[dict[str, Any]]) -> str:
    lines = [
        "issue-radar 为你筛到了以下值得关注的 issue。",
        "以下是本轮推荐结果：",
        "",
    ]
    for index, item in enumerate(candidates, start=1):
        lines.extend(
            [
                f"[{index}] {item['repository']} #{item['number']}",
                f"标题：{item['title']}",
                f"创建时间：{_render_created_at(item.get('created_at'))}",
                f"Label：{_render_labels_text(item.get('labels'))}",
                f"链接：{item['html_url']}",
                f"问题简介：{item.get('issue_summary_zh', '') or '暂无简介'}",
                f"建议工作：{item.get('work_needed_zh', '') or '暂无建议'}",
                "-" * 64,
            ]
        )
    return "\n".join(lines).strip() + "\n"


def render_email_html(candidates: list[dict[str, Any]]) -> str:
    cards = []
    for index, item in enumerate(candidates, start=1):
        repository = escape(str(item["repository"]))
        number = escape(str(item["number"]))
        title = escape(str(item["title"]))
        created_at = escape(_render_created_at(item.get("created_at")))
        labels_html = _render_labels_html(item.get("labels"))
        html_url = escape(str(item["html_url"]), quote=True)
        issue_summary = escape(item.get("issue_summary_zh", "") or "暂无简介")
        work_needed = escape(item.get("work_needed_zh", "") or "暂无建议")
        cards.append(
            f"""
            <section class="issue-card">
              <div class="issue-card__index">{index:02d}</div>
              <div class="issue-card__body">
                <p class="issue-card__repo">{repository} #{number}</p>
                <h2 class="issue-card__title">{title}</h2>
                <div class="issue-card__meta">
                  <span>创建时间：{created_at}</span>
                </div>
                <div class="issue-card__tags">{labels_html}</div>
                <p class="issue-card__section"><strong>问题简介：</strong>{issue_summary}</p>
                <p class="issue-card__section"><strong>建议工作：</strong>{work_needed}</p>
                <p class="issue-card__link"><a href="{html_url}">查看 issue</a></p>
              </div>
            </section>
            """
        )

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0" />
    <title>issue-radar</title>
    <style>
      :root {{
        color-scheme: light;
      }}
      body {{
        margin: 0;
        background: #f3efe7;
        color: #1f2937;
        font-family: "Georgia", "Times New Roman", serif;
      }}
      .shell {{
        width: 100%;
        padding: 28px 12px;
        box-sizing: border-box;
      }}
      .panel {{
        max-width: 760px;
        margin: 0 auto;
        background: #fffdf8;
        border: 1px solid #d9cfbf;
        border-radius: 20px;
        overflow: hidden;
        box-shadow: 0 18px 40px rgba(120, 98, 63, 0.10);
      }}
      .hero {{
        padding: 28px 28px 20px;
        background:
          radial-gradient(circle at top left, rgba(199, 155, 84, 0.18), transparent 36%),
          linear-gradient(135deg, #fff8e8 0%, #f7f1e5 48%, #efe6d8 100%);
        border-bottom: 1px solid #e6dccd;
      }}
      .eyebrow {{
        margin: 0 0 10px;
        font-size: 12px;
        letter-spacing: 0.14em;
        text-transform: uppercase;
        color: #8a6840;
      }}
      .title {{
        margin: 0;
        font-size: 30px;
        line-height: 1.15;
        color: #23180f;
      }}
      .subtitle {{
        margin: 12px 0 0;
        font-size: 15px;
        line-height: 1.7;
        color: #5c4632;
      }}
      .issues {{
        padding: 20px;
      }}
      .issue-card {{
        margin: 0 0 16px;
        border: 1px solid #e7ddcf;
        border-radius: 16px;
        background: #fffaf2;
      }}
      .issue-card:last-child {{
        margin-bottom: 0;
      }}
      .issue-card__index {{
        padding: 14px 18px 0;
        font-size: 12px;
        letter-spacing: 0.16em;
        color: #9b7550;
      }}
      .issue-card__body {{
        padding: 6px 18px 18px;
      }}
      .issue-card__repo {{
        margin: 0;
        font-size: 13px;
        color: #7b6149;
      }}
      .issue-card__title {{
        margin: 8px 0 12px;
        font-size: 22px;
        line-height: 1.35;
        color: #22170f;
      }}
      .issue-card__meta {{
        margin: 0 0 14px;
        font-size: 13px;
        line-height: 1.8;
        color: #4b5563;
      }}
      .issue-card__tags {{
        margin: 0 0 14px;
      }}
      .issue-card__tag {{
        display: inline-block;
        margin: 0 8px 8px 0;
        padding: 5px 10px;
        border: 1px solid #dcc9ad;
        border-radius: 999px;
        background: #fff4df;
        color: #735437;
        font-size: 12px;
        line-height: 1.3;
      }}
      .issue-card__tag--empty {{
        background: #f6f0e6;
        border-color: #e5dbcb;
        color: #8d7d68;
      }}
      .issue-card__section {{
        margin: 0 0 10px;
        font-size: 15px;
        line-height: 1.8;
        color: #2f2418;
      }}
      .issue-card__section strong {{
        color: #7a5632;
      }}
      .issue-card__link {{
        margin: 14px 0 0;
      }}
      .issue-card__link a {{
        color: #8f4f1f;
        text-decoration: none;
        border-bottom: 1px solid rgba(143, 79, 31, 0.32);
        padding-bottom: 2px;
      }}
      @media (max-width: 640px) {{
        .shell {{
          padding: 14px 8px;
        }}
        .hero {{
          padding: 22px 18px 18px;
        }}
        .title {{
          font-size: 24px;
        }}
        .issues {{
          padding: 12px;
        }}
        .issue-card__body {{
          padding: 6px 14px 16px;
        }}
        .issue-card__title {{
          font-size: 19px;
        }}
      }}
    </style>
  </head>
  <body>
    <div class="shell">
      <main class="panel">
        <header class="hero">
          <p class="eyebrow">issue-radar</p>
          <h1 class="title">发现 {len(candidates)} 个值得关注的 issue</h1>
          <p class="subtitle">以下是本轮推荐结果。</p>
        </header>
        <div class="issues">
          {"".join(cards)}
        </div>
      </main>
    </div>
  </body>
</html>
"""


def render_email(candidates: list[dict[str, Any]]) -> tuple[str, str, str]:
    subject = f"[issue-radar] 发现 {len(candidates)} 个值得关注的 issue"
    return subject, render_email_text(candidates), render_email_html(candidates)


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
    (output_dir / "email_body.html").write_text(payload["html_body"], encoding="utf-8")


def load_state_file(path: Path) -> dict[str, Any]:
    return load_json(path, default={})
