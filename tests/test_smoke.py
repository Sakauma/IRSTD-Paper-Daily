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
from arxiv_daily.notifier import (  # noqa: E402
    NotificationError,
    build_daily_digest,
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
    assert "Infrared Small Target Detection" in config["kv"]["IRSTD"]
    assert config["domain_max_results"]["IRSTD"] is None
    assert "submittedDate" not in config["kv"]["IRSTD"]
    assert config["domain_start_dates"]["IRSTD"] == "2025-01-01"
    assert config["domain_lookback_days"]["IRSTD"] == 3
    assert config["publish_wechat"] is True
    assert config["wechat_notification"]["provider"] == "serverchan"
    assert config["wechat_notification"]["max_papers"] == 20


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
    assert "Demo Paper" in send.call_args.kwargs["content"]
    assert "Second paper" in send.call_args.kwargs["content"]
    assert "仅展示前" not in send.call_args.kwargs["content"]


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
        test_merge_and_storage,
        test_render_markdown,
        test_wechat_render,
        test_build_serverchan_url,
        test_build_daily_digest,
        test_send_serverchan_message,
        test_serverchan_business_failure,
        test_serverchan_http_error_hides_sendkey,
        test_notify_without_secrets_skips_safely,
        test_notify_rejects_invalid_sendkey,
        test_initial_notification_ignores_incremental_display_limit,
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
