"""离线冒烟测试：不依赖 arXiv/GitHub 网络服务。"""

from __future__ import annotations

import os
import sys
import tempfile
from datetime import datetime
from datetime import date
from unittest import mock

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

from arxiv_daily.config import build_query, load_config  # noqa: E402
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


def test_config() -> None:
    config = load_config(os.path.join(PROJECT_ROOT, "config.yaml"))
    assert list(config["kv"].keys()) == ["IRSTD"]
    assert "Infrared Small Target Detection" in config["kv"]["IRSTD"]
    assert config["domain_max_results"]["IRSTD"] == 10
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

    fake_search_response = mock.Mock()
    fake_search_response.status_code = 200
    fake_search_response.headers = {}
    fake_search_response.json.return_value = {
        "items": [{"html_url": "https://github.com/foo/bar"}]
    }
    with mock.patch.object(
        codelink.requests, "get", return_value=fake_search_response
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


def test_fetcher_reuses_cached_code_when_lookup_is_disabled() -> None:
    from arxiv_daily import fetcher

    result = mock.Mock()
    result.get_short_id.return_value = "arXiv:2608.07015v2"
    result.title = "Demo Paper"
    result.authors = ["Alice", "Bob"]
    result.updated = datetime(2026, 8, 7)
    result.published = datetime(2026, 8, 6)

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


def test_run_renders_all_enabled_outputs() -> None:
    import daily_arxiv

    with tempfile.TemporaryDirectory() as tmp_dir:
        config = {
            "user_name": "Sakauma",
            "repo_name": "IRSTD-Paper-Daily",
            "data_path": os.path.join(tmp_dir, "data.json"),
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
            "domain_max_results": {"IRSTD": 1},
        }
        with mock.patch.object(
            daily_arxiv,
            "fetch_daily_papers",
            return_value=[sample_paper("https://github.com/foo/bar")],
        ):
            daily_arxiv.run(config)

        assert "Demo Paper" in open(config["md_readme_path"], encoding="utf-8").read()
        assert "Demo Paper" in open(
            config["md_gitpage_path"], encoding="utf-8"
        ).read()
        assert "Paper:" in open(config["md_wechat_path"], encoding="utf-8").read()
        assert os.path.exists(config["wechat_data_path"])


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
        test_fetcher_reuses_cached_code_when_lookup_is_disabled,
        test_run_renders_all_enabled_outputs,
        test_date_is_available,
    ]
    for test in tests:
        test()
        print(f"PASS {test.__name__}")
    print("所有离线冒烟测试通过。")
