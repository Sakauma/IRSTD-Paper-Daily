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
SEARCH_ATTEMPTS = 3
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

IMPLEMENTATION_CUE = re.compile(
    r"\b(?:official\s+(?:(?:pytorch|tensorflow)\s+)?(?:implementation|code|repository)|"
    r"(?:code|implementation)\s+(?:for|of|accompanying)\s+(?:the|our|this)\s+(?:paper|work|method))\b",
    re.I,
)
REFERENCE_HEADING = re.compile(
    r"^(?:\d+\s+)*(?:references?|bibliography|related (?:work|papers)|paper list|"
    r"acknowledg(?:e)?ments?|credits?|baselines?|comparisons?)\b", re.I,
)
# Only grammatical connectors may bridge a claim and its subject. Arbitrary
# prose such as "another method; we compare with ..." is not an association.
SUBJECT_CONNECTORS = {
    "of", "for", "the", "our", "this", "paper", "work", "method",
    "titled", "entitled", "called", "arxiv",
}
SELF_CLAIM_PREFIX = {
    "this", "the", "our", "repository", "repo", "project", "codebase",
    "is", "an", "a", "we", "provide", "provides", "present", "presents",
    "release", "releases", "contains", "contain", "here", "it",
}


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
    """搜索仓库；不完整响应重试原查询，只有完整结果才能判定未找到。"""
    params = {
        "q": query,
        "sort": "stars",
        "order": "desc",
        "per_page": SEARCH_RESULT_LIMIT,
    }
    for attempt in range(SEARCH_ATTEMPTS):
        response = _github_get(GITHUB_SEARCH_URL, params=params, headers=_headers())
        if response.status_code == 404:
            raise CodeLookupError("GitHub 搜索接口不可用（HTTP 404）")
        try:
            payload = response.json()
        except ValueError:
            raise CodeLookupError("GitHub 搜索返回了无效 JSON") from None
        if not isinstance(payload, dict) or not isinstance(payload.get("items"), list):
            raise CodeLookupError("GitHub 搜索响应缺少有效的 items 列表")
        if payload.get("incomplete_results", False):
            if attempt + 1 < SEARCH_ATTEMPTS:
                logger.warning("GitHub 搜索结果不完整，将重试原查询 (%d/%d)", attempt + 2, SEARCH_ATTEMPTS)
                time.sleep(_request_delay() * (attempt + 1))
            continue
        return [
            str(item["html_url"])
            for item in payload["items"]
            if isinstance(item, dict) and item.get("html_url")
        ]
    raise CodeLookupError("GitHub 搜索结果持续不完整，保留已有数据供下次重试")


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


def _words(text: str) -> List[str]:
    return re.findall(r"[a-z0-9]+", text.lower())


def _readme_blocks(readme: str) -> List[str]:
    """保留标题/段落边界，排除参考章节、列表、代码块及 HTML 注释。"""
    readme = re.sub(r"<!--.*?-->", "", readme, flags=re.S)
    # Setext and HTML headings have the same section semantics as Markdown #.
    readme = re.sub(
        r"(?m)^([^\n]+)\n[ \t]*([=-])\2{2,}[ \t]*$",
        lambda match: ("# " if match[2] == "=" else "## ") + match[1], readme,
    )
    readme = re.sub(
        r"<h([1-6])\b[^>]*>(.*?)</h\1>",
        lambda match: "\n" + "#" * int(match[1]) + " " + match[2] + "\n",
        readme, flags=re.I | re.S,
    )
    blocks: List[str] = []
    paragraph: List[str] = []
    skipped_level = None
    fence = ""
    list_paragraph = False

    def flush() -> None:
        if paragraph:
            blocks.append(" ".join(paragraph))
            paragraph.clear()

    for line in readme.splitlines():
        marker = re.match(r"^\s{0,3}(`{3,}|~{3,})", line)
        if marker:
            flush()
            blocks.append("")
            if not fence:
                fence = marker[1]
            elif marker[1][0] == fence[0] and len(marker[1]) >= len(fence):
                fence = ""
            continue
        if fence:
            continue
        heading = re.match(r"^\s{0,3}(#{1,6})\s+(.+)", line)
        if heading:
            flush()
            list_paragraph = False
            level = len(heading[1])
            if skipped_level is not None and level <= skipped_level:
                skipped_level = None
            if skipped_level is None and REFERENCE_HEADING.match(" ".join(_words(heading[2]))):
                skipped_level = level
            blocks.append(heading[2] if skipped_level is None else "")
        elif skipped_level is not None:
            continue
        elif not line.strip():
            flush()
            list_paragraph = False
        elif list_paragraph:
            continue
        elif re.match(r"^\s*(?:[-+*]|\d+[.)])\s", line):
            flush()
            blocks.append("")
            list_paragraph = True
        elif re.match(r"^(?: {4}|\t|\s{0,3}>)", line):
            flush()
            blocks.append("")
        else:
            paragraph.append(line.strip())
    flush()
    return blocks


def _paper_identity_pattern(arxiv_id: str, title: str) -> re.Pattern[str]:
    paper_id = re.escape(arxiv_id)
    paper_url = rf"https?://(?:www\.)?arxiv\.org/(?:abs|pdf)/{paper_id}(?:v\d+)?(?:\.pdf)?/?"
    identities = [
        # Treat a paper link as one subject, including its Markdown label.
        rf"\[[^\]\n]+\]\({paper_url}\)",
        rf"{paper_url}(?!\w)",
        rf"(?<![\w.])(?:arxiv:\s*)?{paper_id}(?:v\d+)?(?!\w)",
    ]
    title_words = _words(title)
    if len(title_words) >= 4:
        identities.append(r"\b" + r"[^a-z0-9]+".join(map(re.escape, title_words)) + r"\b")
    return re.compile("|".join(identities), re.I)


def _has_implementation_evidence(readme: str, arxiv_id: str, title: str) -> bool:
    """声明必须直接指向论文，或紧随独立的论文标题/链接。"""
    identity = _paper_identity_pattern(arxiv_id, title)

    def paper_subject(text: str) -> bool:
        return bool(identity.search(text)) and set(_words(identity.sub("", text))) <= {"paper", "arxiv"}

    def self_claim(text: str) -> bool:
        return any(
            set(_words(text[:cue.start()])) <= SELF_CLAIM_PREFIX
            and set(_words(text[cue.end():])) <= SUBJECT_CONNECTORS | {"in", "pytorch", "tensorflow"}
            for cue in IMPLEMENTATION_CUE.finditer(text)
        )

    previous_subject = False
    for block in _readme_blocks(readme):
        for cue in IMPLEMENTATION_CUE.finditer(block):
            # A preceding sentence can describe the method, but the claim itself
            # must describe this repository, not code borrowed from a baseline.
            prefix = re.split(r"(?<=[.!?;])\s+", block[:cue.start()])[-1]
            if set(_words(prefix)) <= SELF_CLAIM_PREFIX:
                for subject in identity.finditer(block, cue.end()):
                    if set(_words(block[cue.end():subject.start()])) <= SUBJECT_CONNECTORS:
                        return True
        for subject in identity.finditer(block):
            if paper_subject(block[:subject.end()]) and self_claim(block[subject.end():]):
                return True
        if previous_subject and self_claim(block):
            return True
        previous_subject = paper_subject(block)
    return False


def verify_code_link(arxiv_id: str, title: str, html_url: str) -> bool:
    """校验候选仓库是否与论文相关。

    README 的实现声明必须直接关联论文身份（精确 ID 或完整标题）。
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

    def mask_external_code(match: re.Match[str]) -> str:
        linked = urlsplit(match[1])
        linked_repo = linked.path.strip("/").split("/")[:2]
        if CODE_REPO_PATTERN.match(match[1]) and (
            linked.netloc.lower() != "github.com"
            or "/".join(linked_repo).removesuffix(".git").lower() != f"{owner}/{repo}".lower()
        ):
            # Keep a barrier: deleting a foreign link could falsely join a
            # preceding implementation claim to the next paper reference.
            return " external repository reference "
        return match[0]

    readme = re.sub(r"\[[^\]\n]+\]\((https?://[^\s)]+)\)", mask_external_code, readme)
    return _has_implementation_evidence(readme, arxiv_id, title)


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
