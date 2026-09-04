"""把论文目录变化通过 Server酱推送到绑定的微信。"""

from __future__ import annotations

import logging
import os
import re
from datetime import date
from typing import Any, Dict, List, Mapping, Sequence, Tuple

import requests

logger = logging.getLogger(__name__)

SERVERCHAN_TURBO_API_URL = "https://sctapi.ftqq.com/{sendkey}.send"
SERVERCHAN_3_API_URL = "https://{sendkey}.push.ft07.com/send"
REQUEST_TIMEOUT = 15
SERVERCHAN_SUCCESS_CODE = 0
MAX_CONTENT_LENGTH = 40_000
MAX_SUMMARY_LENGTH = 100


class NotificationError(RuntimeError):
    """微信通知配置或发送失败。"""


def build_serverchan_url(sendkey: str) -> str:
    """校验 SendKey，并生成 Turbo 或 Server酱³ 的发送地址。"""
    if re.fullmatch(r"sctp[A-Za-z0-9-]+", sendkey):
        return SERVERCHAN_3_API_URL.format(sendkey=sendkey)
    if re.fullmatch(r"SCT[A-Za-z0-9_-]+", sendkey):
        return SERVERCHAN_TURBO_API_URL.format(sendkey=sendkey)
    raise NotificationError(
        "SERVERCHAN_SENDKEY 无效，应以 SCT（Turbo）或 sctp（Server酱³）开头"
    )


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
    """生成适合 Server酱的 Markdown 摘要和通知标题。"""
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


def send_serverchan_message(
    *,
    sendkey: str,
    summary: str,
    content: str,
) -> Dict[str, Any]:
    """调用 Server酱接口，业务 code=0 才视为成功。"""
    api_url = build_serverchan_url(sendkey)
    payload: Dict[str, Any] = {
        "title": summary[:MAX_SUMMARY_LENGTH],
        "desp": content,
        "short": summary[:MAX_SUMMARY_LENGTH],
    }

    try:
        response = requests.post(
            api_url,
            json=payload,
            timeout=REQUEST_TIMEOUT,
        )
        response.raise_for_status()
        result = response.json()
    except requests.RequestException:
        # Server酱 URL 中含 SendKey，异常链可能打印完整 URL，因此不保留原异常。
        raise NotificationError("Server酱请求失败（网络或 HTTP 错误）") from None
    except ValueError:
        raise NotificationError("Server酱返回了无法解析的响应") from None

    if not isinstance(result, dict) or result.get("code") != SERVERCHAN_SUCCESS_CODE:
        message = (
            result.get("message", "未知错误")
            if isinstance(result, dict)
            else "响应格式错误"
        )
        raise NotificationError(f"Server酱发送失败: {message}")
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
    sendkey = os.environ.get("SERVERCHAN_SENDKEY", "").strip()
    if not sendkey:
        logger.warning("未配置 SERVERCHAN_SENDKEY，跳过微信通知")
        return False

    settings = config.get("wechat_notification", {})
    if not isinstance(settings, dict):
        raise NotificationError("wechat_notification 必须是 YAML 对象")
    provider = str(settings.get("provider", "serverchan")).lower()
    if provider != "serverchan":
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
    send_serverchan_message(
        sendkey=sendkey,
        summary=summary,
        content=content,
    )
    logger.info("Server酱微信通知已提交发送")
    return True
