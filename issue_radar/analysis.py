from __future__ import annotations

import hashlib
import json
import re
from typing import Any

from .utils import clamp, truncate_text


CLAIM_PATTERNS = [
    re.compile(pattern, re.IGNORECASE)
    for pattern in [
        r"\bi(?:'d| would)? like to work on this\b",
        r"\bi(?:'m| am) working on this\b",
        r"\bi(?:'m| am) taking this\b",
        r"\bi(?:'ve| have) started\b",
        r"\bi(?:'ll| will) work on this\b",
        r"\bcan i work on this\b",
        r"\bassign(?:ed)? (?:it )?to me\b",
        r"\bi have a patch\b",
        r"\bi have a pr\b",
        r"\bsent a pr\b",
    ]
]

CATEGORY_VALUES = {"compiler", "mlir", "llvm", "frontend", "docs", "tests", "other"}
DIFFICULTY_VALUES = {"low", "medium_low", "too_hard", "unclear"}
FIT_VALUES = {"good_fit", "possible_fit", "poor_fit"}
CLAIM_VALUES = {"claimed", "unclaimed"}
MAX_AI_COMMENTS = 8
MAX_AI_COMMENT_CHARS = 400
MAX_AI_BODY_CHARS = 2500


def build_heuristics(issue_bundle: dict[str, Any], profile: dict[str, Any]) -> dict[str, Any]:
    issue = issue_bundle["issue"]
    comments = issue_bundle.get("comments", [])
    linked_prs = issue_bundle.get("linked_pull_requests", [])

    comment_evidence = []
    for comment in comments:
        body = comment.get("body") or ""
        for pattern in CLAIM_PATTERNS:
            if pattern.search(body):
                comment_evidence.append(
                    {
                        "author": comment.get("author"),
                        "created_at": comment.get("created_at"),
                        "body": truncate_text(body, 280),
                    }
                )
                break

    preferred_categories = normalize_preferred_domains(profile.get("preferred_domains", []))
    avoid_topics = [str(item).strip().lower() for item in profile.get("avoid_topics", []) if str(item).strip()]
    searchable_text = " ".join(
        [
            issue.get("title", ""),
            issue.get("body", ""),
            " ".join(label.get("name", "") for label in issue.get("labels", [])),
        ]
    ).lower()

    matched_avoid_topics = [topic for topic in avoid_topics if topic in searchable_text]
    return {
        "linked_pull_requests": linked_prs,
        "comment_claim_evidence": comment_evidence,
        "has_hard_claim_signal": bool(linked_prs),
        "preferred_categories": sorted(preferred_categories),
        "matched_avoid_topics": matched_avoid_topics,
    }


def normalize_preferred_domains(values: list[Any]) -> set[str]:
    mapping = {
        "编译器": "compiler",
        "compiler": "compiler",
        "mlir": "mlir",
        "llvm": "llvm",
        "前端": "frontend",
        "frontend": "frontend",
        "文档": "docs",
        "docs": "docs",
        "测试": "tests",
        "tests": "tests",
    }
    normalized: set[str] = set()
    for value in values:
        key = str(value).strip().lower()
        if key in mapping:
            normalized.add(mapping[key])
    return normalized


def build_ai_prompts(issue_bundle: dict[str, Any], profile: dict[str, Any], heuristics: dict[str, Any]) -> tuple[str, str]:
    issue = issue_bundle["issue"]
    raw_comments = issue_bundle.get("comments", [])
    compact_comments = [
        {
            "author": comment.get("author"),
            "author_association": comment.get("author_association"),
            "created_at": comment.get("created_at"),
            "body": truncate_text(comment.get("body") or "", MAX_AI_COMMENT_CHARS),
        }
        for comment in raw_comments[:MAX_AI_COMMENTS]
    ]

    user_payload = {
        "profile": profile,
        "heuristics": heuristics,
        "issue": {
            "repository": issue_bundle.get("repository"),
            "number": issue.get("number"),
            "title": issue.get("title"),
            "body": truncate_text(issue.get("body") or "", MAX_AI_BODY_CHARS),
            "labels": [label.get("name") for label in issue.get("labels", [])],
            "assignees": [item.get("login") for item in issue.get("assignees", [])],
            "created_at": issue.get("created_at"),
            "updated_at": issue.get("updated_at"),
            "comments_count": issue.get("comments_count"),
            "linked_pull_requests": heuristics.get("linked_pull_requests", []),
            "comment_sample_note": (
                f"Only the first {min(len(raw_comments), MAX_AI_COMMENTS)} comments are included, "
                f"each truncated to {MAX_AI_COMMENT_CHARS} characters."
            ),
            "comments": compact_comments,
        },
    }
    system_prompt = (
        "You analyze GitHub issues for a developer. "
        "Reply with JSON only. "
        "Required keys: claim_status, claim_reason, difficulty, category, "
        "fit_for_user, fit_reason, recommend_score, recommend_reason. "
        "claim_status must be claimed or unclaimed. "
        "difficulty must be low, medium_low, too_hard, or unclear. "
        "category must be compiler, mlir, llvm, frontend, docs, tests, or other. "
        "fit_for_user must be good_fit, possible_fit, or poor_fit. "
        "recommend_score must be an integer from 0 to 100."
    )
    return system_prompt, json.dumps(user_payload, ensure_ascii=False, indent=2)


def normalize_ai_result(raw: dict[str, Any]) -> dict[str, Any]:
    claim_status = str(raw.get("claim_status", "unclaimed")).strip().lower()
    difficulty = str(raw.get("difficulty", "unclear")).strip().lower()
    category = str(raw.get("category", "other")).strip().lower()
    fit_for_user = str(raw.get("fit_for_user", "possible_fit")).strip().lower()

    if claim_status not in CLAIM_VALUES:
        claim_status = "unclaimed"
    if difficulty not in DIFFICULTY_VALUES:
        difficulty = "unclear"
    if category not in CATEGORY_VALUES:
        category = "other"
    if fit_for_user not in FIT_VALUES:
        fit_for_user = "possible_fit"

    try:
        recommend_score = int(raw.get("recommend_score", 50))
    except (TypeError, ValueError):
        recommend_score = 50

    return {
        "claim_status": claim_status,
        "claim_reason": str(raw.get("claim_reason", "")).strip(),
        "difficulty": difficulty,
        "category": category,
        "fit_for_user": fit_for_user,
        "fit_reason": str(raw.get("fit_reason", "")).strip(),
        "recommend_score": clamp(recommend_score),
        "recommend_reason": str(raw.get("recommend_reason", "")).strip(),
    }


def apply_post_rules(
    issue_bundle: dict[str, Any],
    ai_result: dict[str, Any],
    heuristics: dict[str, Any],
    *,
    notify_threshold: int,
) -> dict[str, Any]:
    result = dict(ai_result)
    adjustments: list[str] = []
    score = int(result["recommend_score"])

    if heuristics.get("linked_pull_requests"):
        result["claim_status"] = "claimed"
        if heuristics.get("linked_pull_requests"):
            linked = heuristics["linked_pull_requests"][0]["html_url"]
            reason = result.get("claim_reason", "")
            prefix = f"Linked pull request detected: {linked}."
            result["claim_reason"] = f"{prefix} {reason}".strip()

    if result["claim_status"] == "claimed":
        score = min(score, 35)
        adjustments.append("claimed cap 35")

    if result["difficulty"] == "low":
        score += 8
        adjustments.append("low +8")
    elif result["difficulty"] == "medium_low":
        score += 4
        adjustments.append("medium_low +4")
    elif result["difficulty"] == "too_hard":
        score = min(score, 40)
        adjustments.append("too_hard cap 40")

    if result["fit_for_user"] == "good_fit":
        score += 10
        adjustments.append("good_fit +10")
    elif result["fit_for_user"] == "possible_fit":
        score += 2
        adjustments.append("possible_fit +2")
    elif result["fit_for_user"] == "poor_fit":
        score = min(score, 45)
        adjustments.append("poor_fit cap 45")

    preferred_categories = set(heuristics.get("preferred_categories", []))
    if result["category"] in preferred_categories:
        score += 8
        adjustments.append("preferred_category +8")

    labels = {label.get("name", "").lower() for label in issue_bundle["issue"].get("labels", [])}
    if "good first issue" in labels:
        score += 10
        adjustments.append("good_first_issue +10")

    if heuristics.get("matched_avoid_topics"):
        score = min(score, 30)
        adjustments.append("avoid_topic cap 30")

    score = clamp(score)
    result["recommend_score"] = score
    result["score_adjustments"] = adjustments
    result["should_notify"] = (
        result["claim_status"] == "unclaimed"
        and result["difficulty"] in {"low", "medium_low"}
        and result["fit_for_user"] in {"good_fit", "possible_fit"}
        and score >= notify_threshold
    )
    return result


def issue_fingerprint(item: dict[str, Any]) -> str:
    payload = {
        "repository": item.get("repository"),
        "number": item.get("number"),
        "updated_at": item.get("updated_at"),
        "claim_status": item.get("claim_status"),
        "recommend_score": item.get("recommend_score"),
        "should_notify": item.get("should_notify"),
    }
    return hashlib.sha1(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()
