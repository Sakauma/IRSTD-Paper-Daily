"""arXiv 论文抓取模块。"""

from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional, Set

import arxiv

from .codelink import extract_code_link, lookup_code_link

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
    max_results: Optional[int],
    known_codes: Optional[Dict[str, str]] = None,
    known_paper_ids: Optional[Set[str]] = None,
    lookup_missing_code: bool = True,
) -> List[Dict[str, Any]]:
    """按搜索表达式抓取指定数量的最新论文。

    论文摘要/备注中的 GitHub 地址优先级最高。``known_codes`` 用于复用历史
    链接；``known_paper_ids`` 避免每天为已有但无代码的论文重复搜索 GitHub。
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

        code = extract_code_link(
            getattr(result, "summary", None),
            getattr(result, "comment", None),
        )
        if code:
            logger.info("从 arXiv 元数据提取到官方代码链接: %s", code)
        elif known_codes is not None:
            code = known_codes.get(paper_id)

        is_new_paper = known_paper_ids is None or paper_id not in known_paper_ids
        if lookup_missing_code and is_new_paper and not code:
            code = lookup_code_link(paper_id, result.title)

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
