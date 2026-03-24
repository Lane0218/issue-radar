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

CLAIM_STATES = {"claimed", "maybe_claimed", "open"}
CATEGORY_VALUES = {"compiler", "mlir", "llvm", "frontend", "docs", "tests", "other"}
DIFFICULTY_VALUES = {"low", "medium_low", "too_hard", "unclear"}
FIT_VALUES = {"good_fit", "possible_fit", "poor_fit"}
MAX_AI_BODY_CHARS = 2200
KNOWN_BOTS = {"llvmbot"}


def is_bot_author(author: str | None) -> bool:
    if not author:
        return False
    normalized = author.strip().lower()
    return normalized in KNOWN_BOTS or normalized.endswith("bot") or "[bot]" in normalized


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


def build_heuristics(issue_bundle: dict[str, Any], profile: dict[str, Any]) -> dict[str, Any]:
    issue = issue_bundle["issue"]
    comments = issue_bundle.get("comments", [])
    linked_prs = issue_bundle.get("linked_pull_requests", [])

    human_comments = []
    claim_comment_evidence = []
    for comment in comments:
        author = comment.get("author")
        body = comment.get("body") or ""
        if is_bot_author(author):
            continue
        human_comments.append(
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
        "human_comments": human_comments,
        "claim_comment_evidence": claim_comment_evidence,
        "preferred_categories": sorted(preferred_categories),
        "matched_avoid_topics": matched_avoid_topics,
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

    human_comments = heuristics.get("human_comments", [])
    if human_comments:
        latest = human_comments[-1]
        return (
            "maybe_claimed",
            f"Non-bot comment exists from @{latest['author']} at {latest['created_at']}: {latest['body']}",
        )

    return "open", "No assignee, linked PR, claim-like comment, or non-bot comment detected."


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
        "Required keys: difficulty, category, fit_for_user, fit_reason, recommend_score, recommend_reason. "
        "difficulty must be low, medium_low, too_hard, or unclear. "
        "category must be compiler, mlir, llvm, frontend, docs, tests, or other. "
        "fit_for_user must be good_fit, possible_fit, or poor_fit. "
        "recommend_score must be an integer from 0 to 100. "
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

    try:
        recommend_score = int(raw.get("recommend_score", 50))
    except (TypeError, ValueError):
        recommend_score = 50

    return {
        "difficulty": difficulty,
        "category": category,
        "fit_for_user": fit_for_user,
        "fit_reason": str(raw.get("fit_reason", "")).strip(),
        "recommend_score": clamp(recommend_score),
        "recommend_reason": str(raw.get("recommend_reason", "")).strip(),
    }


def build_skip_ai_result(claim_state: str, claim_reason: str) -> dict[str, Any]:
    return {
        "difficulty": "unclear",
        "category": "other",
        "fit_for_user": "possible_fit",
        "fit_reason": f"Skipped AI analysis because claim_state={claim_state}.",
        "recommend_score": 0,
        "recommend_reason": f"Skipped AI analysis because this issue is {claim_state}.",
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
    notify_threshold: int,
) -> dict[str, Any]:
    result = dict(ai_result)
    adjustments: list[str] = []
    score = int(result["recommend_score"])

    if claim_state == "claimed":
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

    if claim_state == "claimed":
        score = min(score, 35)

    score = clamp(score)
    result["claim_state"] = claim_state
    result["claim_reason"] = claim_reason
    result["recommend_score"] = score
    result["score_adjustments"] = adjustments
    result["should_notify"] = (
        claim_state in {"open", "maybe_claimed"}
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
        "claim_state": item.get("claim_state"),
        "recommend_score": item.get("recommend_score"),
        "should_notify": item.get("should_notify"),
    }
    return hashlib.sha1(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()
