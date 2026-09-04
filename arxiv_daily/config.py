"""配置加载与 arXiv 搜索表达式构建。"""

from __future__ import annotations

import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

import yaml


def _date_boundary(value: str, *, end_of_day: bool) -> str:
    """把 ISO 日期转换为 arXiv ``submittedDate`` 边界。"""
    parsed = datetime.date.fromisoformat(value)
    suffix = "2359" if end_of_day else "0000"
    return parsed.strftime("%Y%m%d") + suffix


def build_query(
    filters: Iterable[str] | None,
    *,
    start_date: str | None = None,
    end_date: str | None = None,
) -> str:
    """把多个过滤词用 ``OR`` 连接成 arXiv 搜索表达式。

    含空格的短语会自动加双引号，保证按整体短语搜索；单词保持原样。
    空过滤词会被忽略。
    """
    if filters is None:
        return ""

    parts: List[str] = []
    for raw_keyword in filters:
        keyword = str(raw_keyword).strip()
        if not keyword:
            continue
        if any(char.isspace() for char in keyword):
            parts.append(f'"{keyword}"')
        else:
            parts.append(keyword)
    keyword_query = " OR ".join(parts)
    if not start_date:
        return keyword_query

    effective_end_date = end_date or datetime.date.today().isoformat()
    start_boundary = _date_boundary(start_date, end_of_day=False)
    end_boundary = _date_boundary(effective_end_date, end_of_day=True)
    if start_boundary > end_boundary:
        raise ValueError("start_date 不能晚于 end_date")

    date_query = f"submittedDate:[{start_boundary} TO {end_boundary}]"
    return f"({keyword_query}) AND {date_query}" if keyword_query else date_query


def load_config(config_path: str | Path) -> Dict[str, Any]:
    """读取 YAML 配置并解析启用的领域。

    返回值在原始配置基础上增加 ``kv`` 和 ``domain_max_results``，
    同时兼容参考项目早期使用的 ``keywords`` 结构。
    """
    with Path(config_path).open("r", encoding="utf-8") as file:
        config = yaml.safe_load(file) or {}

    if not isinstance(config, dict):
        raise ValueError("配置文件顶层必须是 YAML 对象")

    raw_domains = config.get("domains") or config.get("keywords") or {}
    if not isinstance(raw_domains, dict):
        raise ValueError("domains/keywords 必须是 YAML 对象")

    global_max_results = config.get("max_results", 10)
    global_start_date = config.get("start_date")
    kv: Dict[str, str] = {}
    domain_max_results: Dict[str, Optional[int]] = {}

    for topic, domain_config in raw_domains.items():
        if isinstance(domain_config, dict):
            if not domain_config.get("enable", True):
                continue
            filters = domain_config.get("filters", [])
            max_results = domain_config.get("max_results", global_max_results)
            start_date = domain_config.get("start_date", global_start_date)
        else:
            # 兼容旧版 keywords: {"Topic": ["keyword", ...]}
            filters = domain_config
            max_results = global_max_results
            start_date = global_start_date

        if isinstance(filters, str):
            filters = [filters]
        if filters is None:
            filters = []
        if not isinstance(filters, (list, tuple)):
            raise ValueError(f"领域 {topic!r} 的 filters 必须是列表")

        parsed_max_results: Optional[int]
        if max_results is None:
            parsed_max_results = None
        else:
            parsed_max_results = int(max_results)
            if parsed_max_results < 1:
                raise ValueError(f"领域 {topic!r} 的 max_results 必须大于 0 或为 null")

        parsed_start_date = str(start_date) if start_date else None

        topic_name = str(topic)
        kv[topic_name] = build_query(filters, start_date=parsed_start_date)
        domain_max_results[topic_name] = parsed_max_results

    config["kv"] = kv
    config["domain_max_results"] = domain_max_results
    return config
