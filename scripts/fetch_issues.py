#!/usr/bin/env python3

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from issue_radar.config import DEFAULT_RAW_OUTPUT, DEFAULT_REPOS_CONFIG, load_monitor_config
from issue_radar.github_client import GitHubClient, extract_linked_pull_requests, load_github_token
from issue_radar.utils import dump_json, now_iso, setup_logging


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Fetch GitHub issues and comments.")
    parser.add_argument("--config", type=Path, default=DEFAULT_REPOS_CONFIG)
    parser.add_argument("--output", type=Path, default=DEFAULT_RAW_OUTPUT)
    return parser.parse_args()


def _serialize_issue(issue: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": issue.get("id"),
        "number": issue.get("number"),
        "title": issue.get("title"),
        "html_url": issue.get("html_url"),
        "state": issue.get("state"),
        "author": (issue.get("user") or {}).get("login"),
        "assignees": [
            {"login": assignee.get("login"), "html_url": assignee.get("html_url")}
            for assignee in issue.get("assignees", [])
        ],
        "labels": [
            {"name": label.get("name"), "description": label.get("description")}
            for label in issue.get("labels", [])
        ],
        "created_at": issue.get("created_at"),
        "updated_at": issue.get("updated_at"),
        "body": issue.get("body") or "",
        "comments_count": issue.get("comments", 0),
    }


def _serialize_comment(comment: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": comment.get("id"),
        "author": (comment.get("user") or {}).get("login"),
        "author_association": comment.get("author_association"),
        "created_at": comment.get("created_at"),
        "updated_at": comment.get("updated_at"),
        "body": comment.get("body") or "",
        "html_url": comment.get("html_url"),
    }


def _serialize_timeline_event(event: dict[str, Any]) -> dict[str, Any]:
    source = event.get("source") or {}
    source_issue = source.get("issue") or {}
    return {
        "event": event.get("event"),
        "created_at": event.get("created_at"),
        "actor": (event.get("actor") or {}).get("login"),
        "source_issue_number": source_issue.get("number"),
        "source_issue_title": source_issue.get("title"),
        "source_issue_html_url": source_issue.get("html_url"),
        "source_pull_request": bool(source_issue.get("pull_request")),
    }


def _bundle_key(repository_name: str, number: int) -> str:
    return f"{repository_name}#{number}"


def main() -> int:
    logger = setup_logging("fetch_issues")
    args = _parse_args()
    logger.info("Loading monitor config from %s", args.config)
    config = load_monitor_config(args.config)
    token = load_github_token()
    logger.info("GitHub token available: %s", "yes" if token else "no")
    client = GitHubClient(token=token)
    fetched_at = now_iso()
    bundles_by_key: dict[str, dict[str, Any]] = {}

    for repo_config in config.repositories:
        repository_name = f"{repo_config.owner}/{repo_config.repo}"
        logger.info(
            "Searching issues for %s with query=%r sort=%s order=%s max_issues=%s max_comments=%s",
            repository_name,
            repo_config.query,
            repo_config.sort,
            repo_config.order,
            repo_config.max_issues,
            repo_config.max_comments,
        )
        search_results = client.search_issues(
            owner=repo_config.owner,
            repo=repo_config.repo,
            query=repo_config.query,
            sort=repo_config.sort,
            order=repo_config.order,
            per_page=repo_config.max_issues,
        )
        logger.info("Search returned %s issues for %s", len(search_results), repository_name)
        for index, item in enumerate(search_results, start=1):
            number = int(item["number"])
            issue_key = _bundle_key(repository_name, number)
            if issue_key in bundles_by_key:
                existing = bundles_by_key[issue_key]
                existing["matched_queries"].append(repo_config.key)
                existing["source_signals"] = sorted(
                    set(existing["source_signals"]) | set(repo_config.source_signals)
                )
                logger.info(
                    "Merged duplicate hit for %s via query=%s signals=%s",
                    issue_key,
                    repo_config.key,
                    ",".join(repo_config.source_signals),
                )
                continue
            logger.info("Fetching issue %s/%s: %s#%s", index, len(search_results), repository_name, number)
            issue = client.get_issue(repo_config.owner, repo_config.repo, number)
            comments = client.get_issue_comments(
                repo_config.owner,
                repo_config.repo,
                number,
                max_comments=repo_config.max_comments,
            )
            timeline = client.get_issue_timeline(repo_config.owner, repo_config.repo, number)
            linked_pull_requests = extract_linked_pull_requests(timeline)
            logger.info(
                "Fetched %s#%s title=%r comments=%s timeline_events=%s linked_prs=%s",
                repository_name,
                number,
                issue.get("title"),
                len(comments),
                len(timeline),
                len(linked_pull_requests),
            )
            bundles_by_key[issue_key] = {
                "query_key": repo_config.key,
                "query": repo_config.query,
                "matched_queries": [repo_config.key],
                "source_signals": sorted(set(repo_config.source_signals)),
                "repository": repository_name,
                "fetched_at": fetched_at,
                "issue": _serialize_issue(issue),
                "comments": [_serialize_comment(comment) for comment in comments],
                "timeline": [_serialize_timeline_event(event) for event in timeline],
                "linked_pull_requests": linked_pull_requests,
            }

    bundles = sorted(
        bundles_by_key.values(),
        key=lambda item: (item["repository"], item["issue"]["number"]),
    )
    dump_json(args.output, bundles)
    logger.info("Fetched %s unique issues into %s", len(bundles), args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
