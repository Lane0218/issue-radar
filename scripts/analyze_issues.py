#!/usr/bin/env python3

from __future__ import annotations

import argparse
import logging
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
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


def _parse_iso_datetime(value: str) -> datetime:
    normalized = value.replace("Z", "+00:00")
    return datetime.fromisoformat(normalized)


def _is_issue_within_age_limit(bundle: dict[str, Any], max_issue_age_days: int) -> tuple[bool, str]:
    issue = bundle["issue"]
    created_at_raw = issue.get("created_at")
    if not created_at_raw:
        return False, "Issue missing created_at."

    created_at = _parse_iso_datetime(created_at_raw)
    cutoff = datetime.now(timezone.utc) - timedelta(days=max_issue_age_days)
    if created_at < cutoff:
        return (
            False,
            f"Issue created_at={created_at_raw} is older than max_issue_age_days={max_issue_age_days} "
            f"(cutoff={cutoff.isoformat(timespec='seconds')}).",
        )
    return True, ""


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
    logger = logging.getLogger("analyze_issues")
    heuristics = build_heuristics(bundle, profile)
    claim_state, claim_reason = determine_claim_state(bundle, heuristics)
    issue = bundle["issue"]
    issue_key = _issue_key(bundle)
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
    max_attempts = 2
    last_error: Exception | None = None
    for attempt in range(1, max_attempts + 1):
        try:
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
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            if attempt < max_attempts:
                logger.warning(
                    "AI request failed for %s on attempt %s/%s: %s; retrying once",
                    issue_key,
                    attempt,
                    max_attempts,
                    exc,
                )
            else:
                logger.warning(
                    "AI request failed for %s on attempt %s/%s: %s; dropping issue from analyzed results",
                    issue_key,
                    attempt,
                    max_attempts,
                    exc,
                )
    assert last_error is not None
    raise last_error


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
    logger.info("Applying max_issue_age_days=%s", monitor_config.max_issue_age_days)
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
    skipped_old = 0
    for bundle in raw_issues:
        issue_key = _issue_key(bundle)
        if issue_key in seen_issues:
            logger.info("Skipping already analyzed issue %s", issue_key)
            continue
        within_age_limit, age_reason = _is_issue_within_age_limit(bundle, monitor_config.max_issue_age_days)
        if not within_age_limit:
            logger.info("Skipping %s because %s", issue_key, age_reason)
            claim_state, _ = determine_claim_state(bundle, build_heuristics(bundle, profile))
            seen_issues[issue_key] = _build_state_entry(bundle, claim_state)
            skipped_old += 1
            continue
        new_bundles.append(bundle)

    logger.info(
        "Found %s new issues to process after skipping %s issues outside the age limit",
        len(new_bundles),
        skipped_old,
    )
    if not new_bundles:
        dump_json(args.output, [])
        dump_json(args.state, {"issues": seen_issues})
        logger.info("No new issues required analysis. Wrote empty result to %s", args.output)
        logger.info("Updated analysis state in %s", args.state)
        return 0

    analyzed: list[dict[str, Any]] = []
    ai_candidates = []
    skipped_claimed = 0
    for bundle in new_bundles:
        heuristics = build_heuristics(bundle, profile)
        claim_state, claim_reason = determine_claim_state(bundle, heuristics)
        issue_key = _issue_key(bundle)

        if claim_state == "claimed":
            logger.info(
                "Skipping AI for %s because claim_state=%s reason=%s",
                issue_key,
                claim_state,
                claim_reason,
            )
            seen_issues[issue_key] = _build_state_entry(bundle, claim_state)
            skipped_claimed += 1
            continue

        ai_candidates.append(bundle)
        logger.info("Queued %s for AI analysis with claim_state=%s", issue_key, claim_state)

    logger.info(
        "Prepared %s AI candidates after skipping %s claimed issues",
        len(ai_candidates),
        skipped_claimed,
    )

    if ai_candidates:
        logger.info("Starting AI analysis for %s issues with concurrency=%s", len(ai_candidates), max_workers)
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_map = {
                executor.submit(_analyze_candidate, bundle, profile, monitor_config.notify_threshold): bundle
                for bundle in ai_candidates
            }
            dropped_count = 0
            for future in as_completed(future_map):
                bundle = future_map[future]
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
                    analyzed.append(final)
                except Exception as exc:  # noqa: BLE001
                    dropped_count += 1
                    logger.warning(
                        "Dropping %s after AI failure. It will not be written to analyzed results: %s",
                        issue_key,
                        exc,
                    )
            logger.info("Dropped %s issues after AI failures", dropped_count)

    analyzed.sort(key=lambda item: item["recommend_score"], reverse=True)
    dump_json(args.output, analyzed)
    dump_json(args.state, {"issues": seen_issues})
    logger.info("Wrote %s successfully analyzed issues into %s", len(analyzed), args.output)
    logger.info("Updated analysis state in %s", args.state)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
