"""通过 SMTP 把微信版 IRSTD 日报发送到指定邮箱。"""

from __future__ import annotations

import argparse
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

logger = logging.getLogger(__name__)

EMAIL_SUBJECT = "IRSTD-Paper-Daily"
SMTP_TIMEOUT = 20
SUPPORTED_SECURITY_MODES = {"ssl", "starttls"}


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


def build_email_message(
    content: str,
    *,
    sender: str,
    recipients: Sequence[str],
) -> EmailMessage:
    """创建主题固定、正文为 ``wechat.md`` 原文的 UTF-8 邮件。"""
    message = EmailMessage()
    message["Subject"] = EMAIL_SUBJECT
    message["From"] = sender
    message["To"] = ", ".join(recipients)
    message.set_content(content, charset="utf-8")
    return message


def send_email_message(
    settings: EmailSettings,
    content: str,
) -> None:
    """使用 SSL 或 STARTTLS 连接 SMTP 服务并发送日报。"""
    message = build_email_message(
        content,
        sender=settings.sender,
        recipients=settings.recipients,
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
    settings = load_email_settings(environ)
    if settings is None:
        logger.warning("未配置 SMTP 邮件 Secrets，跳过邮件通知")
        return False

    path = Path(markdown_path)
    if not path.is_file():
        raise EmailNotificationError(f"邮件正文文件不存在: {path}")
    content = path.read_text(encoding="utf-8")
    if not content.strip():
        raise EmailNotificationError(f"邮件正文文件为空: {path}")

    send_email_message(settings, content)
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
