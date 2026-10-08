"""arXiv 论文抓取模块。"""

from __future__ import annotations

import logging
import re
from typing import Any, Dict, Iterable, List, Optional, Set

import arxiv

from .codelink import extract_code_link, lookup_code_link

logger = logging.getLogger(__name__)

ARXIV_ABS_URL = "http://arxiv.org/abs/{}"
ARXIV_ID_BATCH_SIZE = 100


def _strip_version(paper_id: str) -> str:
    """去掉 arXiv ID 的版本后缀，如 ``2108.09112v1``。"""
    normalized = paper_id.strip()
    normalized = re.sub(r"^arXiv:", "", normalized, flags=re.IGNORECASE)
    return re.sub(r"v\d+$", "", normalized)


def _paper_from_result(
    result: arxiv.Result,
    *,
    known_codes: Optional[Dict[str, str]] = None,
    known_paper_ids: Optional[Set[str]] = None,
    lookup_missing_code: bool = True,
    known_code_sources: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    """把搜索或 ID 查询结果转成同一种记录，统一保留人工代码链接。"""
    paper_id = _strip_version(result.get_short_id())
    logger.info("抓取到论文 %s | %s", paper_id, result.title)

    cached_code = (known_codes or {}).get(paper_id)
    source = (known_code_sources or {}).get(paper_id, "legacy")
    code = extract_code_link(
        getattr(result, "summary", None),
        getattr(result, "comment", None),
    )
    if cached_code and source == "manual":
        code = cached_code
    elif code:
        source = "arxiv_metadata"
        logger.info("从 arXiv 元数据提取到官方代码链接: %s", code)
    else:
        code = cached_code

    is_new_paper = known_paper_ids is None or paper_id not in known_paper_ids
    if lookup_missing_code and is_new_paper and not code:
        code = lookup_code_link(paper_id, result.title)
        source = "github_verified"

    authors = [str(author) for author in (result.authors or [])]
    updated = result.updated or result.published
    return {
        "id": paper_id,
        # 与参考项目一致：展示论文最近更新日期。
        "publish_date": str(updated.date()),
        "title": str(result.title).replace("\n", " "),
        "first_author": authors[0] if authors else "",
        "authors": ", ".join(authors),
        "url": ARXIV_ABS_URL.format(paper_id),
        "code": code,
        **({"code_source": source} if code else {}),
    }


def fetch_daily_papers(
    topic: str,
    query: str,
    max_results: Optional[int],
    known_codes: Optional[Dict[str, str]] = None,
    known_paper_ids: Optional[Set[str]] = None,
    lookup_missing_code: bool = True,
    known_code_sources: Optional[Dict[str, str]] = None,
) -> List[Dict[str, Any]]:
    """按搜索表达式抓取指定数量的最新论文。

    优先使用论文摘要/备注中明确的作者代码地址（支持多种托管平台），保留人工覆盖。
    ``known_codes`` 用于复用历史
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
        papers.append(
            _paper_from_result(
                result,
                known_codes=known_codes,
                known_paper_ids=known_paper_ids,
                lookup_missing_code=lookup_missing_code,
                known_code_sources=known_code_sources,
            )
        )

    logger.info("领域 %s 共抓取 %d 篇论文", topic, len(papers))
    return papers


def fetch_papers_by_id(
    paper_ids: Iterable[str],
    *,
    known_codes: Optional[Dict[str, str]] = None,
    known_code_sources: Optional[Dict[str, str]] = None,
) -> List[Dict[str, Any]]:
    """分批按无版本 ID 获取历史论文的最新版本，不受首次提交日期限制。

    复用同一客户端以保留批次间的请求间隔。这里只提取元数据中的代码链接，
    GitHub 补查由调用方在合并新旧论文后统一执行。
    """
    ids = list(dict.fromkeys(
        normalized for paper_id in paper_ids
        if (normalized := _strip_version(paper_id))
    ))
    if not ids:
        return []
    client = arxiv.Client()
    papers: List[Dict[str, Any]] = []
    for offset in range(0, len(ids), ARXIV_ID_BATCH_SIZE):
        batch = ids[offset:offset + ARXIV_ID_BATCH_SIZE]
        search = arxiv.Search(id_list=batch, max_results=len(batch))
        missing = set(batch)
        for result in client.results(search):
            paper_id = _strip_version(result.get_short_id())
            if paper_id not in missing:
                continue
            papers.append(_paper_from_result(
                result,
                known_codes=known_codes,
                lookup_missing_code=False,
                known_code_sources=known_code_sources,
            ))
            missing.remove(paper_id)
        if missing:
            logger.warning("arXiv 未返回以下历史论文，保留缓存: %s", ", ".join(sorted(missing)))

    logger.info("历史论文检查完成，获取 %d/%d 篇最新元数据", len(papers), len(ids))
    return papers
