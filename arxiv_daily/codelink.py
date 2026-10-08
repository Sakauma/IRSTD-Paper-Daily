"""提取作者提供的代码地址，并通过 GitHub 搜索补查和校验仓库。"""

from __future__ import annotations

import logging
import os
import re
import time
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlsplit

import requests

logger = logging.getLogger(__name__)

GITHUB_SEARCH_URL = "https://api.github.com/search/repositories"
GITHUB_REPO_URL = "https://api.github.com/repos"
REQUEST_TIMEOUT = 15
SEARCH_RESULT_LIMIT = 5
MAX_RETRY_WAIT = 60

CODE_REPO_PATTERN = re.compile(
    r"https?://(?:"
    r"(?:github\.com|gitcode\.com|gitee\.com)/"
    r"[A-Za-z0-9][A-Za-z0-9_.-]*/[A-Za-z0-9_.-]+"
    r"|gitlab\.com/(?:[A-Za-z0-9_.-]+/)+[A-Za-z0-9_.-]+"
    r"|anonymous\.4open\.science/r/[A-Za-z0-9_.-]+"
    r")/?",
    flags=re.IGNORECASE,
)
MIRROR_SEPARATOR = re.compile(
    r"^[\s,()[\]{}<>]*"
    r"(?:(?:and|or)(?:\s+at)?|(?:mirror(?:ed)?|also)(?:\s+at)?\s*:?)?"
    r"[\s,()[\]{}<>]*$",
    flags=re.IGNORECASE,
)

# GitHub Search API 的未认证限制是 10 次/分钟，认证后通常为 30 次/分钟。
UNAUTHENTICATED_DELAY = 6.5
AUTHENTICATED_DELAY = 2.2

class CodeLookupError(RuntimeError):
    """代码仓库服务不可用；与确实没有匹配结果区分。"""


def extract_code_link(*texts: Optional[str]) -> Optional[str]:
    """提取明确的作者代码声明；并列镜像优先使用非匿名地址。"""
    declarations: List[Tuple[int, List[str]]] = []
    for text in texts:
        if not text:
            continue
        normalized = re.sub(r"\\([:/_.-])", r"\1", str(text))
        normalized = re.sub(r"\\\r?\n", " ", normalized)
        for paragraph in re.split(r"\n\s*\n", normalized):
            # arXiv may wrap the code cue and URL onto different lines.
            for sentence in re.split(r"(?<=[.!?;])\s+", " ".join(paragraph.split())):
                previous_end = 0
                current_urls: Optional[List[str]] = None
                for match in CODE_REPO_PATTERN.finditer(sentence):
                    context = sentence[previous_end:match.start()]
                    previous_end = match.end()
                    if re.search(r"\b(baseline|based on|build on|built on|compared? (?:to|with|against)|third.party)\b", context, re.I):
                        current_urls = None
                        continue
                    cue = re.search(
                        r"\b(?:(our|official)\s+)?(?:source\s+)?"
                        r"(?:codes?|implementation|project(?:\s+page)?|repository)\b"
                        r"[^.!?;\n]{0,100}$", context[-180:], re.I,
                    )
                    if cue:
                        current_urls = []
                        declarations.append((2 if cue.group(1) else 1, current_urls))
                    elif current_urls is None or not MIRROR_SEPARATOR.fullmatch(context):
                        current_urls = None
                        continue
                    parsed = urlsplit(match.group(0).rstrip(".,;:!?)]}'\""))
                    path = parsed.path.rstrip("/")
                    if parsed.netloc.lower() == "anonymous.4open.science":
                        path += "/"
                    else:
                        if parsed.netloc.lower() == "gitlab.com":
                            path = path.split("/-/", 1)[0]
                        if path.lower().endswith(".git"):
                            path = path[:-4]
                    url = f"https://{parsed.netloc.lower()}{path}"
                    if url not in current_urls:
                        current_urls.append(url)
    if not declarations:
        return None
    best_score = max(score for score, _ in declarations)
    best = {url for score, urls in declarations if score == best_score for url in urls}
    if len(best) == 1:
        return next(iter(best))
    for score, urls in declarations:
        if score == best_score and set(urls) == best:
            # Only links sharing one explicit code declaration count as mirrors.
            return next((url for url in urls if urlsplit(url).netloc != "anonymous.4open.science"), urls[0])
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
    retry_after = response.headers.get("Retry-After")
    if retry_after:
        try:
            return min(max(int(retry_after), 1), MAX_RETRY_WAIT)
        except (TypeError, ValueError):
            pass
    reset_value = response.headers.get("X-RateLimit-Reset", "0")
    try:
        reset_timestamp = int(reset_value)
    except (TypeError, ValueError):
        reset_timestamp = 0
    return min(max(reset_timestamp - int(time.time()), 30), MAX_RETRY_WAIT)


def _github_get(url: str, *, attempts: int = 3, **kwargs: Any) -> requests.Response:
    """网络错误、服务错误和限流均重试原请求；耗尽后明确报错。"""
    for attempt in range(attempts):
        try:
            response = requests.get(url, timeout=REQUEST_TIMEOUT, **kwargs)
        except requests.RequestException:
            if attempt + 1 == attempts:
                raise CodeLookupError("GitHub 请求失败，保留已有数据供下次重试") from None
            time.sleep(2 ** attempt)
            continue
        limited = response.status_code == 429 or (
            response.status_code == 403 and (
                response.headers.get("X-RateLimit-Remaining") == "0"
                or bool(response.headers.get("Retry-After"))
                or "rate limit" in response.text.lower()
            )
        )
        if limited or response.status_code >= 500:
            if attempt + 1 == attempts:
                raise CodeLookupError(f"GitHub 请求重试耗尽（HTTP {response.status_code}）")
            time.sleep(_rate_limit_wait(response) if limited else 2 ** attempt)
            continue
        if response.status_code not in (200, 404):
            raise CodeLookupError(f"GitHub 请求失败（HTTP {response.status_code}）")
        return response
    raise CodeLookupError("GitHub 请求重试次数必须大于零")


def _search_repositories(query: str) -> List[str]:
    """搜索仓库；仅成功且无结果时返回空列表。"""
    params = {
        "q": query,
        "sort": "stars",
        "order": "desc",
        "per_page": SEARCH_RESULT_LIMIT,
    }
    response = _github_get(GITHUB_SEARCH_URL, params=params, headers=_headers())
    if response.status_code == 404:
        raise CodeLookupError("GitHub 搜索接口不可用（HTTP 404）")
    try:
        payload = response.json()
    except ValueError:
        raise CodeLookupError("GitHub 搜索返回了无效 JSON") from None
    if not isinstance(payload, dict) or not isinstance(payload.get("items"), list):
        raise CodeLookupError("GitHub 搜索响应缺少有效的 items 列表")
    items = payload["items"]
    return [
        str(item["html_url"])
        for item in items
        if isinstance(item, dict) and item.get("html_url")
    ]


def _candidate_queries(arxiv_id: str, title: str) -> List[str]:
    """生成按可靠性排序的 GitHub 搜索词。"""
    queries = [f'"{arxiv_id}" in:readme']
    title_head = title.split(":", 1)[0].strip()

    if ":" in title and title_head and len(title_head) <= 40:
        queries.append(f'"{title_head}" in:name,description,readme')
    elif len(title.split()) <= 5 and title:
        queries.append(f'"{title}" in:name,description,readme')

    short_title = title[:80].strip()
    if short_title:
        queries.append(f'"{short_title}" in:readme')
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


def _fetch_readme(owner: str, repo: str, retries: int = 3) -> str:
    """读取 README；不存在时返回空串，临时故障抛错以免清除有效链接。"""
    url = f"{GITHUB_REPO_URL}/{owner}/{repo}/readme"
    response = _github_get(
        url, attempts=retries,
        headers={**_headers(), "Accept": "application/vnd.github.raw"},
    )
    return response.text if response.status_code == 200 else ""


def verify_code_link(arxiv_id: str, title: str, html_url: str) -> bool:
    """校验候选仓库是否与论文相关。

    README 必须同时有论文身份（精确 ID 或完整标题）和实现说明。
    通用词重合、仓库名相似和仅列出论文的目录都不足以证明是代码仓库。
    """
    parsed = urlsplit(html_url)
    parts = parsed.path.strip("/").split("/")
    if parsed.scheme not in ("https", "http") or parsed.netloc.lower() != "github.com" or len(parts) != 2:
        return False
    owner, repo = parts
    readme = _fetch_readme(owner, repo)
    if not readme:
        return False
    if re.search(r"(?:^|[-_])(awesome|papers|survey)(?:$|[-_])", repo, re.I):
        return False
    # Exclude bibliography and paper-list sections from identity evidence.
    introduction = re.split(
        r"(?im)^#{1,6}\s+(?:references|related (?:work|papers)|paper list)\b", readme,
    )[0]
    identity = bool(re.search(rf"(?<![\w.]){re.escape(arxiv_id)}(?:v\d+)?(?!\w)", introduction))
    normalized_title = " ".join(re.findall(r"[a-z0-9]+", title.lower()))
    normalized_readme = " ".join(re.findall(r"[a-z0-9]+", introduction.lower()))
    identity = identity or (len(normalized_title.split()) >= 4 and normalized_title in normalized_readme)
    implementation = re.search(
        r"\b(?:official\s+(?:(?:pytorch|tensorflow)\s+)?(?:implementation|code|repository)|"
        r"(?:code|implementation)\s+(?:for|of|accompanying)\s+(?:the|our|this)\s+(?:paper|work|method))\b",
        introduction, re.I,
    )
    return bool(identity and implementation)


def backfill_code_links(data: Dict[str, Any]) -> Tuple[Dict[str, Any], int]:
    """补查缺失链接并复核旧搜索结果；保留明确的元数据链接和人工覆盖。"""
    updated = 0
    papers_to_update = [
        paper
        for papers in data.values()
        if isinstance(papers, dict)
        for paper in papers.values()
        if isinstance(paper, dict) and paper.get("code_source") not in ("arxiv_metadata", "manual")
    ]
    total = len(papers_to_update)
    done = 0

    for topic, papers in data.items():
        if not isinstance(papers, dict):
            continue
        for paper_id, paper in papers.items():
            if not isinstance(paper, dict) or paper.get("code_source") in ("arxiv_metadata", "manual"):
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
            previous = paper.get("code")
            if previous and verify_code_link(str(paper_id), title, str(previous)):
                candidate = str(previous)
            else:
                candidate = lookup_code_link(str(paper_id), title)
            # Do not mutate a record until both verification and lookup complete.
            paper["code"] = candidate
            if candidate:
                paper["code_source"] = "github_verified"
            else:
                paper.pop("code_source", None)
            if candidate != previous:
                updated += 1
                logger.info("代码链接已调整: %s", paper_id)

    return data, updated
