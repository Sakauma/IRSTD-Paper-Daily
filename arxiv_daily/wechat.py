"""生成适合复制到微信的日报 Markdown。"""

from __future__ import annotations

import datetime
from typing import Any, Dict, List

from .renderer import _render_badges, _sort_papers


def build_wechat_data(data: Dict[str, Any]) -> Dict[str, Dict[str, str]]:
    """把结构化论文数据转换为原 cv-arxiv-daily 风格的条目文本。"""
    result: Dict[str, Dict[str, str]] = {}
    for topic, papers in data.items():
        if not isinstance(papers, dict):
            continue
        topic_data: Dict[str, str] = {}
        for paper_id, paper in _sort_papers(papers):
            if not isinstance(paper, dict):
                continue
            publish_date = str(paper.get("publish_date", ""))
            title = str(paper.get("title", "")).replace("\n", " ")
            first_author = str(paper.get("first_author", ""))
            author_text = f"{first_author} et.al." if first_author else ""
            paper_url = str(paper.get("url", ""))
            line = (
                f"- {publish_date}, **{title}**, {author_text}, "
                f"Paper: [{paper_url}]({paper_url})"
            )
            code_url = paper.get("code")
            if code_url:
                line += f", Code: **[{code_url}]({code_url})**"
            topic_data[str(paper_id)] = line
        result[str(topic)] = topic_data
    return result


def _as_wechat_lines(data: Dict[str, Any]) -> Dict[str, Dict[str, str]]:
    """兼容结构化论文数据和已持久化的条目文本。"""
    if all(
        isinstance(papers, dict)
        and all(isinstance(paper, str) for paper in papers.values())
        for papers in data.values()
    ):
        return data  # type: ignore[return-value]
    return build_wechat_data(data)


def render_wechat_markdown(
    data: Dict[str, Any],
    *,
    show_badge: bool = True,
    user_name: str = "",
    repo_name: str = "",
) -> str:
    """生成原微信版格式：徽章、更新时间、目录和项目符号条目。"""
    wechat_data = _as_wechat_lines(data)
    today = str(datetime.date.today()).replace("-", ".")
    lines: List[str] = []

    if show_badge:
        lines.extend(
            [
                "[![Contributors][contributors-shield]][contributors-url]",
                "[![Forks][forks-shield]][forks-url]",
                "[![Stargazers][stars-shield]][stars-url]",
                "[![Issues][issues-shield]][issues-url]",
                "",
            ]
        )

    lines.extend(
        [
            f"> Updated on {today}",
            "> Usage instructions: [here](./README.md#usage)",
            "",
            "<details>",
            "  <summary>Table of Contents</summary>",
            "  <ol>",
        ]
    )
    for topic, papers in wechat_data.items():
        if papers:
            anchor = str(topic).replace(" ", "-")
            lines.append(f"    <li><a href=#{anchor}>{topic}</a></li>")
    lines.extend(["  </ol>", "</details>", ""])

    for topic, papers in wechat_data.items():
        if not papers:
            continue
        lines.extend([f"## {topic}", ""])
        lines.extend(papers.values())
        lines.append("")

    if show_badge:
        lines.extend(_render_badges(user_name, repo_name))
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"
