"""通过 GitHub 搜索 API 查找并校验论文代码仓库。"""

from __future__ import annotations

import logging
import os
import re
import time
from typing import Any, Dict, List, Optional, Set, Tuple

import requests

logger = logging.getLogger(__name__)

GITHUB_SEARCH_URL = "https://api.github.com/search/repositories"
GITHUB_REPO_URL = "https://api.github.com/repos"
REQUEST_TIMEOUT = 15
SEARCH_RESULT_LIMIT = 5

GITHUB_REPO_PATTERN = re.compile(
    r"https?://github\.com/[A-Za-z0-9][A-Za-z0-9.-]*/[A-Za-z0-9_.-]+",
    flags=re.IGNORECASE,
)

# GitHub Search API 的未认证限制是 10 次/分钟，认证后通常为 30 次/分钟。
UNAUTHENTICATED_DELAY = 6.5
AUTHENTICATED_DELAY = 2.2

_STOPWORDS: Set[str] = {
    "a", "an", "the", "and", "or", "of", "on", "in", "to", "with",
    "via", "using", "based", "from", "by", "at", "is", "are", "for",
    "towards", "toward", "over", "under", "into", "its", "it", "this",
    "that",
}


def _tokens(text: str) -> Set[str]:
    """把文本切成小写词元，去掉停用词和单字母词。"""
    words = re.findall(r"[a-z0-9]+", text.lower())
    return {word for word in words if word not in _STOPWORDS and len(word) > 1}


def extract_code_link(*texts: Optional[str]) -> Optional[str]:
    """从论文摘要或备注中提取作者提供的 GitHub 仓库地址。"""
    for text in texts:
        if not text:
            continue
        match = GITHUB_REPO_PATTERN.search(str(text))
        if not match:
            continue
        url = match.group(0).rstrip(".,;:!?)]}'\"")
        if url.lower().endswith(".git"):
            url = url[:-4]
        return url
    return None


def _headers() -> Dict[str, str]:
    """构造 GitHub API 请求头；token 只从环境变量读取。"""
    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        return {}
    return {"Authorization": f"token {token}"}


def _request_delay() -> float:
    return AUTHENTICATED_DELAY if os.environ.get("GITHUB_TOKEN") else UNAUTHENTICATED_DELAY


def _rate_limit_wait(response: requests.Response) -> int:
    reset_value = response.headers.get("X-RateLimit-Reset", "0")
    try:
        reset_timestamp = int(reset_value)
    except (TypeError, ValueError):
        reset_timestamp = 0
    return max(reset_timestamp - int(time.time()), 30)


def _search_repositories(query: str) -> List[str]:
    """搜索仓库并返回若干候选地址；请求失败返回空列表。"""
    params = {
        "q": query,
        "sort": "stars",
        "order": "desc",
        "per_page": SEARCH_RESULT_LIMIT,
    }
    response: Optional[requests.Response] = None

    for attempt in range(3):
        try:
            response = requests.get(
                GITHUB_SEARCH_URL,
                params=params,
                headers=_headers(),
                timeout=REQUEST_TIMEOUT,
            )
            break
        except requests.RequestException as exc:
            logger.warning("GitHub 搜索请求失败（第 %d 次）: %s", attempt + 1, exc)
            if attempt < 2:
                time.sleep(5)

    if response is None:
        return []

    if response.status_code in (403, 429):
        wait = _rate_limit_wait(response)
        logger.warning("GitHub API 限流，等待 %d 秒后继续", wait)
        time.sleep(wait)
        return []
    if response.status_code != 200:
        logger.warning("GitHub 搜索失败: HTTP %s", response.status_code)
        return []

    items = response.json().get("items") or []
    return [
        str(item["html_url"])
        for item in items
        if isinstance(item, dict) and item.get("html_url")
    ]


def _candidate_queries(arxiv_id: str, title: str) -> List[str]:
    """生成按可靠性排序的 GitHub 搜索词。"""
    queries = [f'"{arxiv_id}"']
    title_head = title.split(":", 1)[0].strip()

    if ":" in title and title_head and len(title_head) <= 40:
        queries.append(f'"{title_head}"')
    elif len(title.split()) <= 5 and title:
        queries.append(f'"{title}"')

    short_title = title[:80].strip()
    if short_title:
        queries.append(f'"{short_title}"')
    return list(dict.fromkeys(queries))


def lookup_code_link(arxiv_id: str, title: str) -> Optional[str]:
    """搜索并逐个校验候选仓库，返回首个可信代码链接。"""
    queries = _candidate_queries(arxiv_id, title)
    for index, query in enumerate(queries):
        for candidate in _search_repositories(query):
            if verify_code_link(arxiv_id, title, candidate):
                return candidate
            logger.info("候选代码链接未通过校验，继续尝试: %s", candidate)
        if index < len(queries) - 1:
            time.sleep(_request_delay())
    return None


def _fetch_readme(owner: str, repo: str, retries: int = 2) -> str:
    """读取仓库 README 原文；失败返回空字符串。"""
    url = f"{GITHUB_REPO_URL}/{owner}/{repo}/readme"
    response: Optional[requests.Response] = None

    for attempt in range(retries):
        try:
            response = requests.get(
                url,
                headers={**_headers(), "Accept": "application/vnd.github.raw"},
                timeout=REQUEST_TIMEOUT,
            )
        except requests.RequestException as exc:
            logger.warning("README 请求失败 %s: %s", url, exc)
            if attempt < retries - 1:
                time.sleep(5)
            continue

        if response.status_code in (403, 429):
            wait = _rate_limit_wait(response)
            logger.warning("README 请求限流，等待 %d 秒后重试", wait)
            time.sleep(wait)
            continue
        break

    if response is None or response.status_code != 200:
        return ""
    return response.text


def verify_code_link(arxiv_id: str, title: str, html_url: str) -> bool:
    """校验候选仓库是否与论文相关。

    仓库名/README 包含 arXiv ID，或与标题共享至少两个特征词时通过校验。
    网络读取失败视为不通过，避免把无关仓库写入日报。
    """
    parts = html_url.rstrip("/").split("/")
    if len(parts) < 5 or parts[-3] != "github.com":
        return False
    owner, repo = parts[-2], parts[-1]
    combined = f"{owner} {repo} {_fetch_readme(owner, repo)}"

    if arxiv_id in combined:
        return True

    overlap = _tokens(combined) & _tokens(title)
    long_overlap = {token for token in overlap if len(token) >= 5}
    return len(overlap) >= 2 and len(long_overlap) >= 2


def backfill_code_links(data: Dict[str, Any]) -> Tuple[Dict[str, Any], int]:
    """为所有缺少代码链接的结构化论文记录回填链接。"""
    updated = 0
    papers_to_update = [
        paper
        for papers in data.values()
        if isinstance(papers, dict)
        for paper in papers.values()
        if isinstance(paper, dict) and not paper.get("code")
    ]
    total = len(papers_to_update)
    done = 0

    for topic, papers in data.items():
        if not isinstance(papers, dict):
            continue
        for paper_id, paper in papers.items():
            if not isinstance(paper, dict) or paper.get("code"):
                continue
            done += 1
            title = str(paper.get("title", ""))
            logger.info(
                "查找代码链接 (%d/%d): %s %s",
                done,
                total,
                paper_id,
                title[:50],
            )
            candidate = lookup_code_link(str(paper_id), title)
            if candidate:
                paper["code"] = candidate
                updated += 1
                logger.info("找到并校验通过: %s", candidate)
            else:
                logger.info("未找到或校验未通过: %s", paper_id)

    return data, updated
