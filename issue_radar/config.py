from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_REPOS_CONFIG = ROOT / "config" / "repos.yaml"
DEFAULT_PROFILE_CONFIG = ROOT / "config" / "profile.yaml"
DEFAULT_RAW_OUTPUT = ROOT / "data" / "raw" / "issues.json"
DEFAULT_ANALYZED_OUTPUT = ROOT / "data" / "enriched" / "issues.analyzed.json"
DEFAULT_ANALYSIS_STATE_FILE = ROOT / "data" / "state" / "analyzed_issues.json"
DEFAULT_STATE_FILE = ROOT / "data" / "state" / "notified_issues.json"


@dataclass
class RepoQuery:
    key: str
    owner: str
    repo: str
    query: str
    sort: str = "created"
    order: str = "desc"
    max_issues: int = 5
    max_comments: int = 30


@dataclass
class MonitorConfig:
    notify_threshold: int
    repositories: list[RepoQuery]


def _load_yaml(path: Path) -> dict[str, Any]:
    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"Config file must contain a mapping: {path}")
    return payload


def load_monitor_config(path: Path = DEFAULT_REPOS_CONFIG) -> MonitorConfig:
    payload = _load_yaml(path)
    repositories = [RepoQuery(**item) for item in payload.get("repositories", [])]
    if not repositories:
        raise ValueError(f"No repositories configured in {path}")
    return MonitorConfig(
        notify_threshold=int(payload.get("notify_threshold", 80)),
        repositories=repositories,
    )


def load_profile(path: Path = DEFAULT_PROFILE_CONFIG) -> dict[str, Any]:
    return _load_yaml(path)
