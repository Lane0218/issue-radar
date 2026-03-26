from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from typing import Any

from .utils import truncate_text


CLAIM_PATTERNS = [
    re.compile(pattern, re.IGNORECASE)
    for pattern in [
        r"\bi(?:'d| would)? like to work on this\b",
        r"\bi(?:'m| am) working on this\b",
        r"\bi(?:'m| am) taking this\b",
        r"\bi(?:'ve| have) started\b",
        r"\bi(?:'ll| will) work on this\b",
        r"\bcan i work on this\b",
        r"\bcan i get assigned\b",
        r"\bcan this be assigned to me\b",
        r"\bassign me\b",
        r"\bplease assign\b",
        r"\bassign(?:ed)? (?:it )?to me\b",
        r"\bi have a patch\b",
        r"\bi have a pr\b",
        r"\bsent a pr\b",
    ]
]

CLAIM_STATES = {"claimed", "open"}
CATEGORY_VALUES = {"compiler", "mlir", "llvm", "frontend", "docs", "tests", "other"}
DIFFICULTY_VALUES = {"low", "medium_low", "too_hard", "unclear"}
FIT_VALUES = {"good_fit", "possible_fit", "poor_fit"}
MAX_AI_BODY_CHARS = 2200
KNOWN_BOTS = {"llvmbot"}
FIT_SORT_PRIORITY = {"good_fit": 0, "possible_fit": 1, "poor_fit": 2}
DIFFICULTY_SORT_PRIORITY = {"low": 0, "medium_low": 1, "unclear": 2, "too_hard": 3}


def is_bot_author(author: str | None) -> bool:
    if not author:
        return False
    normalized = author.strip().lower()
    return normalized in KNOWN_BOTS or normalized.endswith("bot") or "[bot]" in normalized


def build_heuristics(issue_bundle: dict[str, Any]) -> dict[str, Any]:
    issue = issue_bundle["issue"]
    comments = issue_bundle.get("comments", [])
    linked_prs = issue_bundle.get("linked_pull_requests", [])

    issue_author = (issue.get("author") or "").strip().lower()
    other_participant_comments = []
    claim_comment_evidence = []
    for comment in comments:
        author = comment.get("author")
        body = comment.get("body") or ""
        if is_bot_author(author):
            continue
        normalized_author = (author or "").strip().lower()
        if normalized_author and normalized_author != issue_author:
            other_participant_comments.append(
                {
                    "author": author,
                    "created_at": comment.get("created_at"),
                    "body": truncate_text(body, 280),
                }
            )
        for pattern in CLAIM_PATTERNS:
            if pattern.search(body):
                claim_comment_evidence.append(
                    {
                        "author": author,
                        "created_at": comment.get("created_at"),
                        "body": truncate_text(body, 280),
                    }
                )
                break

    return {
        "linked_pull_requests": linked_prs,
        "other_participant_comments": other_participant_comments,
        "claim_comment_evidence": claim_comment_evidence,
    }


def determine_claim_state(issue_bundle: dict[str, Any], heuristics: dict[str, Any]) -> tuple[str, str]:
    issue = issue_bundle["issue"]
    assignees = issue.get("assignees", [])
    if assignees:
        users = ", ".join(item.get("login", "unknown") for item in assignees)
        return "claimed", f"Assignee detected: {users}."

    linked_prs = heuristics.get("linked_pull_requests", [])
    if linked_prs:
        pr = linked_prs[0]
        return "claimed", f"Linked pull request detected: {pr.get('html_url')}."

    claim_comment_evidence = heuristics.get("claim_comment_evidence", [])
    if claim_comment_evidence:
        evidence = claim_comment_evidence[-1]
        return (
            "claimed",
            f"Claim-like comment from @{evidence['author']} at {evidence['created_at']}: {evidence['body']}",
        )

    other_participant_comments = heuristics.get("other_participant_comments", [])
    if other_participant_comments:
        latest = other_participant_comments[-1]
        return (
            "claimed",
            f"Non-bot, non-author comment from @{latest['author']} at {latest['created_at']}: {latest['body']}",
        )

    return "open", "No assignee, linked PR, claim-like comment, or non-bot, non-author comment detected."


def build_ai_prompts(issue_bundle: dict[str, Any], profile: dict[str, Any]) -> tuple[str, str]:
    issue = issue_bundle["issue"]
    user_payload = {
        "profile": profile,
        "issue": {
            "repository": issue_bundle.get("repository"),
            "number": issue.get("number"),
            "title": issue.get("title"),
            "body": truncate_text(issue.get("body") or "", MAX_AI_BODY_CHARS),
            "labels": [label.get("name") for label in issue.get("labels", [])],
        },
    }
    system_prompt = (
        "You analyze GitHub issues for a developer. "
        "Reply with JSON only. "
        "Required keys: difficulty, category, fit_for_user, fit_reason, issue_summary_zh, work_needed_zh. "
        "difficulty must be low, medium_low, too_hard, or unclear. "
        "category must be compiler, mlir, llvm, frontend, docs, tests, or other. "
        "fit_for_user must be good_fit, possible_fit, or poor_fit. "
        "issue_summary_zh must be concise Simplified Chinese explaining what the issue is. "
        "work_needed_zh must be concise Simplified Chinese explaining what work the contributor likely needs to do. "
        "Do not include claim status or assignment analysis."
    )
    return system_prompt, json.dumps(user_payload, ensure_ascii=False, indent=2)


def normalize_ai_result(raw: dict[str, Any]) -> dict[str, Any]:
    difficulty = str(raw.get("difficulty", "unclear")).strip().lower()
    category = str(raw.get("category", "other")).strip().lower()
    fit_for_user = str(raw.get("fit_for_user", "possible_fit")).strip().lower()

    if difficulty not in DIFFICULTY_VALUES:
        difficulty = "unclear"
    if category not in CATEGORY_VALUES:
        category = "other"
    if fit_for_user not in FIT_VALUES:
        fit_for_user = "possible_fit"

    issue_summary_zh = str(raw.get("issue_summary_zh", "")).strip()
    work_needed_zh = str(raw.get("work_needed_zh", "")).strip()
    if not issue_summary_zh:
        issue_summary_zh = "该 issue 需要先阅读标题与正文，确认它描述的问题背景和目标。"
    if not work_needed_zh:
        work_needed_zh = "需要阅读 issue 内容，定位问题点，并根据上下文完成修复、补测试或补充说明。"

    return {
        "difficulty": difficulty,
        "category": category,
        "fit_for_user": fit_for_user,
        "fit_reason": str(raw.get("fit_reason", "")).strip(),
        "issue_summary_zh": issue_summary_zh,
        "work_needed_zh": work_needed_zh,
    }


def build_skip_ai_result(claim_state: str, claim_reason: str) -> dict[str, Any]:
    return {
        "difficulty": "unclear",
        "category": "other",
        "fit_for_user": "possible_fit",
        "fit_reason": f"Skipped AI analysis because claim_state={claim_state}.",
        "issue_summary_zh": "该 issue 未进行 AI 摘要生成。",
        "work_needed_zh": "该 issue 未进行 AI 工作项分析。",
        "claim_state": claim_state,
        "claim_reason": claim_reason,
    }


def apply_post_rules(
    issue_bundle: dict[str, Any],
    ai_result: dict[str, Any],
    heuristics: dict[str, Any],
    *,
    claim_state: str,
    claim_reason: str,
) -> dict[str, Any]:
    result = dict(ai_result)
    result["claim_state"] = claim_state
    result["claim_reason"] = claim_reason
    result["should_notify"] = (
        claim_state == "open"
        and result["difficulty"] in {"low", "medium_low"}
        and result["fit_for_user"] in {"good_fit", "possible_fit"}
    )
    return result


def parse_iso_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def issue_sort_key(item: dict[str, Any]) -> tuple[int, int, float, str, int]:
    fit_priority = FIT_SORT_PRIORITY.get(str(item.get("fit_for_user", "")).strip().lower(), 99)
    difficulty_priority = DIFFICULTY_SORT_PRIORITY.get(str(item.get("difficulty", "")).strip().lower(), 99)
    created_at = parse_iso_datetime(item.get("created_at"))
    created_sort_value = -(created_at.timestamp() if created_at else 0.0)
    return (
        fit_priority,
        difficulty_priority,
        created_sort_value,
        str(item.get("repository", "")),
        int(item.get("number", 0)),
    )


def issue_fingerprint(item: dict[str, Any]) -> str:
    payload = {
        "repository": item.get("repository"),
        "number": item.get("number"),
        "updated_at": item.get("updated_at"),
        "claim_state": item.get("claim_state"),
        "difficulty": item.get("difficulty"),
        "fit_for_user": item.get("fit_for_user"),
        "should_notify": item.get("should_notify"),
    }
    return hashlib.sha1(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()
