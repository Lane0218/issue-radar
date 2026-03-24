#!/usr/bin/env python3

from __future__ import annotations

import argparse
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

from issue_radar.ai_client import AIClient
from issue_radar.analysis import (
    apply_post_rules,
    build_ai_prompts,
    build_heuristics,
    build_skip_ai_result,
    determine_claim_state,
    normalize_ai_result,
)
from issue_radar.config import (
    DEFAULT_ANALYSIS_STATE_FILE,
    DEFAULT_ANALYZED_OUTPUT,
    DEFAULT_PROFILE_CONFIG,
    DEFAULT_RAW_OUTPUT,
    DEFAULT_REPOS_CONFIG,
    load_monitor_config,
    load_profile,
)
from issue_radar.utils import dump_json, load_json, now_iso, setup_logging


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Analyze fetched issues with AI.")
    parser.add_argument("--input", type=Path, default=DEFAULT_RAW_OUTPUT)
    parser.add_argument("--output", type=Path, default=DEFAULT_ANALYZED_OUTPUT)
    parser.add_argument("--profile", type=Path, default=DEFAULT_PROFILE_CONFIG)
    parser.add_argument("--repos-config", type=Path, default=DEFAULT_REPOS_CONFIG)
    parser.add_argument("--state", type=Path, default=DEFAULT_ANALYSIS_STATE_FILE)
    return parser.parse_args()


def _issue_key(bundle: dict[str, Any]) -> str:
    return f"{bundle['repository']}#{bundle['issue']['number']}"


def _build_state_entry(bundle: dict[str, Any], claim_state: str) -> dict[str, Any]:
    issue = bundle["issue"]
    return {
        "seen_at": now_iso(),
        "repository": bundle["repository"],
        "number": issue["number"],
        "title": issue["title"],
        "created_at": issue["created_at"],
        "claim_state_at_first_seen": claim_state,
    }


def _analyze_candidate(
    bundle: dict[str, Any],
    profile: dict[str, Any],
    notify_threshold: int,
) -> dict[str, Any]:
    heuristics = build_heuristics(bundle, profile)
    claim_state, claim_reason = determine_claim_state(bundle, heuristics)
    issue = bundle["issue"]
    base = {
        "query_key": bundle["query_key"],
        "matched_queries": bundle.get("matched_queries", [bundle["query_key"]]),
        "source_signals": bundle.get("source_signals", []),
        "repository": bundle["repository"],
        "number": issue["number"],
        "title": issue["title"],
        "html_url": issue["html_url"],
        "created_at": issue["created_at"],
        "updated_at": issue["updated_at"],
        "labels": [label["name"] for label in issue.get("labels", [])],
        "assignees": [item["login"] for item in issue.get("assignees", [])],
        "linked_pull_requests": bundle.get("linked_pull_requests", []),
    }

    if claim_state == "claimed":
        final = apply_post_rules(
            bundle,
            build_skip_ai_result(claim_state, claim_reason),
            heuristics,
            claim_state=claim_state,
            claim_reason=claim_reason,
            notify_threshold=notify_threshold,
        )
        return base | final

    system_prompt, user_prompt = build_ai_prompts(bundle, profile)
    ai_client = AIClient.from_env()
    ai_raw = ai_client.analyze_issue(system_prompt, user_prompt)
    ai_result = normalize_ai_result(ai_raw)
    final = apply_post_rules(
        bundle,
        ai_result,
        heuristics,
        claim_state=claim_state,
        claim_reason=claim_reason,
        notify_threshold=notify_threshold,
    )
    return base | final


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
    analysis_state = load_json(args.state, default={"issues": {}})
    seen_issues: dict[str, Any] = dict(analysis_state.get("issues", {}))

    ai_client = AIClient.from_env()
    max_workers = int(os.environ.get("AI_MAX_WORKERS", "3"))
    logger.info(
        "AI client configured with model=%s base_url=%s timeout=%ss max_workers=%s",
        ai_client.model,
        ai_client.base_url,
        ai_client.timeout,
        max_workers,
    )

    new_bundles = []
    for bundle in raw_issues:
        issue_key = _issue_key(bundle)
        if issue_key in seen_issues:
            logger.info("Skipping already analyzed issue %s", issue_key)
            continue
        new_bundles.append(bundle)

    logger.info("Found %s new issues to process", len(new_bundles))
    if not new_bundles:
        dump_json(args.output, [])
        logger.info("No new issues required analysis. Wrote empty result to %s", args.output)
        return 0

    analyzed: list[dict[str, Any]] = []
    ai_candidates = []
    for bundle in new_bundles:
        heuristics = build_heuristics(bundle, profile)
        claim_state, claim_reason = determine_claim_state(bundle, heuristics)
        issue_key = _issue_key(bundle)

        if claim_state == "claimed":
            issue = bundle["issue"]
            logger.info(
                "Skipping AI for %s because claim_state=%s reason=%s",
                issue_key,
                claim_state,
                claim_reason,
            )
            seen_issues[issue_key] = _build_state_entry(bundle, claim_state)
            final = apply_post_rules(
                bundle,
                build_skip_ai_result(claim_state, claim_reason),
                heuristics,
                claim_state=claim_state,
                claim_reason=claim_reason,
                notify_threshold=monitor_config.notify_threshold,
            )
            analyzed.append(
                {
                    "query_key": bundle["query_key"],
                    "matched_queries": bundle.get("matched_queries", [bundle["query_key"]]),
                    "source_signals": bundle.get("source_signals", []),
                    "repository": bundle["repository"],
                    "number": issue["number"],
                    "title": issue["title"],
                    "html_url": issue["html_url"],
                    "created_at": issue["created_at"],
                    "updated_at": issue["updated_at"],
                    "labels": [label["name"] for label in issue.get("labels", [])],
                    "assignees": [item["login"] for item in issue.get("assignees", [])],
                    "linked_pull_requests": bundle.get("linked_pull_requests", []),
                }
                | final
            )
            continue

        ai_candidates.append(bundle)
        logger.info("Queued %s for AI analysis with claim_state=%s", issue_key, claim_state)

    if ai_candidates:
        logger.info("Starting AI analysis for %s issues with concurrency=%s", len(ai_candidates), max_workers)
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_map = {
                executor.submit(_analyze_candidate, bundle, profile, monitor_config.notify_threshold): bundle
                for bundle in ai_candidates
            }
            for future in as_completed(future_map):
                bundle = future_map[future]
                issue = bundle["issue"]
                issue_key = _issue_key(bundle)
                logger.info("Awaiting AI result for %s", issue_key)
                try:
                    final = future.result()
                    logger.info(
                        "AI result for %s: claim_state=%s difficulty=%s category=%s fit=%s score=%s notify=%s",
                        issue_key,
                        final["claim_state"],
                        final["difficulty"],
                        final["category"],
                        final["fit_for_user"],
                        final["recommend_score"],
                        final["should_notify"],
                    )
                    seen_issues[issue_key] = _build_state_entry(bundle, final["claim_state"])
                except Exception as exc:
                    heuristics = build_heuristics(bundle, profile)
                    claim_state, claim_reason = determine_claim_state(bundle, heuristics)
                    logger.warning("AI analysis failed for %s, using fallback defaults: %s", issue_key, exc)
                    final = (
                        {
                            "query_key": bundle["query_key"],
                            "matched_queries": bundle.get("matched_queries", [bundle["query_key"]]),
                            "source_signals": bundle.get("source_signals", []),
                            "repository": bundle["repository"],
                            "number": issue["number"],
                            "title": issue["title"],
                            "html_url": issue["html_url"],
                            "created_at": issue["created_at"],
                            "updated_at": issue["updated_at"],
                            "labels": [label["name"] for label in issue.get("labels", [])],
                            "assignees": [item["login"] for item in issue.get("assignees", [])],
                            "linked_pull_requests": bundle.get("linked_pull_requests", []),
                        }
                        | apply_post_rules(
                            bundle,
                            {
                                "difficulty": "unclear",
                                "category": "other",
                                "fit_for_user": "possible_fit",
                                "fit_reason": "AI analysis failed, using fallback defaults.",
                                "recommend_score": 40,
                                "recommend_reason": f"Fallback score because AI response was unavailable: {exc}",
                            },
                            heuristics,
                            claim_state=claim_state,
                            claim_reason=claim_reason,
                            notify_threshold=monitor_config.notify_threshold,
                        )
                    )
                analyzed.append(final)

    analyzed.sort(key=lambda item: item["recommend_score"], reverse=True)
    dump_json(args.output, analyzed)
    dump_json(args.state, {"issues": seen_issues})
    logger.info("Analyzed %s new issues into %s", len(analyzed), args.output)
    logger.info("Updated analysis state in %s", args.state)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
