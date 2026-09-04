"""arXiv 论文抓取模块。"""

from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional

import arxiv

from .codelink import lookup_code_link, verify_code_link

logger = logging.getLogger(__name__)

ARXIV_ABS_URL = "http://arxiv.org/abs/{}"


def _strip_version(paper_id: str) -> str:
    """去掉 arXiv ID 的版本后缀，如 ``2108.09112v1``。"""
    normalized = paper_id.strip()
    normalized = re.sub(r"^arXiv:", "", normalized, flags=re.IGNORECASE)
    return re.sub(r"v\d+$", "", normalized)


def fetch_daily_papers(
    topic: str,
    query: str,
    max_results: int,
    known_codes: Optional[Dict[str, str]] = None,
    lookup_missing_code: bool = True,
) -> List[Dict[str, Any]]:
    """按搜索表达式抓取指定数量的最新论文。

    ``known_codes`` 用于复用历史链接；``lookup_missing_code`` 控制是否为
    没有缓存链接的新论文访问 GitHub Search API。
    """
    search = arxiv.Search(
        query=query,
        max_results=max_results,
        sort_by=arxiv.SortCriterion.SubmittedDate,
    )
    client = arxiv.Client()
    papers: List[Dict[str, Any]] = []

    for result in client.results(search):
        paper_id = _strip_version(result.get_short_id())
        logger.info("抓取到论文 %s | %s", paper_id, result.title)

        code: Optional[str] = None
        if known_codes is not None:
            code = known_codes.get(paper_id)
            if lookup_missing_code and not code:
                candidate = lookup_code_link(paper_id, result.title)
                if candidate and verify_code_link(paper_id, result.title, candidate):
                    code = candidate
                elif candidate:
                    logger.info("候选代码链接未通过校验，忽略: %s", candidate)

        authors = [str(author) for author in (result.authors or [])]
        updated = result.updated or result.published
        papers.append(
            {
                "id": paper_id,
                # 与参考项目一致：展示论文最近更新日期。
                "publish_date": str(updated.date()),
                "title": str(result.title).replace("\n", " "),
                "first_author": authors[0] if authors else "",
                "authors": ", ".join(authors),
                "url": ARXIV_ABS_URL.format(paper_id),
                "code": code,
            }
        )

    logger.info("领域 %s 共抓取 %d 篇论文", topic, len(papers))
    return papers
