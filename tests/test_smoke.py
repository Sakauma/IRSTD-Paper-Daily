"""离线冒烟测试：不依赖 arXiv/GitHub 网络服务。"""

from __future__ import annotations

import os
import sys
import tempfile
from datetime import date, datetime
from unittest import mock

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

from arxiv_daily.config import add_date_range, build_query, load_config  # noqa: E402
from arxiv_daily.emailer import (  # noqa: E402
    EMAIL_SUBJECT,
    EmailNotificationError,
    build_email_message,
    load_email_settings,
    parse_recipients,
    parse_wechat_markdown,
    render_html_email,
    render_plain_email,
    send_daily_email,
)
from arxiv_daily.notifier import (  # noqa: E402
    MAX_CONTENT_BYTES,
    NotificationError,
    build_daily_digest,
    build_initial_digests,
    build_serverchan_url,
    notify_daily_update,
    send_serverchan_message,
)
from arxiv_daily.renderer import render_markdown  # noqa: E402
from arxiv_daily.storage import load_data, merge_papers, save_data  # noqa: E402
from arxiv_daily.wechat import build_wechat_data, render_wechat_markdown  # noqa: E402


def sample_paper(code: str | None = None) -> dict[str, object]:
    return {
        "id": "2608.07015",
        "publish_date": "2026-08-07",
        "title": "Demo Paper $x$",
        "first_author": "Alice",
        "authors": "Alice, Bob",
        "url": "http://arxiv.org/abs/2608.07015",
        "code": code,
    }


def sample_wechat_markdown() -> str:
    return """[![Contributors][contributors-shield]][contributors-url]

> Updated on 2026.09.04
> Usage instructions: [here](./README.md#usage)

<details>
  <summary>Table of Contents</summary>
</details>

## IRSTD

- 2026-08-07, **Demo Paper $x$**, Alice et.al., Paper: [http://arxiv.org/abs/2608.07015](http://arxiv.org/abs/2608.07015), Code: **[https://github.com/foo/bar](https://github.com/foo/bar)**

[contributors-shield]: https://img.shields.io/example
[contributors-url]: https://github.com/example
"""


def test_build_query() -> None:
    assert build_query(["Infrared Small Target Detection", "IRSTD"]) == (
        '"Infrared Small Target Detection" OR IRSTD'
    )
    assert build_query([]) == ""
    dated_query = build_query(
        ["IRSTD"],
        start_date="2025-01-01",
        end_date="2026-09-04",
    )
    assert dated_query == (
        "(IRSTD) AND submittedDate:[202501010000 TO 202609042359]"
    )
    assert add_date_range(
        "IRSTD",
        start_date="2026-09-01",
        end_date="2026-09-04",
    ) == "(IRSTD) AND submittedDate:[202609010000 TO 202609042359]"


def test_config() -> None:
    config = load_config(os.path.join(PROJECT_ROOT, "config.yaml"))
    assert list(config["kv"].keys()) == ["IRSTD"]
    assert "all:infrared" in config["kv"]["IRSTD"]
    assert 'all:"small targets"' in config["kv"]["IRSTD"]
    assert 'all:"point target"' in config["kv"]["IRSTD"]
    assert "all:MFIRST" in config["kv"]["IRSTD"]
    assert config["domain_max_results"]["IRSTD"] is None
    assert "submittedDate" not in config["kv"]["IRSTD"]
    assert config["domain_start_dates"]["IRSTD"] == "2025-01-01"
    assert config["domain_lookback_days"]["IRSTD"] == 3
    assert config["publish_wechat"] is True
    assert config["wechat_notification"]["provider"] == "serverchan"
    assert config["wechat_notification"]["max_papers"] == 20


def test_domain_query_overrides_filters() -> None:
    config_text = """domains:
    IRSTD:
        enable: true
        query: 'all:infrared AND all:"small target"'
        filters:
            - ignored keyword
"""
    with tempfile.TemporaryDirectory() as tmp_dir:
        config_path = os.path.join(tmp_dir, "config.yaml")
        with open(config_path, "w", encoding="utf-8") as file:
            file.write(config_text)
        config = load_config(config_path)

    assert config["kv"]["IRSTD"] == 'all:infrared AND all:"small target"'


def test_merge_and_storage() -> None:
    data = merge_papers({}, [sample_paper()], "IRSTD")
    data = merge_papers(data, [sample_paper()], "IRSTD")
    assert len(data["IRSTD"]) == 1

    with tempfile.TemporaryDirectory() as tmp_dir:
        path = os.path.join(tmp_dir, "nested", "data.json")
        save_data(path, data)
        assert load_data(path) == data
        assert load_data(os.path.join(tmp_dir, "missing.json")) == {}


def test_render_markdown() -> None:
    data = {"IRSTD": {"2608.07015": sample_paper()}}
    readme = render_markdown(data, format="readme", show_badge=False)
    assert "## IRSTD" in readme
    assert "2608.07015" in readme
    assert "null" in readme
    assert "Demo Paper $x$" in readme

    web = render_markdown(data, format="web")
    assert web.startswith("---\nlayout: default\n---")


def test_wechat_render() -> None:
    data = {"IRSTD": {"2608.07015": sample_paper("https://github.com/foo/bar")}}
    wechat_data = build_wechat_data(data)
    assert "Code: **[https://github.com/foo/bar]" in wechat_data["IRSTD"]["2608.07015"]
    output = render_wechat_markdown(
        wechat_data,
        show_badge=False,
        user_name="Sakauma",
        repo_name="IRSTD-Paper-Daily",
    )
    assert "## IRSTD" in output
    assert "Paper: [http://arxiv.org/abs/2608.07015]" in output


def test_load_email_settings() -> None:
    settings = load_email_settings(
        {
            "SMTP_HOST": "smtp.example.com",
            "SMTP_USERNAME": "sender@example.com",
            "SMTP_PASSWORD": "app-password",
            "EMAIL_TO": "first@example.com; second@example.com,first@example.com",
        }
    )

    assert settings is not None
    assert settings.host == "smtp.example.com"
    assert settings.port == 465
    assert settings.security == "ssl"
    assert settings.sender == "sender@example.com"
    assert settings.recipients == ("first@example.com", "second@example.com")
    assert parse_recipients("one@example.com,two@example.com") == (
        "one@example.com",
        "two@example.com",
    )


def test_email_settings_skip_or_reject_incomplete_configuration() -> None:
    assert load_email_settings({}) is None
    try:
        load_email_settings({"SMTP_HOST": "smtp.example.com"})
    except EmailNotificationError as exc:
        assert "SMTP_USERNAME" in str(exc)
        assert "SMTP_PASSWORD" in str(exc)
        assert "EMAIL_TO" in str(exc)
    else:
        raise AssertionError("不完整 SMTP 配置应当被拒绝")


def test_render_formatted_email() -> None:
    digest = parse_wechat_markdown(sample_wechat_markdown())
    plain_content = render_plain_email(digest)
    html_content = render_html_email(
        digest,
        repository_url="https://github.com/Sakauma/IRSTD-Paper-Daily",
    )

    assert digest.updated_on == "2026-09-04"
    assert len(digest.papers) == 1
    assert "1. Demo Paper $x$" in plain_content
    assert "**" not in plain_content
    assert "contributors-shield" not in plain_content
    assert '<article class="paper-card">' in html_content
    assert "更新日期：2026-09-04 · 共 1 篇" in html_content
    assert 'href="http://arxiv.org/abs/2608.07015"' in html_content
    assert 'href="https://github.com/foo/bar"' in html_content
    assert "查看论文" in html_content
    assert "查看代码" in html_content
    assert "contributors-shield" not in html_content
    assert "<details>" not in html_content

    unsafe_digest = parse_wechat_markdown(
        "## IRSTD\n"
        "- 2026-09-04, **<script>alert(1)</script>**, Alice et.al., "
        "Paper: [unsafe](javascript:evil)\n"
    )
    unsafe_html = render_html_email(unsafe_digest)
    assert "<script>" not in unsafe_html
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in unsafe_html
    assert "javascript:evil" not in unsafe_html


def test_formatted_email_limits_size_and_keeps_newest_papers() -> None:
    lines = ["> Updated on 2026.09.04", "", "## IRSTD", ""]
    for index in range(1, 13):
        paper_url = f"https://arxiv.org/abs/2609.{index:05d}"
        lines.append(
            f"- 2026-09-{index:02d}, **Paper {index} {'红' * 300}**, "
            f"Author {index} et.al., Paper: [{paper_url}]({paper_url})"
        )
    markdown = "\n".join(lines) + "\n"
    digest = parse_wechat_markdown(markdown)
    repository_url = "https://github.com/Sakauma/IRSTD-Paper-Daily"
    max_bytes = 10_000

    html_content = render_html_email(
        digest,
        repository_url=repository_url,
        max_bytes=max_bytes,
    )
    message = build_email_message(
        markdown,
        sender="sender@example.com",
        recipients=["reader@example.com"],
        repository_url=repository_url,
        max_html_bytes=max_bytes,
    )

    assert len(html_content.encode("utf-8")) <= max_bytes
    assert "2609.00012" in html_content
    assert "2609.00001" not in html_content
    assert "仅显示最新" in html_content
    assert "较早的" in html_content
    assert "在 GitHub 查看完整目录" in html_content
    plain_part = message.get_body(preferencelist=("plain",))
    assert plain_part is not None
    plain_content = plain_part.get_content()
    assert "2609.00012" in plain_content
    assert "2609.00001" not in plain_content
    assert f"完整目录：{repository_url}" in plain_content


def test_send_daily_email_with_ssl() -> None:
    content = sample_wechat_markdown()
    smtp_client = mock.Mock()
    smtp_context = mock.MagicMock()
    smtp_context.__enter__.return_value = smtp_client
    environ = {
        "SMTP_HOST": "smtp.example.com",
        "SMTP_PORT": "465",
        "SMTP_SECURITY": "ssl",
        "SMTP_USERNAME": "sender@example.com",
        "SMTP_PASSWORD": "app-password",
        "EMAIL_FROM": "papers@example.com",
        "EMAIL_TO": "reader@example.com",
        "GITHUB_REPOSITORY": "Sakauma/IRSTD-Paper-Daily",
    }

    with tempfile.TemporaryDirectory() as tmp_dir:
        markdown_path = os.path.join(tmp_dir, "wechat.md")
        with open(markdown_path, "w", encoding="utf-8") as file:
            file.write(content)
        with mock.patch(
            "arxiv_daily.emailer.smtplib.SMTP_SSL",
            return_value=smtp_context,
        ) as smtp_ssl:
            sent = send_daily_email(markdown_path, environ=environ)

    assert sent is True
    smtp_ssl.assert_called_once()
    smtp_client.login.assert_called_once_with("sender@example.com", "app-password")
    message = smtp_client.send_message.call_args.args[0]
    assert message["Subject"] == EMAIL_SUBJECT
    assert message["From"] == "papers@example.com"
    assert message["To"] == "reader@example.com"
    assert message.is_multipart()
    plain_part = message.get_body(preferencelist=("plain",))
    html_part = message.get_body(preferencelist=("html",))
    assert plain_part is not None
    assert html_part is not None
    assert "1. Demo Paper $x$" in plain_part.get_content()
    assert '<article class="paper-card">' in html_part.get_content()
    assert "在 GitHub 查看完整目录" in html_part.get_content()


def test_send_daily_email_with_starttls() -> None:
    smtp_client = mock.Mock()
    smtp_context = mock.MagicMock()
    smtp_context.__enter__.return_value = smtp_client
    environ = {
        "SMTP_HOST": "smtp.example.com",
        "SMTP_PORT": "587",
        "SMTP_SECURITY": "starttls",
        "SMTP_USERNAME": "sender@example.com",
        "SMTP_PASSWORD": "app-password",
        "EMAIL_TO": "reader@example.com",
    }

    with tempfile.TemporaryDirectory() as tmp_dir:
        markdown_path = os.path.join(tmp_dir, "wechat.md")
        with open(markdown_path, "w", encoding="utf-8") as file:
            file.write(sample_wechat_markdown())
        with mock.patch(
            "arxiv_daily.emailer.smtplib.SMTP",
            return_value=smtp_context,
        ) as smtp:
            sent = send_daily_email(markdown_path, environ=environ)

    assert sent is True
    smtp.assert_called_once()
    assert smtp_client.ehlo.call_count == 2
    smtp_client.starttls.assert_called_once()
    smtp_client.login.assert_called_once_with("sender@example.com", "app-password")


def test_send_daily_email_without_secrets_skips_safely() -> None:
    with mock.patch("arxiv_daily.emailer.send_email_message") as send:
        sent = send_daily_email("missing-wechat.md", environ={})

    assert sent is False
    send.assert_not_called()


def test_build_serverchan_url() -> None:
    assert build_serverchan_url("SCT_test-key_123") == (
        "https://sctapi.ftqq.com/SCT_test-key_123.send"
    )
    assert build_serverchan_url("sctp-test-key") == (
        "https://sctp-test-key.push.ft07.com/send"
    )

    try:
        build_serverchan_url("invalid-key")
    except NotificationError as exc:
        assert "SERVERCHAN_SENDKEY 无效" in str(exc)
    else:
        raise AssertionError("无效 Server酱 SendKey 应当被拒绝")


def test_build_daily_digest() -> None:
    new_paper = sample_paper("https://github.com/foo/new")
    updated_paper = sample_paper("https://github.com/foo/updated")
    updated_paper["id"] = "2608.07016"
    updated_paper["title"] = "Updated Demo Paper"

    summary, content = build_daily_digest(
        {"IRSTD": [new_paper]},
        {"IRSTD": [updated_paper]},
        run_date=date(2026, 9, 4),
        repo_url="https://github.com/Sakauma/IRSTD-Paper-Daily",
    )
    assert "新增 1 篇，更新 1 篇" in summary
    assert "## 新增论文" in content
    assert "## 更新论文" in content
    assert "Demo Paper" in content
    assert "Updated Demo Paper" in content
    assert "https://github.com/foo/new" in content

    empty_summary, empty_content = build_daily_digest(
        {},
        {},
        run_date=date(2026, 9, 4),
        repo_url="",
    )
    assert "今日无新增" in empty_summary
    assert "未发现新增或发生变化" in empty_content

    initial_summary, initial_content = build_daily_digest(
        {"IRSTD": [new_paper, updated_paper]},
        {},
        run_date=date(2026, 9, 4),
        repo_url="",
        max_papers=2,
        initial_sync=True,
    )
    assert "首次同步 2 篇" in initial_summary
    assert "## 完整论文目录" in initial_content


def test_daily_digest_truncates_by_utf8_bytes() -> None:
    paper = sample_paper()
    paper["title"] = "红外小目标" * 3_000
    _, content = build_daily_digest(
        {"IRSTD": [paper]},
        {},
        run_date=date(2026, 9, 4),
        repo_url="",
        max_papers=1,
    )

    assert len(content.encode("utf-8")) <= MAX_CONTENT_BYTES
    assert content.endswith("内容已按 Server酱长度限制截断。")


def test_initial_digests_split_and_omit_oldest_papers() -> None:
    papers = []
    for index in range(1, 9):
        paper = sample_paper()
        paper_id = f"2601.{index:05d}"
        paper["id"] = paper_id
        paper["publish_date"] = f"2026-01-{index:02d}"
        paper["url"] = f"http://arxiv.org/abs/{paper_id}"
        paper["title"] = f"Paper {index} " + "红" * 160
        papers.append(paper)

    messages = build_initial_digests(
        {"IRSTD": papers},
        run_date=date(2026, 9, 4),
        repo_url="",
        max_content_bytes=1_024,
        max_messages=5,
    )

    assert len(messages) == 5
    assert [summary.rsplit(" ", 1)[-1] for summary, _ in messages] == [
        "1/5",
        "2/5",
        "3/5",
        "4/5",
        "5/5",
    ]
    assert all(len(content.encode("utf-8")) <= 1_024 for _, content in messages)
    combined_content = "\n".join(content for _, content in messages)
    for index in range(4, 9):
        assert f"2601.{index:05d}" in combined_content
    for index in range(1, 4):
        assert f"2601.{index:05d}" not in combined_content
    assert "较早的 **3** 篇论文未展示" in combined_content


def test_send_serverchan_message() -> None:
    response = mock.Mock()
    response.json.return_value = {"code": 0, "message": "SUCCESS"}
    with mock.patch(
        "arxiv_daily.notifier.requests.post",
        return_value=response,
    ) as post:
        result = send_serverchan_message(
            sendkey="SCT_test-key",
            summary="测试摘要",
            content="# 测试内容",
        )

    assert result["code"] == 0
    response.raise_for_status.assert_called_once_with()
    payload = post.call_args.kwargs["json"]
    assert post.call_args.args[0] == (
        "https://sctapi.ftqq.com/SCT_test-key.send"
    )
    assert payload == {
        "title": "测试摘要",
        "desp": "# 测试内容",
        "short": "测试摘要",
    }
    assert post.call_args.kwargs["timeout"] == 15


def test_serverchan_business_failure() -> None:
    response = mock.Mock()
    response.json.return_value = {"code": 1, "message": "发送失败"}
    with mock.patch(
        "arxiv_daily.notifier.requests.post",
        return_value=response,
    ):
        try:
            send_serverchan_message(
                sendkey="SCT_test-key",
                summary="测试摘要",
                content="# 测试内容",
            )
        except NotificationError as exc:
            assert "发送失败" in str(exc)
        else:
            raise AssertionError("Server酱业务失败应当抛出 NotificationError")


def test_serverchan_http_error_hides_sendkey() -> None:
    from arxiv_daily import notifier

    secret = "SCT_secret-must-not-leak"
    response = mock.Mock()
    response.raise_for_status.side_effect = notifier.requests.HTTPError(
        f"500 Server Error: https://sctapi.ftqq.com/{secret}.send"
    )
    with mock.patch(
        "arxiv_daily.notifier.requests.post",
        return_value=response,
    ):
        try:
            send_serverchan_message(
                sendkey=secret,
                summary="测试摘要",
                content="# 测试内容",
            )
        except NotificationError as exc:
            assert secret not in str(exc)
            assert exc.__cause__ is None
        else:
            raise AssertionError("Server酱 HTTP 失败应当抛出 NotificationError")


def test_notify_without_secrets_skips_safely() -> None:
    with mock.patch.dict(os.environ, {}, clear=True), mock.patch(
        "arxiv_daily.notifier.send_serverchan_message"
    ) as send:
        sent = notify_daily_update({}, {}, {}, run_date=date(2026, 9, 4))

    assert sent is False
    send.assert_not_called()


def test_notify_unchanged_sends_daily_status() -> None:
    config = {
        "wechat_notification": {"provider": "serverchan", "max_papers": 20}
    }
    with mock.patch.dict(
        os.environ,
        {"SERVERCHAN_SENDKEY": "SCT_test-key"},
        clear=True,
    ), mock.patch("arxiv_daily.notifier.send_serverchan_message") as send:
        sent = notify_daily_update(
            config,
            {},
            {},
            run_date=date(2026, 9, 4),
            notify_unchanged=True,
        )

    assert sent is True
    send.assert_called_once()
    assert "今日无新增" in send.call_args.kwargs["summary"]
    assert "未发现新增或发生变化" in send.call_args.kwargs["content"]


def test_notify_rejects_invalid_sendkey() -> None:
    with mock.patch.dict(os.environ, {"SERVERCHAN_SENDKEY": "invalid"}, clear=True):
        try:
            notify_daily_update(
                {},
                {"IRSTD": [sample_paper()]},
                {},
                run_date=date(2026, 9, 4),
            )
        except NotificationError as exc:
            assert "SERVERCHAN_SENDKEY 无效" in str(exc)
        else:
            raise AssertionError("无效 Server酱 SendKey 应当报错")


def test_initial_notification_ignores_incremental_display_limit() -> None:
    first = sample_paper()
    second = sample_paper()
    second["id"] = "2608.07016"
    second["title"] = "Second paper"
    config = {
        "wechat_notification": {"provider": "serverchan", "max_papers": 1}
    }
    with mock.patch.dict(
        os.environ,
        {"SERVERCHAN_SENDKEY": "SCT_test-key"},
        clear=True,
    ), mock.patch("arxiv_daily.notifier.send_serverchan_message") as send:
        sent = notify_daily_update(
            config,
            {"IRSTD": [first, second]},
            {},
            run_date=date(2026, 9, 4),
            initial_sync=True,
        )

    assert sent is True
    send.assert_called_once()
    assert "Demo Paper" in send.call_args.kwargs["content"]
    assert "Second paper" in send.call_args.kwargs["content"]
    assert "仅展示前" not in send.call_args.kwargs["content"]


def test_initial_notification_sends_every_digest_part() -> None:
    config = {"wechat_notification": {"provider": "serverchan"}}
    messages = [("首次同步 1/2", "第一条"), ("首次同步 2/2", "第二条")]
    with mock.patch.dict(
        os.environ,
        {"SERVERCHAN_SENDKEY": "SCT_test-key"},
        clear=True,
    ), mock.patch(
        "arxiv_daily.notifier.build_initial_digests",
        return_value=messages,
    ), mock.patch("arxiv_daily.notifier.send_serverchan_message") as send:
        sent = notify_daily_update(
            config,
            {"IRSTD": [sample_paper()]},
            {},
            run_date=date(2026, 9, 4),
            initial_sync=True,
        )

    assert sent is True
    assert [call.kwargs["content"] for call in send.call_args_list] == [
        "第一条",
        "第二条",
    ]


def test_incremental_notification_remains_one_message() -> None:
    papers = []
    for index in range(20):
        paper = sample_paper()
        paper["id"] = f"2608.{index:05d}"
        paper["title"] = "红外小目标" * 500
        papers.append(paper)
    config = {
        "wechat_notification": {"provider": "serverchan", "max_papers": 20}
    }

    with mock.patch.dict(
        os.environ,
        {"SERVERCHAN_SENDKEY": "SCT_test-key"},
        clear=True,
    ), mock.patch("arxiv_daily.notifier.send_serverchan_message") as send:
        sent = notify_daily_update(
            config,
            {"IRSTD": papers},
            {},
            run_date=date(2026, 9, 4),
        )

    assert sent is True
    send.assert_called_once()
    content = send.call_args.kwargs["content"]
    assert len(content.encode("utf-8")) <= MAX_CONTENT_BYTES
    assert content.endswith("内容已按 Server酱长度限制截断。")


def test_lookup_and_verify_code_link() -> None:
    from arxiv_daily import codelink

    with mock.patch.object(
        codelink,
        "_search_repositories",
        return_value=[
            "https://github.com/unrelated/project",
            "https://github.com/foo/bar",
        ],
    ), mock.patch.object(
        codelink,
        "verify_code_link",
        side_effect=[False, True],
    ), mock.patch.object(codelink.time, "sleep"):
        assert codelink.lookup_code_link("2608.07015", "Demo Title") == (
            "https://github.com/foo/bar"
        )

    def fake_get(url: str, *args: object, **kwargs: object) -> mock.Mock:
        response = mock.Mock()
        response.status_code = 200
        response.text = "Official implementation. Paper: arXiv:2608.05771"
        return response

    with mock.patch.object(codelink.requests, "get", side_effect=fake_get):
        assert codelink.verify_code_link(
            "2608.05771",
            "HyTBE: Hyperbolic Target-Background Expert Model",
            "https://github.com/PepperCS/HyTBE",
        )


def test_extract_code_link_from_arxiv_metadata() -> None:
    from arxiv_daily.codelink import extract_code_link

    summary = (
        "Code is available at https://github.com/Sakauma/SPARK-SAM. "
        "The repository contains the official implementation."
    )
    assert extract_code_link(summary) == "https://github.com/Sakauma/SPARK-SAM"


def test_fetcher_reuses_cached_code_when_lookup_is_disabled() -> None:
    from arxiv_daily import fetcher

    result = mock.Mock()
    result.get_short_id.return_value = "arXiv:2608.07015v2"
    result.title = "Demo Paper"
    result.authors = ["Alice", "Bob"]
    result.updated = datetime(2026, 8, 7)
    result.published = datetime(2026, 8, 6)
    result.summary = ""
    result.comment = None

    fake_client = mock.Mock()
    fake_client.results.return_value = [result]
    with mock.patch.object(fetcher.arxiv, "Search"), mock.patch.object(
        fetcher.arxiv, "Client", return_value=fake_client
    ), mock.patch.object(fetcher, "lookup_code_link") as lookup:
        papers = fetcher.fetch_daily_papers(
            "IRSTD",
            "IRSTD",
            1,
            known_codes={"2608.07015": "https://github.com/foo/bar"},
            lookup_missing_code=False,
        )

    lookup.assert_not_called()
    assert papers[0]["id"] == "2608.07015"
    assert papers[0]["code"] == "https://github.com/foo/bar"


def test_fetcher_prefers_official_arxiv_code_link() -> None:
    from arxiv_daily import fetcher

    result = mock.Mock()
    result.get_short_id.return_value = "2608.20754v2"
    result.title = "SPARK-SAM: Learning How to Prompt and Respond"
    result.authors = ["Aji Mao"]
    result.updated = datetime(2026, 8, 26)
    result.published = datetime(2026, 8, 21)
    result.summary = (
        "Code is available at https://github.com/Sakauma/SPARK-SAM."
    )
    result.comment = None

    fake_client = mock.Mock()
    fake_client.results.return_value = [result]
    with mock.patch.object(fetcher.arxiv, "Search"), mock.patch.object(
        fetcher.arxiv, "Client", return_value=fake_client
    ), mock.patch.object(fetcher, "lookup_code_link") as lookup:
        papers = fetcher.fetch_daily_papers(
            "IRSTD",
            "IRSTD",
            None,
            known_codes={"2608.20754": "https://github.com/wrong/repository"},
            known_paper_ids={"2608.20754"},
        )

    lookup.assert_not_called()
    assert papers[0]["code"] == "https://github.com/Sakauma/SPARK-SAM"


def test_run_renders_all_enabled_outputs() -> None:
    import daily_arxiv

    with tempfile.TemporaryDirectory() as tmp_dir:
        config = {
            "user_name": "Sakauma",
            "repo_name": "IRSTD-Paper-Daily",
            "data_path": os.path.join(tmp_dir, "data.json"),
            "state_path": os.path.join(tmp_dir, "state.json"),
            "wechat_data_path": os.path.join(tmp_dir, "wechat.json"),
            "md_readme_path": os.path.join(tmp_dir, "README.md"),
            "md_gitpage_path": os.path.join(tmp_dir, "docs", "index.md"),
            "md_wechat_path": os.path.join(tmp_dir, "docs", "wechat.md"),
            "publish_readme": True,
            "publish_gitpage": True,
            "publish_wechat": True,
            "show_badge": False,
            "show_authors": True,
            "show_links": True,
            "enable_code_lookup": False,
            "kv": {"IRSTD": "IRSTD"},
            "domain_max_results": {"IRSTD": None},
            "domain_start_dates": {"IRSTD": "2025-01-01"},
            "domain_lookback_days": {"IRSTD": 3},
        }
        with mock.patch.object(
            daily_arxiv,
            "fetch_daily_papers",
            return_value=[sample_paper("https://github.com/foo/bar")],
        ):
            daily_arxiv.run(config, today=date(2026, 9, 4))

        assert "Demo Paper" in open(config["md_readme_path"], encoding="utf-8").read()
        assert "Demo Paper" in open(
            config["md_gitpage_path"], encoding="utf-8"
        ).read()
        assert "Paper:" in open(config["md_wechat_path"], encoding="utf-8").read()
        assert os.path.exists(config["wechat_data_path"])
        assert load_data(config["state_path"])["last_successful_update"] == {
            "IRSTD": "2026-09-04"
        }


def test_incremental_and_full_refresh_windows() -> None:
    import daily_arxiv

    with tempfile.TemporaryDirectory() as tmp_dir:
        data_path = os.path.join(tmp_dir, "data.json")
        state_path = os.path.join(tmp_dir, "state.json")
        save_data(data_path, {"IRSTD": {"2608.07015": sample_paper()}})
        save_data(
            state_path,
            {"last_successful_update": {"IRSTD": "2026-09-04"}},
        )
        config = {
            "data_path": data_path,
            "state_path": state_path,
            "publish_readme": False,
            "publish_gitpage": False,
            "publish_wechat": False,
            "enable_code_lookup": False,
            "max_results": None,
            "incremental_lookback_days": 3,
            "kv": {"IRSTD": "IRSTD"},
            "domain_max_results": {"IRSTD": None},
            "domain_start_dates": {"IRSTD": "2025-01-01"},
            "domain_lookback_days": {"IRSTD": 3},
        }

        with mock.patch.object(
            daily_arxiv,
            "fetch_daily_papers",
            return_value=[],
        ) as fetch:
            daily_arxiv.run(config, today=date(2026, 9, 5))
            incremental_query = fetch.call_args.args[1]
            assert "submittedDate:[202609010000 TO 202609052359]" in (
                incremental_query
            )

            daily_arxiv.run(
                config,
                full_refresh=True,
                today=date(2026, 9, 6),
            )
            full_query = fetch.call_args.args[1]
            assert "submittedDate:[202501010000 TO 202609062359]" in full_query

        assert load_data(state_path)["last_successful_update"]["IRSTD"] == (
            "2026-09-06"
        )


def test_run_notifies_only_new_and_updated_papers() -> None:
    import daily_arxiv

    with tempfile.TemporaryDirectory() as tmp_dir:
        data_path = os.path.join(tmp_dir, "data.json")
        state_path = os.path.join(tmp_dir, "state.json")
        unchanged = sample_paper()
        old_version = sample_paper()
        old_version["id"] = "2608.07016"
        old_version["title"] = "Old title"
        save_data(
            data_path,
            {
                "IRSTD": {
                    str(unchanged["id"]): unchanged,
                    str(old_version["id"]): old_version,
                }
            },
        )
        save_data(
            state_path,
            {
                "wechat_notification": {
                    "initialized": True,
                    "provider": "serverchan",
                }
            },
        )

        updated = dict(old_version)
        updated["title"] = "New title"
        new_paper = sample_paper()
        new_paper["id"] = "2608.07017"
        new_paper["title"] = "Brand new paper"
        config = {
            "data_path": data_path,
            "state_path": state_path,
            "publish_readme": False,
            "publish_gitpage": False,
            "publish_wechat": False,
            "enable_code_lookup": False,
            "kv": {"IRSTD": "IRSTD"},
            "domain_max_results": {"IRSTD": None},
            "domain_start_dates": {"IRSTD": "2025-01-01"},
            "domain_lookback_days": {"IRSTD": 3},
        }
        with mock.patch.object(
            daily_arxiv,
            "fetch_daily_papers",
            return_value=[unchanged, updated, new_paper],
        ), mock.patch.object(daily_arxiv, "notify_daily_update") as notify:
            daily_arxiv.run(
                config,
                notify_wechat=True,
                today=date(2026, 9, 4),
            )

        notify.assert_called_once()
        notified_new = notify.call_args.args[1]["IRSTD"]
        notified_updated = notify.call_args.args[2]["IRSTD"]
        assert [paper["id"] for paper in notified_new] == ["2608.07017"]
        assert [paper["id"] for paper in notified_updated] == ["2608.07016"]
        assert notify.call_args.kwargs["initial_sync"] is False


def test_first_run_notifies_with_full_catalog_then_skips_unchanged() -> None:
    import daily_arxiv

    with tempfile.TemporaryDirectory() as tmp_dir:
        data_path = os.path.join(tmp_dir, "data.json")
        state_path = os.path.join(tmp_dir, "state.json")
        first = sample_paper()
        second = sample_paper()
        second["id"] = "2608.07016"
        second["title"] = "Second paper"
        save_data(
            data_path,
            {"IRSTD": {str(first["id"]): first, str(second["id"]): second}},
        )
        save_data(
            state_path,
            {
                "wechat_notification": {
                    "initialized": True,
                    "last_successful_notification": "2026-09-04",
                }
            },
        )
        config = {
            "data_path": data_path,
            "state_path": state_path,
            "publish_readme": False,
            "publish_gitpage": False,
            "publish_wechat": False,
            "enable_code_lookup": False,
            "kv": {"IRSTD": "IRSTD"},
            "domain_max_results": {"IRSTD": None},
            "domain_start_dates": {"IRSTD": "2025-01-01"},
            "domain_lookback_days": {"IRSTD": 3},
        }
        with mock.patch.object(
            daily_arxiv,
            "fetch_daily_papers",
            return_value=[first, second],
        ), mock.patch.object(
            daily_arxiv,
            "notify_daily_update",
            return_value=True,
        ) as notify:
            daily_arxiv.run(
                config,
                notify_wechat=True,
                today=date(2026, 9, 4),
            )

            full_catalog = notify.call_args.args[1]["IRSTD"]
            assert [paper["id"] for paper in full_catalog] == [
                "2608.07015",
                "2608.07016",
            ]
            assert notify.call_args.kwargs["initial_sync"] is True
            notification_state = load_data(state_path)["wechat_notification"]
            assert notification_state["initialized"]
            assert notification_state["provider"] == "serverchan"

            notify.reset_mock()
            daily_arxiv.run(
                config,
                notify_wechat=True,
                today=date(2026, 9, 5),
            )
            notify.assert_not_called()

            daily_arxiv.run(
                config,
                notify_wechat=True,
                notify_unchanged=True,
                today=date(2026, 9, 5),
            )
            notify.assert_called_once()
            assert notify.call_args.args[1] == {"IRSTD": []}
            assert notify.call_args.args[2] == {"IRSTD": []}
            assert notify.call_args.kwargs["notify_unchanged"] is True


def test_full_refresh_notifies_after_code_backfill() -> None:
    import daily_arxiv

    with tempfile.TemporaryDirectory() as tmp_dir:
        data_path = os.path.join(tmp_dir, "data.json")
        state_path = os.path.join(tmp_dir, "state.json")
        paper = sample_paper()
        save_data(data_path, {"IRSTD": {str(paper["id"]): paper}})
        save_data(
            state_path,
            {
                "wechat_notification": {
                    "initialized": True,
                    "provider": "serverchan",
                }
            },
        )
        config = {
            "data_path": data_path,
            "state_path": state_path,
            "publish_readme": False,
            "publish_gitpage": False,
            "publish_wechat": False,
            "enable_code_lookup": False,
            "kv": {"IRSTD": "IRSTD"},
            "domain_max_results": {"IRSTD": None},
            "domain_start_dates": {"IRSTD": "2025-01-01"},
            "domain_lookback_days": {"IRSTD": 3},
        }

        def add_code_link(data: dict[str, object]) -> tuple[dict[str, object], int]:
            topics = data["IRSTD"]
            assert isinstance(topics, dict)
            topics["2608.07015"]["code"] = "https://github.com/foo/new-code"
            return data, 1

        with mock.patch.object(
            daily_arxiv,
            "fetch_daily_papers",
            return_value=[paper],
        ), mock.patch.object(
            daily_arxiv,
            "backfill_code_links",
            side_effect=add_code_link,
        ), mock.patch.object(daily_arxiv, "notify_daily_update") as notify:
            daily_arxiv.run(
                config,
                full_refresh=True,
                backfill_code=True,
                notify_wechat=True,
                today=date(2026, 9, 4),
            )

        notified_updated = notify.call_args.args[2]["IRSTD"]
        assert notified_updated[0]["code"] == "https://github.com/foo/new-code"


def test_date_is_available() -> None:
    assert date.today().isoformat()


if __name__ == "__main__":
    tests = [
        test_build_query,
        test_config,
        test_domain_query_overrides_filters,
        test_merge_and_storage,
        test_render_markdown,
        test_wechat_render,
        test_load_email_settings,
        test_email_settings_skip_or_reject_incomplete_configuration,
        test_render_formatted_email,
        test_formatted_email_limits_size_and_keeps_newest_papers,
        test_send_daily_email_with_ssl,
        test_send_daily_email_with_starttls,
        test_send_daily_email_without_secrets_skips_safely,
        test_build_serverchan_url,
        test_build_daily_digest,
        test_daily_digest_truncates_by_utf8_bytes,
        test_initial_digests_split_and_omit_oldest_papers,
        test_send_serverchan_message,
        test_serverchan_business_failure,
        test_serverchan_http_error_hides_sendkey,
        test_notify_without_secrets_skips_safely,
        test_notify_unchanged_sends_daily_status,
        test_notify_rejects_invalid_sendkey,
        test_initial_notification_ignores_incremental_display_limit,
        test_initial_notification_sends_every_digest_part,
        test_incremental_notification_remains_one_message,
        test_lookup_and_verify_code_link,
        test_extract_code_link_from_arxiv_metadata,
        test_fetcher_reuses_cached_code_when_lookup_is_disabled,
        test_fetcher_prefers_official_arxiv_code_link,
        test_run_renders_all_enabled_outputs,
        test_incremental_and_full_refresh_windows,
        test_run_notifies_only_new_and_updated_papers,
        test_first_run_notifies_with_full_catalog_then_skips_unchanged,
        test_full_refresh_notifies_after_code_backfill,
        test_date_is_available,
    ]
    for test in tests:
        test()
        print(f"PASS {test.__name__}")
    print("所有离线冒烟测试通过。")
