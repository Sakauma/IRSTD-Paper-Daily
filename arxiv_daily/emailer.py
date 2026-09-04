"""通过 SMTP 把微信版 IRSTD 日报发送到指定邮箱。"""

from __future__ import annotations

import argparse
import html
import logging
import os
import re
import smtplib
import ssl
from dataclasses import dataclass
from email.message import EmailMessage
from email.utils import getaddresses, parseaddr
from pathlib import Path
from typing import Mapping, Sequence
from urllib.parse import urlsplit

logger = logging.getLogger(__name__)

EMAIL_SUBJECT = "IRSTD-Paper-Daily"
SMTP_TIMEOUT = 20
MAX_EMAIL_HTML_BYTES = 96 * 1024
SUPPORTED_SECURITY_MODES = {"ssl", "starttls"}
UPDATED_PATTERN = re.compile(r"^> Updated on (?P<date>.+)$")
PAPER_PATTERN = re.compile(
    r"^- (?P<date>\d{4}-\d{2}-\d{2}), \*\*(?P<title>.+?)\*\*, "
    r"(?P<author>.*?), Paper: \[[^]]*\]\((?P<paper_url>[^)]+)\)"
    r"(?:, Code: \*\*\[[^]]*\]\((?P<code_url>[^)]+)\)\*\*)?$"
)


class EmailNotificationError(RuntimeError):
    """邮件通知配置或发送失败。"""


@dataclass(frozen=True)
class EmailSettings:
    """完成一次 SMTP 邮件发送所需的配置。"""

    host: str
    port: int
    security: str
    username: str
    password: str
    sender: str
    recipients: tuple[str, ...]


@dataclass(frozen=True)
class EmailPaper:
    """从 ``wechat.md`` 解析出的单篇论文。"""

    topic: str
    publish_date: str
    title: str
    author: str
    paper_url: str
    code_url: str


@dataclass(frozen=True)
class EmailDigest:
    """用于纯文本和 HTML 邮件渲染的日报内容。"""

    updated_on: str
    papers: tuple[EmailPaper, ...]


def _email_address(value: str, *, label: str) -> str:
    if "\r" in value or "\n" in value:
        raise EmailNotificationError(f"{label} 包含非法换行符")
    _, address = parseaddr(value)
    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", address):
        raise EmailNotificationError(f"{label} 不是有效邮箱地址")
    return address


def parse_recipients(value: str) -> tuple[str, ...]:
    """解析逗号或分号分隔的收件邮箱，并保持顺序去重。"""
    if "\r" in value or "\n" in value:
        raise EmailNotificationError("EMAIL_TO 包含非法换行符")
    parsed = getaddresses([value.replace(";", ",")])
    recipients = tuple(
        dict.fromkeys(
            _email_address(address, label="EMAIL_TO")
            for _, address in parsed
            if address.strip()
        )
    )
    if not recipients:
        raise EmailNotificationError("EMAIL_TO 未配置任何有效收件邮箱")
    return recipients


def load_email_settings(
    environ: Mapping[str, str] | None = None,
) -> EmailSettings | None:
    """读取 SMTP 环境变量；完全未配置时返回 ``None``。"""
    values = environ if environ is not None else os.environ
    required_names = (
        "SMTP_HOST",
        "SMTP_USERNAME",
        "EMAIL_TO",
    )
    required = {name: str(values.get(name, "")).strip() for name in required_names}
    required["SMTP_PASSWORD"] = str(values.get("SMTP_PASSWORD", ""))
    if not any(required.values()):
        return None

    missing = [name for name, value in required.items() if not value]
    if missing:
        raise EmailNotificationError(
            "邮件通知配置不完整，缺少: " + ", ".join(missing)
        )

    host = required["SMTP_HOST"]
    if not re.fullmatch(r"[A-Za-z0-9.-]+", host):
        raise EmailNotificationError("SMTP_HOST 格式无效")

    security = str(values.get("SMTP_SECURITY", "ssl")).strip().lower() or "ssl"
    if security not in SUPPORTED_SECURITY_MODES:
        raise EmailNotificationError("SMTP_SECURITY 只能是 ssl 或 starttls")

    raw_port = str(values.get("SMTP_PORT", "")).strip()
    if not raw_port:
        raw_port = "465" if security == "ssl" else "587"
    try:
        port = int(raw_port)
    except ValueError:
        raise EmailNotificationError("SMTP_PORT 必须是整数") from None
    if not 1 <= port <= 65_535:
        raise EmailNotificationError("SMTP_PORT 必须在 1 到 65535 之间")

    username = required["SMTP_USERNAME"]
    sender = _email_address(
        str(values.get("EMAIL_FROM", "")).strip() or username,
        label="EMAIL_FROM",
    )
    recipients = parse_recipients(required["EMAIL_TO"])
    return EmailSettings(
        host=host,
        port=port,
        security=security,
        username=username,
        password=required["SMTP_PASSWORD"],
        sender=sender,
        recipients=recipients,
    )


def parse_wechat_markdown(content: str) -> EmailDigest:
    """从生成的 ``wechat.md`` 中提取更新时间、领域和论文条目。"""
    updated_on = ""
    current_topic = "IRSTD"
    papers: list[EmailPaper] = []
    for raw_line in content.splitlines():
        line = raw_line.strip()
        updated_match = UPDATED_PATTERN.fullmatch(line)
        if updated_match:
            updated_on = updated_match.group("date").replace(".", "-")
            continue
        if line.startswith("## "):
            current_topic = line[3:].strip() or "IRSTD"
            continue
        paper_match = PAPER_PATTERN.fullmatch(line)
        if not paper_match:
            continue
        papers.append(
            EmailPaper(
                topic=current_topic,
                publish_date=paper_match.group("date"),
                title=paper_match.group("title"),
                author=paper_match.group("author").strip().rstrip(","),
                paper_url=paper_match.group("paper_url"),
                code_url=paper_match.group("code_url") or "",
            )
        )

    if not papers:
        raise EmailNotificationError("未能从 wechat.md 解析出论文条目")
    return EmailDigest(updated_on=updated_on, papers=tuple(papers))


def _topics(digest: EmailDigest) -> dict[str, list[EmailPaper]]:
    grouped: dict[str, list[EmailPaper]] = {}
    for paper in digest.papers:
        grouped.setdefault(paper.topic, []).append(paper)
    return grouped


def render_plain_email(
    digest: EmailDigest,
    *,
    total_count: int | None = None,
    omitted_count: int = 0,
    repository_url: str = "",
) -> str:
    """生成不含 Markdown 标记的纯文本备用正文。"""
    effective_total = total_count if total_count is not None else len(digest.papers)
    lines = [EMAIL_SUBJECT]
    if digest.updated_on:
        lines.append(f"更新日期：{digest.updated_on}")
    lines.extend([f"论文数量：{effective_total}", ""])
    index = 0
    for topic, papers in _topics(digest).items():
        lines.extend([topic, "=" * len(topic)])
        for paper in papers:
            index += 1
            lines.extend(
                [
                    f"{index}. {paper.title}",
                    f"   日期：{paper.publish_date}",
                ]
            )
            if paper.author:
                lines.append(f"   作者：{paper.author}")
            lines.append(f"   论文：{paper.paper_url}")
            if paper.code_url:
                lines.append(f"   代码：{paper.code_url}")
            lines.append("")
    if omitted_count:
        lines.extend(
            [
                f"为避免邮件过长，仅显示最新 {len(digest.papers)} 篇，"
                f"较早的 {omitted_count} 篇未展示。",
                "",
            ]
        )
    if _safe_http_url(repository_url):
        lines.append(f"完整目录：{repository_url.strip()}")
    return "\n".join(lines).rstrip() + "\n"


def _safe_http_url(value: str) -> str:
    parsed = urlsplit(value.strip())
    if parsed.scheme in {"http", "https"} and parsed.netloc:
        return html.escape(value.strip(), quote=True)
    return ""


def _render_html_document(
    digest: EmailDigest,
    *,
    repository_url: str = "",
    total_count: int | None = None,
    omitted_count: int = 0,
) -> str:
    """渲染已经确定论文范围的 HTML 日报。"""
    effective_total = total_count if total_count is not None else len(digest.papers)
    updated_on = html.escape(digest.updated_on or "本次更新")
    sections: list[str] = []
    global_index = 0
    for topic, papers in _topics(digest).items():
        cards: list[str] = []
        for paper in papers:
            global_index += 1
            title = html.escape(paper.title)
            author = html.escape(paper.author)
            publish_date = html.escape(paper.publish_date)
            paper_url = _safe_http_url(paper.paper_url)
            code_url = _safe_http_url(paper.code_url)
            title_html = (
                f'<a class="paper-title" href="{paper_url}">{title}</a>'
                if paper_url
                else f'<span class="paper-title">{title}</span>'
            )
            author_html = (
                f'<div class="paper-author">作者：{author}</div>' if author else ""
            )
            actions: list[str] = []
            if paper_url:
                actions.append(
                    f'<a class="button paper-button" href="{paper_url}">查看论文</a>'
                )
            if code_url:
                actions.append(
                    f'<a class="button code-button" href="{code_url}">查看代码</a>'
                )
            cards.append(
                "".join(
                    [
                        '<article class="paper-card">',
                        '<div class="paper-meta">',
                        f'<span class="paper-index">#{global_index}</span>',
                        f'<span class="paper-date">{publish_date}</span>',
                        "</div>",
                        title_html,
                        author_html,
                        f'<div class="paper-actions">{"".join(actions)}</div>',
                        "</article>",
                    ]
                )
            )
        sections.append(
            "".join(
                [
                    '<section class="topic-section">',
                    '<div class="topic-heading">',
                    f"<h2>{html.escape(topic)}</h2>",
                    f'<span class="topic-count">{len(papers)} 篇</span>',
                    "</div>",
                    "".join(cards),
                    "</section>",
                ]
            )
        )

    safe_repository_url = _safe_http_url(repository_url)
    repository_link = (
        f'<a href="{safe_repository_url}">在 GitHub 查看完整目录</a>'
        if safe_repository_url
        else "IRSTD Paper Daily 自动生成"
    )
    omitted_notice = (
        '<div class="omitted-notice">'
        f"为避免邮件超过 102 KB，仅显示最新 <strong>{len(digest.papers)}</strong> "
        f"篇，较早的 <strong>{omitted_count}</strong> 篇未展示。"
        "</div>"
        if omitted_count
        else ""
    )
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <style>
    body {{ margin: 0; padding: 0; background: #f3f5f8; color: #172033;
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Arial, sans-serif; }}
    .page {{ max-width: 760px; margin: 0 auto; padding: 24px 12px 40px; }}
    .hero {{ padding: 30px 28px; border-radius: 18px; color: #fff;
      background: #172554 linear-gradient(135deg, #172554, #9a3412); }}
    .eyebrow {{ margin: 0 0 8px; color: #fed7aa; font-size: 12px;
      font-weight: 700; letter-spacing: 1.5px; text-transform: uppercase; }}
    .hero h1 {{ margin: 0; font-size: 30px; line-height: 1.25; }}
    .hero p {{ margin: 10px 0 0; color: #e2e8f0; font-size: 14px; }}
    .summary {{ display: inline-block; margin-top: 18px; padding: 8px 12px;
      border: 1px solid rgba(255,255,255,.25); border-radius: 999px;
      background: rgba(255,255,255,.12); color: #fff; font-size: 13px; }}
    .topic-section {{ margin-top: 26px; }}
    .topic-heading {{ display: flex; align-items: center; justify-content: space-between;
      margin: 0 4px 12px; }}
    .topic-heading h2 {{ margin: 0; color: #172554; font-size: 20px; }}
    .topic-count {{ color: #64748b; font-size: 13px; }}
    .paper-card {{ margin-bottom: 12px; padding: 18px 20px; border: 1px solid #e2e8f0;
      border-left: 4px solid #ea580c; border-radius: 12px; background: #fff;
      box-shadow: 0 3px 12px rgba(15,23,42,.05); }}
    .paper-meta {{ margin-bottom: 8px; color: #64748b; font-size: 12px; }}
    .paper-index {{ display: inline-block; margin-right: 8px; color: #c2410c;
      font-weight: 700; }}
    .paper-title {{ display: block; color: #172033; font-size: 16px;
      font-weight: 700; line-height: 1.5; text-decoration: none; }}
    .paper-author {{ margin-top: 7px; color: #64748b; font-size: 13px; }}
    .paper-actions {{ margin-top: 13px; }}
    .button {{ display: inline-block; margin: 0 8px 4px 0; padding: 7px 12px;
      border-radius: 7px; color: #fff !important; font-size: 12px;
      font-weight: 700; text-decoration: none; }}
    .paper-button {{ background: #2563eb; }}
    .code-button {{ background: #15803d; }}
    .footer {{ margin-top: 28px; color: #64748b; font-size: 12px;
      text-align: center; }}
    .footer a {{ color: #2563eb; text-decoration: none; }}
    .omitted-notice {{ margin: 26px 0 0; padding: 13px 16px; border: 1px solid #fed7aa;
      border-radius: 9px; background: #fff7ed; color: #9a3412; font-size: 13px;
      line-height: 1.6; text-align: center; }}
    @media (max-width: 520px) {{
      .page {{ padding: 12px 8px 28px; }}
      .hero {{ padding: 24px 20px; border-radius: 12px; }}
      .hero h1 {{ font-size: 25px; }}
      .paper-card {{ padding: 16px; }}
    }}
  </style>
</head>
<body>
  <main class="page">
    <header class="hero">
      <p class="eyebrow">Infrared Small Target Research</p>
      <h1>IRSTD Paper Daily</h1>
      <p>红外小目标检测论文每日更新</p>
      <div class="summary">更新日期：{updated_on} · 共 {effective_total} 篇</div>
    </header>
    {''.join(sections)}
    {omitted_notice}
    <footer class="footer">{repository_link}</footer>
  </main>
</body>
</html>
"""


def _fit_html_digest(
    digest: EmailDigest,
    *,
    repository_url: str,
    max_bytes: int,
) -> tuple[EmailDigest, int, str]:
    """保留尽可能多的最新论文，使 HTML 正文不超过字节上限。"""
    if max_bytes < 1:
        raise EmailNotificationError("HTML 邮件字节上限必须大于 0")

    total_count = len(digest.papers)
    full_html = _render_html_document(
        digest,
        repository_url=repository_url,
        total_count=total_count,
    )
    if len(full_html.encode("utf-8")) <= max_bytes:
        return digest, 0, full_html

    ordered_papers = sorted(
        digest.papers,
        key=lambda paper: (paper.publish_date, paper.paper_url),
        reverse=True,
    )
    low = 0
    high = total_count
    best_digest = EmailDigest(updated_on=digest.updated_on, papers=())
    best_html = _render_html_document(
        best_digest,
        repository_url=repository_url,
        total_count=total_count,
        omitted_count=total_count,
    )
    if len(best_html.encode("utf-8")) > max_bytes:
        raise EmailNotificationError("HTML 邮件模板本身超过字节上限")

    while low <= high:
        visible_count = (low + high) // 2
        candidate = EmailDigest(
            updated_on=digest.updated_on,
            papers=tuple(ordered_papers[:visible_count]),
        )
        candidate_html = _render_html_document(
            candidate,
            repository_url=repository_url,
            total_count=total_count,
            omitted_count=total_count - visible_count,
        )
        if len(candidate_html.encode("utf-8")) <= max_bytes:
            best_digest = candidate
            best_html = candidate_html
            low = visible_count + 1
        else:
            high = visible_count - 1

    return best_digest, total_count - len(best_digest.papers), best_html


def render_html_email(
    digest: EmailDigest,
    *,
    repository_url: str = "",
    max_bytes: int = MAX_EMAIL_HTML_BYTES,
) -> str:
    """生成不超过安全字节上限、优先保留最新论文的 HTML 日报。"""
    _, _, html_content = _fit_html_digest(
        digest,
        repository_url=repository_url,
        max_bytes=max_bytes,
    )
    return html_content


def _repository_url(environ: Mapping[str, str]) -> str:
    explicit_url = str(environ.get("EMAIL_REPOSITORY_URL", "")).strip()
    if _safe_http_url(explicit_url):
        return explicit_url
    repository = str(environ.get("GITHUB_REPOSITORY", "")).strip()
    if re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository):
        return f"https://github.com/{repository}"
    return ""


def build_email_message(
    content: str,
    *,
    sender: str,
    recipients: Sequence[str],
    repository_url: str = "",
    max_html_bytes: int = MAX_EMAIL_HTML_BYTES,
) -> EmailMessage:
    """创建主题固定、同时包含纯文本和 HTML 排版的 UTF-8 邮件。"""
    digest = parse_wechat_markdown(content)
    visible_digest, omitted_count, html_content = _fit_html_digest(
        digest,
        repository_url=repository_url,
        max_bytes=max_html_bytes,
    )
    message = EmailMessage()
    message["Subject"] = EMAIL_SUBJECT
    message["From"] = sender
    message["To"] = ", ".join(recipients)
    message.set_content(
        render_plain_email(
            visible_digest,
            total_count=len(digest.papers),
            omitted_count=omitted_count,
            repository_url=repository_url,
        ),
        charset="utf-8",
    )
    message.add_alternative(
        html_content,
        subtype="html",
        charset="utf-8",
    )
    return message


def send_email_message(
    settings: EmailSettings,
    content: str,
    *,
    repository_url: str = "",
) -> None:
    """使用 SSL 或 STARTTLS 连接 SMTP 服务并发送日报。"""
    message = build_email_message(
        content,
        sender=settings.sender,
        recipients=settings.recipients,
        repository_url=repository_url,
    )
    tls_context = ssl.create_default_context()
    try:
        if settings.security == "ssl":
            with smtplib.SMTP_SSL(
                settings.host,
                settings.port,
                timeout=SMTP_TIMEOUT,
                context=tls_context,
            ) as smtp:
                smtp.login(settings.username, settings.password)
                smtp.send_message(
                    message,
                    from_addr=settings.sender,
                    to_addrs=list(settings.recipients),
                )
        else:
            with smtplib.SMTP(
                settings.host,
                settings.port,
                timeout=SMTP_TIMEOUT,
            ) as smtp:
                smtp.ehlo()
                smtp.starttls(context=tls_context)
                smtp.ehlo()
                smtp.login(settings.username, settings.password)
                smtp.send_message(
                    message,
                    from_addr=settings.sender,
                    to_addrs=list(settings.recipients),
                )
    except (OSError, smtplib.SMTPException):
        raise EmailNotificationError(
            "邮件发送失败（SMTP 连接、认证或发送错误）"
        ) from None


def send_daily_email(
    markdown_path: str | Path,
    *,
    environ: Mapping[str, str] | None = None,
) -> bool:
    """读取日报并发送；完全未配置 SMTP 时安全跳过。"""
    values = environ if environ is not None else os.environ
    settings = load_email_settings(values)
    if settings is None:
        logger.warning("未配置 SMTP 邮件 Secrets，跳过邮件通知")
        return False

    path = Path(markdown_path)
    if not path.is_file():
        raise EmailNotificationError(f"邮件正文文件不存在: {path}")
    content = path.read_text(encoding="utf-8")
    if not content.strip():
        raise EmailNotificationError(f"邮件正文文件为空: {path}")

    send_email_message(
        settings,
        content,
        repository_url=_repository_url(values),
    )
    logger.info("邮件通知已发送给 %d 个收件地址", len(settings.recipients))
    return True


def main() -> None:
    parser = argparse.ArgumentParser(description="发送 IRSTD Paper Daily 邮件")
    parser.add_argument(
        "markdown_path",
        nargs="?",
        default="docs/wechat.md",
        help="邮件正文 Markdown 文件（默认: docs/wechat.md）",
    )
    args = parser.parse_args()
    logging.basicConfig(
        format="[%(asctime)s %(levelname)s] %(message)s",
        datefmt="%m/%d/%Y %H:%M:%S",
        level=logging.INFO,
    )
    send_daily_email(args.markdown_path)


if __name__ == "__main__":
    main()
