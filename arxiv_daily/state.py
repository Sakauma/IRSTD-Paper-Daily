"""把抓取进度绑定到缓存和查询，把通知进度绑定到接收目标。"""

from __future__ import annotations

import hashlib
import json
import os
from typing import Any, Mapping


def fingerprint(value: Any) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def query_fingerprint(query: str, start_date: str | None, max_results: int | None) -> str:
    return fingerprint({"query": query.strip(), "start_date": start_date, "max_results": max_results})


def notification_target(config: Mapping[str, Any], provider: str) -> str | None:
    sendkey = os.environ.get("SERVERCHAN_SENDKEY", "").strip()
    if not sendkey:
        return None
    repository = os.environ.get("GITHUB_REPOSITORY") or (
        f"{config.get('user_name', '')}/{config.get('repo_name', '')}"
    )
    # Only a one-way fingerprint is persisted; the SendKey never leaves the environment.
    return fingerprint([provider, repository.strip().lower(), sendkey])


def catalog_fingerprints(data: Mapping[str, Any]) -> dict[str, dict[str, str]]:
    fields = ("id", "publish_date", "title", "first_author", "authors", "url", "code")
    return {
        str(topic): {
            str(paper_id): fingerprint({key: paper.get(key) for key in fields})
            for paper_id, paper in papers.items() if isinstance(paper, dict)
        }
        for topic, papers in data.items() if isinstance(papers, dict)
    }


def changes_since_notification(
    data: Mapping[str, Any], delivered: Mapping[str, Any],
) -> tuple[dict[str, list[dict[str, Any]]], dict[str, list[dict[str, Any]]]]:
    current = catalog_fingerprints(data)
    new, updated = {}, {}
    for topic, papers in current.items():
        previous = delivered.get(topic, {})
        if not isinstance(previous, dict):
            previous = {}
        new[topic] = [data[topic][pid] for pid in papers if pid not in previous]
        updated[topic] = [
            data[topic][pid] for pid, digest in papers.items()
            if pid in previous and previous[pid] != digest
        ]
    return new, updated
