"""Regression tests for real failure/restart paths, not external delivery."""
from copy import deepcopy
from datetime import date, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import pytest
import requests

import daily_arxiv
from arxiv_daily import codelink, emailer, fetcher, notifier, storage
from arxiv_daily.state import (
    catalog_fingerprints, changes_since_notification, fingerprint,
    notification_target, query_fingerprint,
)
from arxiv_daily.wechat import render_wechat_markdown


def paper(pid="2606.00001", **changes):
    return {
        "id": pid, "publish_date": "2026-06-01", "title": "Infrared Small Target Detection",
        "first_author": "Alice", "authors": "Alice, Bob",
        "url": f"https://arxiv.org/abs/{pid}", "code": None, **changes,
    }


@pytest.fixture
def config(tmp_path, monkeypatch):
    monkeypatch.setenv("SERVERCHAN_SENDKEY", "SCT_test-receiver")
    monkeypatch.delenv("GITHUB_REPOSITORY", raising=False)
    return {
        "user_name": "example", "repo_name": "papers",
        "data_path": str(tmp_path / "papers.json"), "state_path": str(tmp_path / "state.json"),
        "publish_readme": False, "publish_gitpage": False, "publish_wechat": False,
        "enable_code_lookup": False, "kv": {"IRSTD": "all:IRSTD"},
        "domain_max_results": {"IRSTD": None}, "domain_start_dates": {"IRSTD": "2025-01-01"},
        "domain_lookback_days": {"IRSTD": 3},
    }


def setup_catalog(config):
    data = {"IRSTD": {"2606.00001": paper()}}
    state = {
        "last_successful_update": {"IRSTD": "2026-10-02"},
        "query_fingerprints": {"IRSTD": query_fingerprint("all:IRSTD", "2025-01-01", None)},
        "catalog_checksums": {"IRSTD": fingerprint(data["IRSTD"])},
        "wechat_notification": {
            "initialized": True, "provider": "serverchan",
            "target": notification_target(config, "serverchan"),
            "delivered": catalog_fingerprints(data),
        },
    }
    storage.save_data(config["data_path"], data)
    storage.save_data(config["state_path"], state)
    return data, state


@pytest.mark.parametrize("mutation", ["missing", "empty", "partial", "query", "start_date", "limit", "legacy_state"])
def test_invalid_checkpoint_forces_rebuild(config, mutation):
    data, state = setup_catalog(config)
    path = Path(config["data_path"])
    if mutation == "missing":
        path.unlink()
    elif mutation == "empty":
        path.write_text("", encoding="utf-8")
    elif mutation == "partial":
        storage.save_data(path, {"IRSTD": {}})
    elif mutation == "query":
        config["kv"]["IRSTD"] += " OR all:infrared"
    elif mutation == "start_date":
        config["domain_start_dates"]["IRSTD"] = "2024-01-01"
    elif mutation == "limit":
        config["domain_max_results"]["IRSTD"] = 100
    else:
        state.pop("query_fingerprints")
        storage.save_data(config["state_path"], state)
    with mock.patch.object(daily_arxiv, "fetch_daily_papers", return_value=[paper()]) as fetch:
        daily_arxiv.run(config, today=date(2026, 10, 3))
    start = "20240101" if mutation == "start_date" else "20250101"
    assert f"submittedDate:[{start}0000 TO 202610032359]" in fetch.call_args.args[1]
    assert storage.load_data(path)["IRSTD"]["2606.00001"] == paper()


def test_valid_checkpoint_remains_incremental(config):
    setup_catalog(config)
    with mock.patch.object(daily_arxiv, "fetch_daily_papers", return_value=[]) as fetch:
        daily_arxiv.run(config, today=date(2026, 10, 3))
    assert "submittedDate:[202609290000 TO 202610032359]" in fetch.call_args.args[1]


def test_backfill_does_not_certify_damaged_cache(config):
    setup_catalog(config)
    storage.save_data(config["data_path"], {"IRSTD": {}})
    with mock.patch.object(daily_arxiv, "backfill_code_links", side_effect=lambda data: (data, 0)):
        daily_arxiv.run_backfill(config)
    with mock.patch.object(daily_arxiv, "fetch_daily_papers", return_value=[paper()]) as fetch:
        daily_arxiv.run(config, today=date(2026, 10, 3))
    assert "submittedDate:[202501010000" in fetch.call_args.args[1]


def test_update_does_not_certify_unfetched_topic(config):
    data, state = setup_catalog(config)
    data["Disabled"] = {"2606.00001": paper()}
    state["catalog_checksums"]["Disabled"] = fingerprint(data["Disabled"])
    state["query_fingerprints"]["Disabled"] = query_fingerprint("all:other", "2025-01-01", None)
    state["last_successful_update"]["Disabled"] = "2026-10-02"
    storage.save_data(config["state_path"], state)
    data["Disabled"] = {}
    storage.save_data(config["data_path"], data)
    with mock.patch.object(daily_arxiv, "fetch_daily_papers", return_value=[]):
        daily_arxiv.run(config, today=date(2026, 10, 3))
    config["kv"]["Disabled"] = "all:other"
    config["domain_max_results"]["Disabled"] = None
    config["domain_start_dates"]["Disabled"] = "2025-01-01"
    with mock.patch.object(daily_arxiv, "fetch_daily_papers", return_value=[paper()]) as fetch:
        daily_arxiv.run(config, today=date(2026, 10, 4))
    queries = {call.args[0]: call.args[1] for call in fetch.call_args_list}
    assert "submittedDate:[202501010000" in queries["Disabled"]


def test_json_replace_failure_preserves_previous_file(tmp_path):
    path = tmp_path / "data.json"
    storage.save_data(path, {"old": True})
    with mock.patch.object(storage.os, "replace", side_effect=OSError("disk failure")):
        with pytest.raises(OSError):
            storage.save_data(path, {"new": True})
    assert storage.load_data(path) == {"old": True}
    assert list(tmp_path.iterdir()) == [path]


@pytest.mark.parametrize("lookup", ["run", "run_backfill"])
def test_failed_notification_is_retried_without_new_changes(config, lookup):
    data, state = setup_catalog(config)
    changed = paper(code="https://github.com/example/method")

    def backfill(catalog):
        catalog["IRSTD"][changed["id"]] = changed
        return catalog, 1

    with mock.patch.object(daily_arxiv, "fetch_daily_papers", return_value=[changed]), \
         mock.patch.object(daily_arxiv, "backfill_code_links", side_effect=backfill):
        with mock.patch.object(daily_arxiv, "notify_daily_update", side_effect=notifier.NotificationError("unavailable")):
            with pytest.raises(notifier.NotificationError):
                getattr(daily_arxiv, lookup)(config, notify_wechat=True, today=date(2026, 10, 3))
        assert storage.load_data(config["data_path"])["IRSTD"][changed["id"]] == changed
        assert storage.load_data(config["state_path"])["wechat_notification"] == state["wechat_notification"]
        with mock.patch.object(daily_arxiv, "notify_daily_update", return_value=True) as retry:
            daily_arxiv.run_notification(config)
        assert retry.call_args.args[2]["IRSTD"] == [changed]
        with mock.patch.object(daily_arxiv, "notify_daily_update") as sent_again:
            daily_arxiv.run_notification(config)
        sent_again.assert_not_called()


def test_missing_sendkey_does_not_lose_changes(config, monkeypatch):
    setup_catalog(config)
    added = paper("2610.00002", title="New paper")
    monkeypatch.delenv("SERVERCHAN_SENDKEY")
    with mock.patch.object(daily_arxiv, "fetch_daily_papers", return_value=[added]):
        daily_arxiv.run(config, notify_wechat=True, today=date(2026, 10, 3))
    monkeypatch.setenv("SERVERCHAN_SENDKEY", "SCT_test-receiver")
    with mock.patch.object(daily_arxiv, "notify_daily_update", return_value=True) as send:
        daily_arxiv.run_notification(config)
    assert send.call_args.args[1]["IRSTD"] == [added]
    assert send.call_args.kwargs["initial_sync"] is False


@pytest.mark.parametrize("change_type", ["new", "updated"])
@pytest.mark.parametrize("multiple_topics", [False, True])
def test_limited_pending_digest_prioritizes_newest_changes(change_type, multiple_topics):
    older = paper("2610.00001", publish_date="2026-10-01", title="Older pending paper")
    newer = paper("2610.00008", publish_date="2026-10-08", title="Newest pending paper")
    data = {"IRSTD": {older["id"]: older}}
    data.setdefault("Other" if multiple_topics else "IRSTD", {})[newer["id"]] = newer
    delivered = {}
    if change_type == "updated":
        previous = deepcopy(data)
        for papers in previous.values():
            for record in papers.values():
                record["code"] = "https://github.com/example/previous-link"
        delivered = catalog_fingerprints(previous)
    added, updated = changes_since_notification(data, delivered)
    _, content = notifier.build_daily_digest(
        added, updated, run_date=date(2026, 10, 8), repo_url="", max_papers=1,
    )
    assert "Newest pending paper" in content
    assert "Older pending paper" not in content
    assert "本次共有 2 篇变化，仅展示前 1 篇" in content


@pytest.mark.parametrize("change", ["sendkey", "fork", "legacy"])
def test_new_notification_target_gets_full_catalog(config, monkeypatch, change):
    data, state = setup_catalog(config)
    if change == "sendkey":
        monkeypatch.setenv("SERVERCHAN_SENDKEY", "SCT_different-receiver")
    elif change == "fork":
        monkeypatch.setenv("GITHUB_REPOSITORY", "another/fork")
    else:
        state["wechat_notification"].pop("target")
        storage.save_data(config["state_path"], state)
    with mock.patch.object(daily_arxiv, "notify_daily_update", return_value=True) as send:
        daily_arxiv.run_notification(config)
    assert send.call_args.kwargs["initial_sync"] is True
    assert send.call_args.args[1]["IRSTD"] == [paper()]
    persisted = Path(config["state_path"]).read_text(encoding="utf-8")
    assert "SCT_" not in persisted


def test_multipart_failure_remains_pending(config):
    data, state = setup_catalog(config)
    state.pop("wechat_notification")
    storage.save_data(config["state_path"], state)
    messages = [("Part 1", "First paper"), ("Part 2", "Second paper")]
    with mock.patch.object(notifier, "build_initial_digests", return_value=messages):
        with mock.patch.object(notifier, "send_serverchan_message", side_effect=[{}, notifier.NotificationError("fail")]):
            with pytest.raises(notifier.NotificationError):
                daily_arxiv.run_notification(config)
        assert "wechat_notification" not in storage.load_data(config["state_path"])
        with mock.patch.object(notifier, "send_serverchan_message", return_value={}) as retry:
            daily_arxiv.run_notification(config)
        assert retry.call_count == 2


@pytest.mark.parametrize("key", ["sctp-test-key", "sctp123", "sctp123t", "sctp12tABC/extra"])
def test_serverchan_3_rejects_malformed_keys(key):
    with pytest.raises(notifier.NotificationError):
        notifier.build_serverchan_url(key)


def test_metadata_uses_explicit_implementation_not_baseline():
    baseline = "https://github.com/facebookresearch/segment-anything"
    own = "https://github.com/example/our-method"
    text = f"We build on {baseline}. Our implementation is available at {own}."
    assert codelink.extract_code_link(text) == own
    assert codelink.extract_code_link(f"We compare against the code at {baseline}.") is None
    assert codelink.extract_code_link(f"Our code: {own}. Our code: https://github.com/another/project.") is None


@pytest.mark.parametrize("manual", [False, True])
def test_fetcher_preserves_cached_code_with_referenced_baseline(manual):
    cached = "https://github.com/example/our-method"
    result = SimpleNamespace(
        get_short_id=lambda: "2606.00001v2", title="Our infrared detection method",
        authors=["Alice"], published=datetime(2026, 6, 1), updated=datetime(2026, 10, 3),
        summary="We build on https://github.com/facebookresearch/segment-anything.",
        comment="Code: https://github.com/another/project" if manual else None,
    )
    with mock.patch.object(fetcher.arxiv.Client, "results", return_value=[result]):
        records = fetcher.fetch_daily_papers(
            "IRSTD", "all:IRSTD", None, known_codes={"2606.00001": cached},
            known_paper_ids={"2606.00001"},
            known_code_sources={"2606.00001": "manual" if manual else "arxiv_metadata"},
        )
    assert records[0]["code"] == cached


@pytest.mark.parametrize("readme, expected", [
    ("A website builder with small models", False),
    ("Attention network for target detection", False),
    ("Official implementation. Paper: arXiv:2606.00001.", True),
    ("Official implementation. Paper: arXiv:2606.000010", False),
    ("A bibliography of arXiv:2606.00001", False),
    ("Official implementation of another method\n# References\narXiv:2606.00001", False),
    ("", False),
])
def test_verifier_requires_identity_and_implementation(readme, expected):
    with mock.patch.object(codelink, "_fetch_readme", return_value=readme):
        assert codelink.verify_code_link("2606.00001", paper()["title"], "https://github.com/example/project") is expected


def test_paper_list_is_not_implementation():
    with mock.patch.object(codelink, "_fetch_readme", return_value="Official implementation arXiv:2606.00001"):
        assert not codelink.verify_code_link("2606.00001", paper()["title"], "https://github.com/example/awesome-irstd")


def test_backfill_rechecks_wrong_links_but_preserves_manual():
    data = {"IRSTD": {
        "2606.00001": paper(code="https://github.com/silexlabs/Silex"),
        "2606.00002": paper("2606.00002", code="https://github.com/custom/project", code_source="manual"),
    }}
    with mock.patch.object(codelink, "verify_code_link", return_value=False), \
         mock.patch.object(codelink, "lookup_code_link", return_value=None):
        corrected, count = codelink.backfill_code_links(data)
    assert count == 1
    assert corrected["IRSTD"]["2606.00001"]["code"] is None
    assert corrected["IRSTD"]["2606.00002"]["code"] == "https://github.com/custom/project"


def test_backfill_network_failure_preserves_link():
    data = {"IRSTD": {"2606.00001": paper(code="https://github.com/example/project")}}
    original = deepcopy(data)
    with mock.patch.object(codelink, "_fetch_readme", side_effect=codelink.CodeLookupError("unavailable")):
        with pytest.raises(codelink.CodeLookupError):
            codelink.backfill_code_links(data)
    assert data == original


@pytest.mark.parametrize("status", [429, 403, 503])
def test_github_retries_original_query(status):
    failed = mock.Mock(status_code=status, headers={"X-RateLimit-Remaining": "0", "Retry-After": "1"})
    success = mock.Mock(status_code=200)
    success.json.return_value = {"items": [{"html_url": "https://github.com/example/project"}]}
    with mock.patch.object(codelink.requests, "get", side_effect=[failed, success]) as get, \
         mock.patch.object(codelink.time, "sleep"):
        assert codelink._search_repositories('"2606.00001"') == ["https://github.com/example/project"]
    assert get.call_count == 2
    assert get.call_args_list[0] == get.call_args_list[1]


def test_exhausted_requests_are_not_reported_as_no_match():
    with mock.patch.object(codelink.requests, "get", side_effect=requests.Timeout), \
         mock.patch.object(codelink.time, "sleep"):
        with pytest.raises(codelink.CodeLookupError):
            codelink._search_repositories('"2606.00001"')


@pytest.mark.parametrize("mode, constructor", [("ssl", "SMTP_SSL"), ("starttls", "SMTP")])
def test_smtp_partial_permanent_failure_is_visible(config, mode, constructor):
    settings = emailer.EmailSettings("smtp.example.com", 465, mode, "sender@example.com", "dummy", "sender@example.com", ("ok@example.com", "bad@example.com"))
    context = mock.MagicMock()
    smtp = context.__enter__.return_value
    smtp.send_message.return_value = {"bad@example.com": (550, b"rejected")}
    content = render_wechat_markdown({"IRSTD": {"2606.00001": paper()}}, show_badge=False)
    with mock.patch.object(emailer.smtplib, constructor, return_value=context):
        with pytest.raises(emailer.EmailNotificationError, match="bad@example.com"):
            emailer.send_email_message(settings, content)
    assert smtp.send_message.call_count == 1


def test_smtp_retry_only_targets_temporarily_refused_recipients():
    settings = emailer.EmailSettings("smtp.example.com", 465, "ssl", "sender@example.com", "dummy", "sender@example.com", ("ok@example.com", "later@example.com"))
    context = mock.MagicMock()
    smtp = context.__enter__.return_value
    smtp.send_message.side_effect = [{"later@example.com": (450, b"try later")}, {}]
    content = render_wechat_markdown({"IRSTD": {"2606.00001": paper()}}, show_badge=False)
    with mock.patch.object(emailer.smtplib, "SMTP_SSL", return_value=context):
        emailer.send_email_message(settings, content)
    assert [call.kwargs["to_addrs"] for call in smtp.send_message.call_args_list] == [
        ["ok@example.com", "later@example.com"], ["later@example.com"],
    ]


def test_smtp_mixed_refusals_keep_permanent_failure_after_retry():
    settings = emailer.EmailSettings("smtp.example.com", 465, "ssl", "sender@example.com", "dummy", "sender@example.com", ("bad@example.com", "later@example.com"))
    smtp = mock.Mock()
    smtp.send_message.side_effect = [
        emailer.smtplib.SMTPRecipientsRefused({
            "bad@example.com": (550, b"rejected"), "later@example.com": (450, b"temporary"),
        }), {},
    ]
    with pytest.raises(emailer.EmailNotificationError, match="bad@example.com"):
        emailer._send_to_recipients(smtp, emailer.EmailMessage(), settings)
    assert smtp.send_message.call_args.kwargs["to_addrs"] == ["later@example.com"]


def test_smtp_transient_failure_stops_after_one_retry():
    settings = emailer.EmailSettings("smtp.example.com", 465, "ssl", "sender@example.com", "dummy", "sender@example.com", ("later@example.com",))
    smtp = mock.Mock()
    smtp.send_message.return_value = {"later@example.com": (450, b"temporary")}
    with pytest.raises(emailer.EmailNotificationError, match="later@example.com"):
        emailer._send_to_recipients(smtp, emailer.EmailMessage(), settings)
    assert smtp.send_message.call_count == 2
