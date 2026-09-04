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


def test_date_is_available() -> None:
    assert date.today().isoformat()


if __name__ == "__main__":
    tests = [
        test_build_query,
        test_config,
        test_merge_and_storage,
        test_render_markdown,
        test_wechat_render,
        test_lookup_and_verify_code_link,
        test_extract_code_link_from_arxiv_metadata,
        test_fetcher_reuses_cached_code_when_lookup_is_disabled,
        test_fetcher_prefers_official_arxiv_code_link,
        test_run_renders_all_enabled_outputs,
        test_incremental_and_full_refresh_windows,
        test_date_is_available,
    ]
    for test in tests:
        test()
        print(f"PASS {test.__name__}")
    print("所有离线冒烟测试通过。")
