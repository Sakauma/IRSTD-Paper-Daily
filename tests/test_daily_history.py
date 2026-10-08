"""Daily history refresh at the arXiv/API boundary, without external delivery."""
from datetime import date, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import pytest

import daily_arxiv
from arxiv_daily import codelink, fetcher, storage
from arxiv_daily.config import load_config
from arxiv_daily.state import catalog_fingerprints, fingerprint, notification_target, query_fingerprint


TODAY = date(2026, 10, 8)


def paper(pid, **changes):
    return {
        "id": pid, "publish_date": "2026-06-01",
        "title": f"Infrared Small Target Detection {pid}",
        "first_author": "Alice", "authors": "Alice, Bob",
        "url": f"http://arxiv.org/abs/{pid}", "code": None, **changes,
    }


def result(pid, *, updated="2026-06-01", title=None, summary="", comment=None):
    return SimpleNamespace(
        get_short_id=lambda: f"{pid}v2",
        title=title or paper(pid)["title"], authors=["Alice", "Bob"],
        published=datetime(2026, 6, 1), updated=datetime.fromisoformat(updated),
        summary=summary, comment=comment,
    )


@pytest.fixture
def config(tmp_path, monkeypatch):
    config = load_config(Path(__file__).resolve().parents[1] / "config.yaml")
    assert config["refresh_history"] is True
    config.update({
        "user_name": "example", "repo_name": "papers",
        "data_path": str(tmp_path / "papers.json"),
        "state_path": str(tmp_path / "state.json"),
        "md_readme_path": str(tmp_path / "README.md"),
        "publish_readme": False, "publish_gitpage": False, "publish_wechat": False,
    })
    monkeypatch.setenv("SERVERCHAN_SENDKEY", "SCT_test-receiver")
    monkeypatch.delenv("GITHUB_REPOSITORY", raising=False)
    return config


def seed(config, data):
    state = {
        "last_successful_update": {topic: "2026-10-07" for topic in data},
        "query_fingerprints": {
            topic: query_fingerprint(
                config["kv"].get(topic, "all:disabled"),
                config["domain_start_dates"].get(topic, "2025-01-01"),
                config["domain_max_results"].get(topic),
            )
            for topic in data
        },
        "catalog_checksums": {topic: fingerprint(papers) for topic, papers in data.items()},
        "wechat_notification": {
            "initialized": True, "provider": "serverchan",
            "target": notification_target(config, "serverchan"),
            "delivered": catalog_fingerprints(data),
            "last_successful_notification": "2026-10-07",
        },
    }
    storage.save_data(config["data_path"], data)
    storage.save_data(config["state_path"], state)
    return state


def test_history_fetch_batches_latest_ids_and_reuses_client():
    ids = [f"2601.{index:05d}" for index in range(205)]
    requested = [f"arXiv:{pid}v1" for pid in ids] + [ids[0], " ", f" {ids[-1]}v3 "]
    client = mock.Mock()
    client.results.side_effect = lambda search: [
        result(pid, updated=TODAY.isoformat()) for pid in reversed(search.id_list)
    ]
    with mock.patch.object(fetcher.arxiv, "Client", return_value=client) as create_client, \
         mock.patch.object(fetcher, "lookup_code_link") as lookup:
        records = fetcher.fetch_papers_by_id(requested)

    create_client.assert_called_once()
    searches = [call.args[0] for call in client.results.call_args_list]
    assert [search.id_list for search in searches] == [ids[:100], ids[100:200], ids[200:]]
    assert [search.max_results for search in searches] == [100, 100, 5]
    assert all(not search.query for search in searches)
    assert {record["id"] for record in records} == set(ids)
    assert all(record["publish_date"] == TODAY.isoformat() for record in records)
    lookup.assert_not_called()


def test_empty_history_does_not_request_arxiv():
    with mock.patch.object(fetcher.arxiv, "Client") as client:
        assert fetcher.fetch_papers_by_id([]) == []
    client.assert_not_called()


def test_daily_combines_new_papers_revisions_and_code_changes_in_one_notification(config):
    revision_id, code_id, new_id = "2606.00001", "2606.00002", "2610.00003"
    seed(config, {"IRSTD": {pid: paper(pid) for pid in (revision_id, code_id)}})
    official_code = "https://gitcode.com/example/revised-method"
    found_code = "https://github.com/example/old-method"
    discovery_queries = []

    def results(search):
        if search.id_list:
            assert set(search.id_list) == {revision_id, code_id}
            return [
                result(revision_id, updated=TODAY.isoformat(), title="Revised infrared method",
                       summary=f"Our code is available at {official_code}."),
                result(code_id),
            ]
        discovery_queries.append(search.query)
        return [result(new_id, updated=TODAY.isoformat())]

    with mock.patch.object(fetcher.arxiv.Client, "results", side_effect=results), \
         mock.patch.object(fetcher, "lookup_code_link") as eager_lookup, \
         mock.patch.object(codelink, "lookup_code_link", side_effect=lambda pid, title: found_code if pid == code_id else None) as lookup, \
         mock.patch.object(codelink, "verify_code_link", return_value=True), \
         mock.patch.object(daily_arxiv, "notify_daily_update", return_value=True) as notify:
        daily_arxiv.run(config, notify_wechat=True, today=TODAY)
        notify.assert_called_once()
        assert [p["id"] for p in notify.call_args.args[1]["IRSTD"]] == [new_id]
        updated = {p["id"]: p for p in notify.call_args.args[2]["IRSTD"]}
        assert set(updated) == {revision_id, code_id}
        assert updated[revision_id]["publish_date"] == TODAY.isoformat()
        assert updated[revision_id]["code"] == official_code
        assert updated[revision_id]["code_source"] == "arxiv_metadata"
        assert updated[code_id]["publish_date"] == "2026-06-01"
        assert updated[code_id]["code"] == found_code
        assert updated[code_id]["code_source"] == "github_verified"
        assert sorted(call.args[0] for call in lookup.call_args_list) == [code_id, new_id]
        eager_lookup.assert_not_called()

        notify.reset_mock()
        daily_arxiv.run(config, notify_wechat=True, today=TODAY)
        notify.assert_not_called()

    assert len(discovery_queries) == 2
    assert "submittedDate:[202610040000 TO 202610082359]" in discovery_queries[0]
    assert "submittedDate:[202610050000 TO 202610082359]" in discovery_queries[1]
    data = storage.load_data(config["data_path"])
    state = storage.load_data(config["state_path"])
    assert state["wechat_notification"]["delivered"] == catalog_fingerprints(data)
    assert state["catalog_checksums"]["IRSTD"] == fingerprint(data["IRSTD"])


@pytest.mark.parametrize("full_refresh", [False, True])
def test_history_reuses_discovery_and_shared_ids_across_enabled_topics(config, full_refresh):
    config["enable_code_lookup"] = False
    config["kv"]["Other"] = "all:other"
    config["domain_max_results"].update({"IRSTD": 1, "Other": 1})
    config["domain_start_dates"]["Other"] = "2025-01-01"
    shared_id, discovered_id = "2606.00001", "2606.00002"
    seed(config, {
        "IRSTD": {pid: paper(pid) for pid in (shared_id, discovered_id)},
        "Other": {shared_id: paper(shared_id)},
    })
    history_requests = []

    def results(search):
        if search.id_list:
            history_requests.append(search.id_list)
            return [result(pid, updated=TODAY.isoformat()) for pid in search.id_list]
        if "all:other" in search.query:
            return [result(discovered_id, updated=TODAY.isoformat())]
        return []

    with mock.patch.object(fetcher.arxiv.Client, "results", side_effect=results), \
         mock.patch.object(daily_arxiv, "backfill_code_links") as backfill:
        daily_arxiv.run(config, full_refresh=full_refresh, today=TODAY)
    assert history_requests == [[shared_id]]
    data = storage.load_data(config["data_path"])
    for papers in data.values():
        assert set(papers) == {shared_id, discovered_id}
        assert all(p["publish_date"] == TODAY.isoformat() for p in papers.values())
    backfill.assert_not_called()


def test_daily_keeps_manual_sanet_link_during_metadata_refresh_and_backfill(config):
    pid = "2610.09875"
    code = "https://gitcode.com/m0_61988291/SANet"
    seed(config, {"IRSTD": {pid: paper(pid, code=code, code_source="manual")}})
    with mock.patch.object(fetcher.arxiv.Client, "results", side_effect=lambda search: [
        result(pid, updated=TODAY.isoformat(), summary="Our code: https://github.com/mj129/SANet")
    ] if search.id_list else []), \
         mock.patch.object(codelink, "lookup_code_link") as lookup, \
         mock.patch.object(codelink, "verify_code_link") as verify:
        daily_arxiv.run(config, today=TODAY)
    saved = storage.load_data(config["data_path"])["IRSTD"][pid]
    assert saved["publish_date"] == TODAY.isoformat()
    assert saved["code"] == code
    assert saved["code_source"] == "manual"
    lookup.assert_not_called()
    verify.assert_not_called()


def test_missing_history_result_preserves_cached_paper(config, caplog):
    config["enable_code_lookup"] = False
    present_id, missing_id = "2606.00001", "2606.00002"
    original = {"IRSTD": {pid: paper(pid) for pid in (present_id, missing_id)}}
    seed(config, original)
    with mock.patch.object(fetcher.arxiv.Client, "results", side_effect=lambda search: [
        result(present_id, updated=TODAY.isoformat())
    ] if search.id_list else []):
        daily_arxiv.run(config, today=TODAY)
    saved = storage.load_data(config["data_path"])["IRSTD"]
    assert saved[missing_id] == original["IRSTD"][missing_id]
    assert saved[present_id]["publish_date"] == TODAY.isoformat()
    assert missing_id in caplog.text


def test_daily_history_leaves_disabled_topics_and_their_checkpoints_untouched(config):
    active_id, disabled_id = "2606.00001", "2606.00002"
    original = {
        "IRSTD": {active_id: paper(active_id)},
        "Disabled": {disabled_id: paper(disabled_id)},
    }
    state = seed(config, original)

    def results(search):
        if search.id_list:
            assert search.id_list == [active_id]
            return [result(active_id, updated=TODAY.isoformat())]
        return []

    with mock.patch.object(fetcher.arxiv.Client, "results", side_effect=results), \
         mock.patch.object(codelink, "lookup_code_link", return_value="https://github.com/example/method") as lookup:
        daily_arxiv.run(config, today=TODAY)
    assert [call.args[0] for call in lookup.call_args_list] == [active_id]
    assert storage.load_data(config["data_path"])["Disabled"] == original["Disabled"]
    saved_state = storage.load_data(config["state_path"])
    for key in ("last_successful_update", "query_fingerprints", "catalog_checksums"):
        assert saved_state[key]["Disabled"] == state[key]["Disabled"]


@pytest.mark.parametrize("refresh_setting", [False, None])
def test_disabled_or_omitted_history_setting_preserves_incremental_only_mode(config, refresh_setting):
    if refresh_setting is None:
        config.pop("refresh_history")
    else:
        config["refresh_history"] = refresh_setting
    seed(config, {"IRSTD": {"2606.00001": paper("2606.00001")}})
    with mock.patch.object(daily_arxiv, "fetch_daily_papers", return_value=[]) as fetch, \
         mock.patch.object(daily_arxiv, "fetch_papers_by_id") as history, \
         mock.patch.object(daily_arxiv, "backfill_code_links") as backfill:
        daily_arxiv.run(config, today=TODAY)
    assert fetch.call_args.kwargs["lookup_missing_code"] is True
    history.assert_not_called()
    backfill.assert_not_called()


@pytest.mark.parametrize("failure_stage", ["history", "backfill"])
def test_history_failure_keeps_catalog_checkpoint_and_outputs_for_retry(config, failure_stage):
    old_ids = ["2606.00001", "2606.00002"]
    seed(config, {"IRSTD": {pid: paper(pid) for pid in old_ids}})
    config["publish_readme"] = True
    Path(config["md_readme_path"]).write_text("Previous output", encoding="utf-8")
    paths = [Path(config[key]) for key in ("data_path", "state_path", "md_readme_path")]
    before = {path: path.read_bytes() for path in paths}

    def results(search):
        if search.id_list:
            if failure_stage == "history":
                raise RuntimeError("arXiv unavailable")
            return [result(pid, updated=TODAY.isoformat()) for pid in search.id_list]
        return [result("2610.00003", updated=TODAY.isoformat())]

    failure = RuntimeError if failure_stage == "history" else codelink.CodeLookupError
    with mock.patch.object(fetcher.arxiv.Client, "results", side_effect=results), \
         mock.patch.object(codelink, "lookup_code_link", side_effect=[
             "https://github.com/example/method", codelink.CodeLookupError("GitHub unavailable"),
         ]), \
         mock.patch.object(daily_arxiv, "notify_daily_update") as notify:
        with pytest.raises(failure):
            daily_arxiv.run(config, notify_wechat=True, today=TODAY)
    assert {path: path.read_bytes() for path in paths} == before
    notify.assert_not_called()
