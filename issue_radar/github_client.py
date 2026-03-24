from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import requests
import yaml


class GitHubClient:
    def __init__(self, token: str | None = None, base_url: str = "https://api.github.com") -> None:
        self.base_url = base_url.rstrip("/")
        self.session = requests.Session()
        self.session.headers.update(
            {
                "Accept": "application/vnd.github+json",
                "User-Agent": "issue-radar",
                "X-GitHub-Api-Version": "2022-11-28",
            }
        )
        if token:
            self.session.headers["Authorization"] = f"Bearer {token}"

    def _request(
        self,
        method: str,
        path_or_url: str,
        *,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> Any:
        url = path_or_url if path_or_url.startswith("http") else f"{self.base_url}{path_or_url}"
        response = self.session.request(method, url, params=params, headers=headers, timeout=30)
        response.raise_for_status()
        return response.json()

    def search_issues(
        self,
        owner: str,
        repo: str,
        query: str,
        *,
        sort: str = "created",
        order: str = "desc",
        per_page: int = 20,
    ) -> list[dict[str, Any]]:
        payload = self._request(
            "GET",
            "/search/issues",
            params={
                "q": f"repo:{owner}/{repo} {query}",
                "sort": sort,
                "order": order,
                "per_page": per_page,
            },
        )
        return [item for item in payload.get("items", []) if "pull_request" not in item]

    def get_issue(self, owner: str, repo: str, number: int) -> dict[str, Any]:
        return self._request("GET", f"/repos/{owner}/{repo}/issues/{number}")

    def get_issue_comments(
        self,
        owner: str,
        repo: str,
        number: int,
        *,
        max_comments: int = 30,
    ) -> list[dict[str, Any]]:
        comments: list[dict[str, Any]] = []
        page = 1
        while len(comments) < max_comments:
            batch = self._request(
                "GET",
                f"/repos/{owner}/{repo}/issues/{number}/comments",
                params={"per_page": min(100, max_comments), "page": page},
            )
            if not batch:
                break
            comments.extend(batch)
            if len(batch) < min(100, max_comments):
                break
            page += 1
        return comments[:max_comments]

    def get_issue_timeline(self, owner: str, repo: str, number: int) -> list[dict[str, Any]]:
        try:
            return self._request("GET", f"/repos/{owner}/{repo}/issues/{number}/timeline")
        except requests.HTTPError:
            return []


def extract_linked_pull_requests(timeline_events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    pull_requests: list[dict[str, Any]] = []
    seen_urls: set[str] = set()
    for event in timeline_events:
        source = event.get("source") or {}
        issue = source.get("issue") or {}
        pull_request = issue.get("pull_request")
        html_url = None
        if isinstance(pull_request, dict):
            html_url = pull_request.get("html_url")
        if not html_url:
            continue
        if html_url in seen_urls:
            continue
        seen_urls.add(html_url)
        pull_requests.append(
            {
                "number": issue.get("number"),
                "title": issue.get("title"),
                "html_url": html_url,
                "state": issue.get("state"),
                "event": event.get("event"),
                "created_at": event.get("created_at"),
            }
        )
    return pull_requests


def load_github_token() -> str | None:
    for env_name in ("GITHUB_TOKEN", "GH_TOKEN"):
        value = os.environ.get(env_name)
        if value:
            return value

    hosts_path = Path.home() / ".config" / "gh" / "hosts.yml"
    if not hosts_path.exists():
        return None

    try:
        payload = yaml.safe_load(hosts_path.read_text(encoding="utf-8")) or {}
    except Exception:
        return None

    github = payload.get("github.com") or {}
    token = github.get("oauth_token")
    if isinstance(token, str) and token.strip():
        return token.strip()
    return None
