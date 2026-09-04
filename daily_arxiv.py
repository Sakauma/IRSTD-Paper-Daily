"""每日抓取 arXiv 论文并生成 README、GitHub Pages 和微信版日报。

用法：
    python daily_arxiv.py [--config_path config.yaml]
    python daily_arxiv.py --backfill_code
    python daily_arxiv.py --full-refresh
"""

from __future__ import annotations

import argparse
import logging
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Dict, Optional

from arxiv_daily.codelink import backfill_code_links
from arxiv_daily.config import add_date_range, load_config
from arxiv_daily.fetcher import fetch_daily_papers
from arxiv_daily.renderer import render_markdown
from arxiv_daily.storage import load_data, merge_papers, save_data
from arxiv_daily.wechat import build_wechat_data, render_wechat_markdown

logging.basicConfig(
    format="[%(asctime)s %(levelname)s] %(message)s",
    datefmt="%m/%d/%Y %H:%M:%S",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)


def _write_markdown(path: str | Path, content: str) -> None:
    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(content, encoding="utf-8")
    logger.info("已生成 %s", output_path)


def _render_outputs(config: Dict[str, Any], data: Dict[str, Any]) -> None:
    """按发布开关渲染三类日报产物。"""
    user_name = str(config.get("user_name", ""))
    repo_name = str(config.get("repo_name", ""))
    show_badge = bool(config.get("show_badge", True))
    show_authors = bool(config.get("show_authors", True))
    show_links = bool(config.get("show_links", True))

    if config.get("publish_readme", True):
        content = render_markdown(
            data,
            format="readme",
            show_badge=show_badge,
            user_name=user_name,
            repo_name=repo_name,
            show_authors=show_authors,
            show_links=show_links,
        )
        _write_markdown(config["md_readme_path"], content)

    if config.get("publish_gitpage", True):
        content = render_markdown(
            data,
            format="web",
            show_authors=show_authors,
            show_links=show_links,
        )
        _write_markdown(config["md_gitpage_path"], content)

    if config.get("publish_wechat", False):
        wechat_data = build_wechat_data(data)
        save_data(config["wechat_data_path"], wechat_data)
        content = render_wechat_markdown(
            wechat_data,
            show_badge=show_badge,
            user_name=user_name,
            repo_name=repo_name,
        )
        _write_markdown(config["md_wechat_path"], content)


def _known_code_links(data: Dict[str, Any]) -> Dict[str, str]:
    """构建论文 ID 到代码链接的缓存，兼容历史中的非结构化条目。"""
    known: Dict[str, str] = {}
    for papers in data.values():
        if not isinstance(papers, dict):
            continue
        for paper_id, paper in papers.items():
            if isinstance(paper, dict) and paper.get("code"):
                known[str(paper_id)] = str(paper["code"])
    return known


def _known_paper_ids(data: Dict[str, Any]) -> set[str]:
    """收集所有历史论文 ID，用于跳过重复的 GitHub 搜索。"""
    return {
        str(paper_id)
        for papers in data.values()
        if isinstance(papers, dict)
        for paper_id in papers
    }


def _last_successful_dates(state: Dict[str, Any]) -> Dict[str, str]:
    """读取每个领域最近一次成功更新日期。"""
    values = state.get("last_successful_update", {})
    if not isinstance(values, dict):
        return {}
    return {str(topic): str(value) for topic, value in values.items() if value}


def _effective_start_date(
    configured_start: Optional[str],
    last_successful: Optional[str],
    lookback_days: int,
    *,
    full_refresh: bool,
    today: date,
) -> tuple[Optional[str], bool]:
    """返回实际查询起始日期以及是否使用了增量水位。"""
    if full_refresh or not last_successful:
        return configured_start, False

    try:
        last_date = date.fromisoformat(last_successful)
    except ValueError:
        logger.warning("更新状态日期无效，将执行全量刷新: %s", last_successful)
        return configured_start, False

    if last_date > today:
        logger.warning("更新状态日期晚于今天，将执行全量刷新: %s", last_successful)
        return configured_start, False

    incremental_start = last_date - timedelta(days=lookback_days)
    if configured_start:
        full_start = date.fromisoformat(configured_start)
        incremental_start = max(incremental_start, full_start)
    return incremental_start.isoformat(), True


def run(
    config: Dict[str, Any],
    *,
    full_refresh: bool = False,
    today: Optional[date] = None,
) -> None:
    """读取缓存，执行增量或全量抓取，然后合并并渲染。"""
    run_date = today or date.today()
    data = load_data(config["data_path"])
    state_path = config.get("state_path", "./docs/irstd-paper-daily-state.json")
    state = load_data(state_path)
    successful_dates = _last_successful_dates(state)
    known_codes: Dict[str, str] = _known_code_links(data)
    known_paper_ids = _known_paper_ids(data)
    lookup_missing_code = bool(config.get("enable_code_lookup", True))

    new_papers_by_topic: Dict[str, Any] = {}
    for topic, base_query in config["kv"].items():
        max_results = config["domain_max_results"].get(
            topic,
            config.get("max_results", 10),
        )
        configured_start = config.get("domain_start_dates", {}).get(topic)
        lookback_days = config.get("domain_lookback_days", {}).get(
            topic,
            int(config.get("incremental_lookback_days", 3)),
        )
        query_start, is_incremental = _effective_start_date(
            configured_start,
            successful_dates.get(topic),
            lookback_days,
            full_refresh=full_refresh,
            today=run_date,
        )
        query = add_date_range(
            base_query,
            start_date=query_start,
            end_date=run_date.isoformat(),
        )
        limit_description = (
            f"{query_start or '不限起始日期'} 至 {run_date.isoformat()} 的全部论文"
            if max_results is None
            else f"最多 {max_results} 篇"
        )
        mode = "增量" if is_incremental else "全量"
        logger.info("开始%s抓取领域 %s（%s）", mode, topic, limit_description)
        new_papers_by_topic[topic] = fetch_daily_papers(
            topic,
            query,
            max_results,
            known_codes=known_codes,
            known_paper_ids=known_paper_ids,
            lookup_missing_code=lookup_missing_code,
        )

    for topic, papers in new_papers_by_topic.items():
        data = merge_papers(data, papers, topic)
    save_data(config["data_path"], data)
    logger.info("数据已写入 %s", config["data_path"])

    _render_outputs(config, data)
    successful_dates.update(
        {topic: run_date.isoformat() for topic in new_papers_by_topic}
    )
    state["last_successful_update"] = successful_dates
    save_data(state_path, state)
    logger.info("增量更新状态已写入 %s", state_path)
    logger.info("全部任务完成")


def run_backfill(config: Dict[str, Any]) -> None:
    """为历史论文补齐代码链接并重新生成所有开启的输出。"""
    data = load_data(config["data_path"])
    data, updated = backfill_code_links(data)
    save_data(config["data_path"], data)
    _render_outputs(config, data)
    logger.info("代码链接回填完成，共补齐 %d 篇", updated)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="每日抓取 arXiv 论文并生成日报（README / GitPage / 微信版）"
    )
    parser.add_argument(
        "--config_path",
        type=str,
        default="config.yaml",
        help="配置文件路径（默认: config.yaml）",
    )
    parser.add_argument(
        "--backfill_code",
        action="store_true",
        help="遍历已有数据，为缺失代码链接的论文补齐链接（不抓取新论文）",
    )
    parser.add_argument(
        "--full-refresh",
        action="store_true",
        help="忽略增量水位，重新抓取配置起始日期以来的全部论文",
    )
    args = parser.parse_args()

    config = load_config(args.config_path)
    logger.info("配置加载完成，启用领域: %s", list(config["kv"].keys()))
    if args.full_refresh:
        run(config, full_refresh=True)
        if args.backfill_code:
            run_backfill(config)
    elif args.backfill_code:
        run_backfill(config)
    else:
        run(config)


if __name__ == "__main__":
    main()
