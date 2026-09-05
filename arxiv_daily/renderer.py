"""结构化 JSON 数据到 README / GitHub Pages Markdown 的渲染。"""

from __future__ import annotations

import datetime
import re
from typing import Any, Dict, List, Tuple


def _pretty_math(text: str) -> str:
    """给行内公式两侧补空格，避免 Markdown 文字粘连。"""
    match = re.search(r"\$.*?\$", text)
    if match is None:
        return text

    start, end = match.span()
    before, after = text[:start], text[end:]
    space_before = "" if not before or before[-1] in (" ", "*") else " "
    space_after = "" if not after or after[0] in (" ", "*") else " "
    return (
        f"{before}{space_before}${match.group()[1:-1].strip()}$"
        f"{space_after}{after}"
    )


def _sort_papers(papers: Dict[str, Any]) -> List[Tuple[str, Any]]:
    """按发布日期倒序排列，同日按论文 ID 倒序。"""
    return sorted(
        papers.items(),
        key=lambda item: (
            str(item[1].get("publish_date", "")) if isinstance(item[1], dict) else "",
            str(item[0]),
        ),
        reverse=True,
    )


def _escape_cell(value: Any) -> str:
    return str(value or "").replace("|", "\\|").replace("\n", " ")


def _paper_row(
    paper_id: str,
    paper: Dict[str, Any],
    *,
    show_authors: bool = True,
    show_links: bool = True,
) -> str:
    code = "null"
    code_url = paper.get("code")
    if show_links and code_url:
        code = f"[link]({code_url})"

    title = _escape_cell(paper.get("title", ""))
    publish_date = _escape_cell(paper.get("publish_date", ""))
    if show_links:
        paper_link = f"[{_escape_cell(paper_id)}]({paper.get('url', '')})"
    else:
        paper_link = _escape_cell(paper_id)

    author = _escape_cell(paper.get("first_author", ""))
    author_cell = f"{author} et.al." if show_authors and author else ""
    return f"|**{publish_date}**|**{title}**|{author_cell}|{paper_link}|{code}|\n"


def _render_toc(data: Dict[str, Any]) -> List[str]:
    lines = [
        "<details>",
        "  <summary>Table of Contents</summary>",
        "  <ol>",
    ]
    for topic, papers in data.items():
        if not papers:
            continue
        anchor = str(topic).replace(" ", "-").lower()
        lines.append(f"    <li><a href=#{anchor}>{topic}</a></li>")
    lines.extend(["  </ol>", "</details>"])
    return lines


def _render_badges(user_name: str, repo_name: str) -> List[str]:
    shield_template = (
        "https://img.shields.io/github/{metric}/{user}/{repo}.svg?style=for-the-badge"
    )
    lines: List[str] = []
    for metric, label in (
        ("contributors", "contributors"),
        ("forks", "forks"),
        ("stars", "stars"),
        ("issues", "issues"),
    ):
        lines.append(
            f"[{label}-shield]: "
            f"{shield_template.format(metric=metric, user=user_name, repo=repo_name)}"
        )
        lines.append(
            f"[{label}-url]: https://github.com/{user_name}/{repo_name}/{label}"
        )
    return lines


def _render_tables(
    data: Dict[str, Any],
    *,
    to_web: bool,
    with_back_to_top: bool = False,
    today: str = "",
    show_authors: bool = True,
    show_links: bool = True,
) -> List[str]:
    lines: List[str] = []
    for topic, papers in data.items():
        if not isinstance(papers, dict) or not papers:
            continue
        lines.extend([f"## {topic}", ""])
        if to_web:
            lines.extend(
                [
                    "| Publish Date | Title | Authors | PDF | Code |",
                    "|:---------|:-----------------------|:---------|:------|:------|",
                ]
            )
        else:
            lines.extend(
                [
                    "|Publish Date|Title|Authors|PDF|Code|",
                    "|---|---|---|---|---|",
                ]
            )

        for paper_id, paper in _sort_papers(papers):
            if isinstance(paper, dict):
                row = _paper_row(
                    str(paper_id),
                    paper,
                    show_authors=show_authors,
                    show_links=show_links,
                )
                lines.append(_pretty_math(row).rstrip())
        lines.append("")

        if with_back_to_top:
            anchor = "#updated-on-" + today.replace(".", "")
            lines.append(f"<p align=right>(<a href={anchor.lower()}>back to top</a>)</p>")
            lines.append("")
    return lines


def render_markdown(
    data: Dict[str, Any],
    *,
    format: str = "readme",
    show_badge: bool = True,
    user_name: str = "",
    repo_name: str = "",
    show_authors: bool = True,
    show_links: bool = True,
) -> str:
    """渲染 README 或 GitHub Pages Markdown。"""
    today = str(datetime.date.today()).replace("-", ".")
    lines: List[str] = []

    if format == "web":
        lines.extend(["---", "layout: default", "---", ""])

    lines.append(f"## Updated on {today}")
    usage_path = "./README.md#usage" if format == "web" else "./docs/README.md#usage"
    lines.append(f"> Usage instructions: [here]({usage_path})")
    lines.append("")

    if format == "readme":
        lines.extend([
            "本项目可开启微信和邮箱推送，"
            "具体配置请参阅[使用说明](./docs/README.md#usage)。",
            "",
        ])
        lines.extend(_render_toc(data))
        lines.append("")
        lines.extend(
            _render_tables(
                data,
                to_web=False,
                with_back_to_top=True,
                today=today,
                show_authors=show_authors,
                show_links=show_links,
            )
        )
        if show_badge:
            lines.extend(_render_badges(user_name, repo_name))
            lines.append("")
    elif format == "web":
        lines.extend(
            _render_tables(
                data,
                to_web=True,
                show_authors=show_authors,
                show_links=show_links,
            )
        )
    else:
        raise ValueError(f"未知的输出格式: {format}")

    return "\n".join(lines).rstrip() + "\n"
