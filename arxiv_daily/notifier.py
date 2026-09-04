"""把每日论文变化通过 WxPusher 推送到指定微信用户。"""

from __future__ import annotations

import logging
import os
import re
from datetime import date
from typing import Any, Dict, List, Mapping, Sequence, Tuple

import requests

logger = logging.getLogger(__name__)

WXPUSHER_API_URL = "https://wxpusher.zjiecode.com/api/send/message"
REQUEST_TIMEOUT = 15
WXPUSHER_SUCCESS_CODE = 1000
MAX_CONTENT_LENGTH = 40_000
MAX_SUMMARY_LENGTH = 100


class NotificationError(RuntimeError):
    """微信通知配置或发送失败。"""


def parse_wxpusher_uids(value: str) -> List[str]:
    """解析逗号、分号或空白分隔的 WxPusher UID，并保持顺序去重。"""
    uids = list(dict.fromkeys(part for part in re.split(r"[,;\s]+", value) if part))
    invalid = [uid for uid in uids if not re.fullmatch(r"UID_\S+", uid)]
    if invalid:
        raise NotificationError("WXPUSHER_UIDS 包含无效 UID，UID 必须以 UID_ 开头")
    if not uids:
        raise NotificationError("WXPUSHER_UIDS 未配置任何接收者")
    if len(uids) > 2000:
        raise NotificationError("WXPUSHER_UIDS 不能超过 2000 个接收者")
    return uids


def _change_count(groups: Mapping[str, Sequence[Mapping[str, Any]]]) -> int:
    return sum(len(papers) for papers in groups.values())


def _escape_markdown(value: Any) -> str:
    return str(value or "").replace("[", "\\[").replace("]", "\\]")


def _append_papers(
    lines: List[str],
    groups: Mapping[str, Sequence[Mapping[str, Any]]],
    *,
    heading: str,
    remaining: int,
    start_index: int,
) -> Tuple[int, int]:
    if remaining <= 0 or not _change_count(groups):
        return remaining, start_index

    lines.extend([f"## {heading}", ""])
    index = start_index
    for topic, papers in groups.items():
        if len(groups) > 1 and papers:
            lines.extend([f"### {_escape_markdown(topic)}", ""])
        for paper in papers:
            if remaining <= 0:
                return remaining, index
            index += 1
            remaining -= 1
            title = _escape_markdown(paper.get("title", "未命名论文"))
            paper_url = str(paper.get("url") or "")
            title_text = f"[{title}]({paper_url})" if paper_url else title
            lines.append(f"{index}. {title_text}")

            author = _escape_markdown(paper.get("first_author", ""))
            if author:
                lines.append(f"   - 作者：{author} et al.")
            code_url = str(paper.get("code") or "")
            if code_url:
                lines.append(f"   - 代码：[GitHub]({code_url})")
            lines.append("")
    return remaining, index


def build_daily_digest(
    new_papers: Mapping[str, Sequence[Mapping[str, Any]]],
    updated_papers: Mapping[str, Sequence[Mapping[str, Any]]],
    *,
    run_date: date,
    repo_url: str,
    max_papers: int = 20,
    initial_sync: bool = False,
) -> Tuple[str, str]:
    """生成适合 WxPusher 的 Markdown 摘要和通知标题。"""
    if max_papers < 1:
        raise NotificationError("wechat_notification.max_papers 必须大于 0")

    new_count = _change_count(new_papers)
    updated_count = _change_count(updated_papers)
    total = new_count + updated_count
    if initial_sync:
        summary = f"IRSTD Paper Daily｜首次同步 {total} 篇"
        status = f"首次同步，共 **{total}** 篇 IRSTD 论文。"
    elif total:
        summary = (
            f"IRSTD Paper Daily｜新增 {new_count} 篇，更新 {updated_count} 篇"
        )
        status = f"新增 **{new_count}** 篇，更新 **{updated_count}** 篇。"
    else:
        summary = "IRSTD Paper Daily｜今日无新增"
        status = "今日未发现新增或发生变化的 IRSTD 论文。"

    lines = [
        "# IRSTD Paper Daily",
        "",
        f"> {run_date.isoformat()} 更新完成：{status}",
        "",
    ]
    remaining = max_papers
    if initial_sync:
        remaining, _ = _append_papers(
            lines,
            new_papers,
            heading="完整论文目录",
            remaining=remaining,
            start_index=0,
        )
    else:
        remaining, index = _append_papers(
            lines,
            new_papers,
            heading="新增论文",
            remaining=remaining,
            start_index=0,
        )
        remaining, _ = _append_papers(
            lines,
            updated_papers,
            heading="更新论文",
            remaining=remaining,
            start_index=index,
        )
    if total > max_papers:
        lines.extend(
            [
                f"> 本次共有 {total} 篇变化，仅展示前 {max_papers} 篇。",
                "",
            ]
        )
    if repo_url:
        lines.append(f"[查看完整论文列表]({repo_url})")

    content = "\n".join(lines).strip()
    if len(content) > MAX_CONTENT_LENGTH:
        content = content[: MAX_CONTENT_LENGTH - 20].rstrip() + "\n\n内容已截断。"
    return summary[:MAX_SUMMARY_LENGTH], content


def send_wxpusher_message(
    *,
    app_token: str,
    uids: Sequence[str],
    summary: str,
    content: str,
    url: str = "",
) -> Dict[str, Any]:
    """调用 WxPusher 标准推送接口，业务 code=1000 才视为成功。"""
    if not re.fullmatch(r"AT_\S+", app_token):
        raise NotificationError("WXPUSHER_APP_TOKEN 无效，Token 必须以 AT_ 开头")
    parsed_uids = parse_wxpusher_uids(",".join(uids))
    payload: Dict[str, Any] = {
        "appToken": app_token,
        "content": content,
        "summary": summary[:MAX_SUMMARY_LENGTH],
        "contentType": 3,
        "uids": parsed_uids,
    }
    if url:
        payload["url"] = url

    try:
        response = requests.post(
            WXPUSHER_API_URL,
            json=payload,
            timeout=REQUEST_TIMEOUT,
        )
        response.raise_for_status()
        result = response.json()
    except (requests.RequestException, ValueError) as exc:
        raise NotificationError(f"WxPusher 请求失败: {exc}") from exc

    if not isinstance(result, dict) or result.get("code") != WXPUSHER_SUCCESS_CODE:
        message = result.get("msg", "未知错误") if isinstance(result, dict) else "响应格式错误"
        raise NotificationError(f"WxPusher 发送失败: {message}")
    return result


def notify_daily_update(
    config: Mapping[str, Any],
    new_papers: Mapping[str, Sequence[Mapping[str, Any]]],
    updated_papers: Mapping[str, Sequence[Mapping[str, Any]]],
    *,
    run_date: date,
    initial_sync: bool = False,
) -> bool:
    """读取 GitHub Secrets 对应环境变量并推送每日变化。"""
    app_token = os.environ.get("WXPUSHER_APP_TOKEN", "").strip()
    raw_uids = os.environ.get("WXPUSHER_UIDS", "").strip()
    if not app_token and not raw_uids:
        logger.warning("未配置 WxPusher Secrets，跳过微信通知")
        return False
    if not app_token or not raw_uids:
        raise NotificationError(
            "必须同时配置 WXPUSHER_APP_TOKEN 和 WXPUSHER_UIDS"
        )

    settings = config.get("wechat_notification", {})
    if not isinstance(settings, dict):
        raise NotificationError("wechat_notification 必须是 YAML 对象")
    provider = str(settings.get("provider", "wxpusher")).lower()
    if provider != "wxpusher":
        raise NotificationError(f"暂不支持微信通知提供方: {provider}")

    total = _change_count(new_papers) + _change_count(updated_papers)
    if not total:
        logger.info("论文目录没有变化，跳过微信通知")
        return False

    user_name = str(config.get("user_name", "")).strip()
    repo_name = str(config.get("repo_name", "")).strip()
    default_repo_url = (
        f"https://github.com/{user_name}/{repo_name}"
        if user_name and repo_name
        else ""
    )
    repo_url = str(settings.get("url") or default_repo_url)
    max_papers = int(settings.get("max_papers", 20))
    if initial_sync:
        max_papers = max(max_papers, total)
    summary, content = build_daily_digest(
        new_papers,
        updated_papers,
        run_date=run_date,
        repo_url=repo_url,
        max_papers=max_papers,
        initial_sync=initial_sync,
    )
    uids = parse_wxpusher_uids(raw_uids)
    send_wxpusher_message(
        app_token=app_token,
        uids=uids,
        summary=summary,
        content=content,
        url=repo_url,
    )
    logger.info("WxPusher 微信通知已发送给 %d 个接收者", len(uids))
    return True
