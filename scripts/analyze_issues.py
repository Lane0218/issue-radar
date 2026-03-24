#!/usr/bin/env python3

from __future__ import annotations

import argparse
from pathlib import Path

from issue_radar.ai_client import AIClient
from issue_radar.analysis import apply_post_rules, build_ai_prompts, build_heuristics, normalize_ai_result
from issue_radar.config import (
    DEFAULT_ANALYZED_OUTPUT,
    DEFAULT_PROFILE_CONFIG,
    DEFAULT_RAW_OUTPUT,
    DEFAULT_REPOS_CONFIG,
    load_monitor_config,
    load_profile,
)
from issue_radar.utils import dump_json, load_json, setup_logging


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Analyze fetched issues with AI.")
    parser.add_argument("--input", type=Path, default=DEFAULT_RAW_OUTPUT)
    parser.add_argument("--output", type=Path, default=DEFAULT_ANALYZED_OUTPUT)
    parser.add_argument("--profile", type=Path, default=DEFAULT_PROFILE_CONFIG)
    parser.add_argument("--repos-config", type=Path, default=DEFAULT_REPOS_CONFIG)
    return parser.parse_args()


def main() -> int:
    logger = setup_logging("analyze_issues")
    args = _parse_args()
    logger.info("Loading raw issues from %s", args.input)
    raw_issues = load_json(args.input, default=[])
    if not raw_issues:
        raise RuntimeError(f"No raw issues found in {args.input}")

    logger.info("Loaded %s raw issues", len(raw_issues))
    profile = load_profile(args.profile)
    monitor_config = load_monitor_config(args.repos_config)
    ai_client = AIClient.from_env()
    logger.info(
        "AI client configured with model=%s base_url=%s timeout=%ss",
        ai_client.model,
        ai_client.base_url,
        ai_client.timeout,
    )
    analyzed = []

    for index, bundle in enumerate(raw_issues, start=1):
        heuristics = build_heuristics(bundle, profile)
        system_prompt, user_prompt = build_ai_prompts(bundle, profile, heuristics)
        issue = bundle["issue"]
        logger.info(
            "Analyzing issue %s/%s: %s#%s %r",
            index,
            len(raw_issues),
            bundle["repository"],
            issue["number"],
            issue["title"],
        )
        try:
            ai_raw = ai_client.analyze_issue(system_prompt, user_prompt)
            ai_result = normalize_ai_result(ai_raw)
            logger.info(
                "AI result for %s#%s: claim=%s difficulty=%s category=%s fit=%s score=%s",
                bundle["repository"],
                issue["number"],
                ai_result["claim_status"],
                ai_result["difficulty"],
                ai_result["category"],
                ai_result["fit_for_user"],
                ai_result["recommend_score"],
            )
        except Exception as exc:
            logger.warning(
                "AI analysis failed for %s#%s, using fallback defaults: %s",
                bundle["repository"],
                issue["number"],
                exc,
            )
            ai_result = {
                "claim_status": "claimed" if heuristics.get("linked_pull_requests") else "unclaimed",
                "claim_reason": f"Fallback because AI analysis failed: {exc}",
                "difficulty": "unclear",
                "category": "other",
                "fit_for_user": "possible_fit",
                "fit_reason": "AI analysis failed, using fallback defaults.",
                "recommend_score": 40,
                "recommend_reason": "Fallback score because AI response was unavailable.",
            }

        final = apply_post_rules(
            bundle,
            ai_result,
            heuristics,
            notify_threshold=monitor_config.notify_threshold,
        )
        logger.info(
            "Final result for %s#%s: notify=%s score=%s adjustments=%s",
            bundle["repository"],
            issue["number"],
            final["should_notify"],
            final["recommend_score"],
            ",".join(final["score_adjustments"]) or "-",
        )
        analyzed.append(
            {
                "query_key": bundle["query_key"],
                "repository": bundle["repository"],
                "number": issue["number"],
                "title": issue["title"],
                "html_url": issue["html_url"],
                "created_at": issue["created_at"],
                "updated_at": issue["updated_at"],
                "labels": [label["name"] for label in issue.get("labels", [])],
                "assignees": [item["login"] for item in issue.get("assignees", [])],
                "linked_pull_requests": bundle.get("linked_pull_requests", []),
                "claim_status": final["claim_status"],
                "claim_reason": final["claim_reason"],
                "difficulty": final["difficulty"],
                "category": final["category"],
                "fit_for_user": final["fit_for_user"],
                "fit_reason": final["fit_reason"],
                "recommend_score": final["recommend_score"],
                "recommend_reason": final["recommend_reason"],
                "score_adjustments": final["score_adjustments"],
                "should_notify": final["should_notify"],
            }
        )

    analyzed.sort(key=lambda item: item["recommend_score"], reverse=True)
    dump_json(args.output, analyzed)
    logger.info("Analyzed %s issues into %s", len(analyzed), args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
