"""每日抓取 arXiv 论文并生成 README、GitHub Pages 和微信版日报。

用法：
    python daily_arxiv.py [--config_path config.yaml]
    python daily_arxiv.py --backfill_code
"""

from __future__ import annotations

import argparse
import logging
from pathlib import Path
from typing import Any, Dict

from arxiv_daily.codelink import backfill_code_links
from arxiv_daily.config import load_config
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


def run(config: Dict[str, Any]) -> None:
    """主流程：读取历史 -> 抓取 -> 合并 -> 渲染。"""
    data = load_data(config["data_path"])
    known_codes: Dict[str, str] = _known_code_links(data)
    lookup_missing_code = bool(config.get("enable_code_lookup", True))

    new_papers_by_topic: Dict[str, Any] = {}
    for topic, query in config["kv"].items():
        max_results = config["domain_max_results"].get(
            topic,
            config.get("max_results", 10),
        )
        logger.info("开始抓取领域 %s（最多 %d 篇）", topic, max_results)
        new_papers_by_topic[topic] = fetch_daily_papers(
            topic,
            query,
            max_results,
            known_codes=known_codes,
            lookup_missing_code=lookup_missing_code,
        )

    for topic, papers in new_papers_by_topic.items():
        data = merge_papers(data, papers, topic)
    save_data(config["data_path"], data)
    logger.info("数据已写入 %s", config["data_path"])

    _render_outputs(config, data)
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
    args = parser.parse_args()

    config = load_config(args.config_path)
    logger.info("配置加载完成，启用领域: %s", list(config["kv"].keys()))
    if args.backfill_code:
        run_backfill(config)
    else:
        run(config)


if __name__ == "__main__":
    main()
