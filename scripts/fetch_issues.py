#!/usr/bin/env python3

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from issue_radar.config import DEFAULT_RAW_OUTPUT, DEFAULT_REPOS_CONFIG, load_monitor_config
from issue_radar.github_client import GitHubClient, extract_linked_pull_requests, load_github_token
from issue_radar.utils import dump_json, now_iso


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


def main() -> int:
    args = _parse_args()
    config = load_monitor_config(args.config)
    token = load_github_token()
    client = GitHubClient(token=token)
    fetched_at = now_iso()
    bundles: list[dict[str, Any]] = []

    for repo_config in config.repositories:
        search_results = client.search_issues(
            owner=repo_config.owner,
            repo=repo_config.repo,
            query=repo_config.query,
            sort=repo_config.sort,
            order=repo_config.order,
            per_page=repo_config.max_issues,
        )
        for item in search_results:
            number = int(item["number"])
            issue = client.get_issue(repo_config.owner, repo_config.repo, number)
            comments = client.get_issue_comments(
                repo_config.owner,
                repo_config.repo,
                number,
                max_comments=repo_config.max_comments,
            )
            timeline = client.get_issue_timeline(repo_config.owner, repo_config.repo, number)
            bundles.append(
                {
                    "query_key": repo_config.key,
                    "query": repo_config.query,
                    "repository": f"{repo_config.owner}/{repo_config.repo}",
                    "fetched_at": fetched_at,
                    "issue": _serialize_issue(issue),
                    "comments": [_serialize_comment(comment) for comment in comments],
                    "timeline": [_serialize_timeline_event(event) for event in timeline],
                    "linked_pull_requests": extract_linked_pull_requests(timeline),
                }
            )

    dump_json(args.output, bundles)
    print(f"Fetched {len(bundles)} issues into {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
