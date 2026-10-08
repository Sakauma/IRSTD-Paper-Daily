"""Repository evidence must identify the implemented paper, not a cited baseline."""
from copy import deepcopy
from pathlib import Path
from unittest import mock

import pytest

import daily_arxiv
from arxiv_daily import codelink, storage


PAPER_ID = "2606.00001"
PAPER_TITLE = "Example Baseline for Infrared Small Target Detection"
PAPER_LINK = f"[{PAPER_TITLE}](https://arxiv.org/abs/{PAPER_ID})"
REPO_URL = "https://github.com/example/target-method"


@pytest.mark.parametrize("readme", [
    f"Official implementation of {PAPER_LINK}.",
    f"This repository contains the official PyTorch implementation of our paper\n{PAPER_LINK}.",
    f"Code for the paper: **{PAPER_TITLE}** (CVPR 2026).",
    f"Official implementation. Paper: arXiv:{PAPER_ID}v2.",
    f"# {PAPER_TITLE}\n\nThis is the official implementation of our paper.",
    f"# {PAPER_LINK}\n\nOfficial PyTorch implementation.",
    f"{PAPER_LINK}\n\nThis repository provides the official code.",
    f"Paper: {PAPER_LINK}. This is the official implementation.",
    f"[Official implementation of {PAPER_TITLE}]({REPO_URL})",
    f"Official implementation of {PAPER_LINK}. We compare with other baselines below.\n\n## References\nOther papers.",
    f"# Project\n\n## References\nOther papers.\n\n## Implementation\nOfficial implementation of {PAPER_LINK}.",
])
def test_verifier_accepts_direct_implementation_evidence(readme):
    with mock.patch.object(codelink, "_fetch_readme", return_value=readme):
        assert codelink.verify_code_link(PAPER_ID, PAPER_TITLE, REPO_URL)


@pytest.mark.parametrize("readme", [
    f"# Another Method\nOfficial implementation of another paper.\n\n## Acknowledgements\nWe use {PAPER_LINK} as a baseline.",
    f"# Another Method\nOfficial implementation of another paper.\n\n{PAPER_LINK}",
    f"Official implementation of Another Method. We compare against {PAPER_LINK}.",
    f"Official implementation of Another Method; baseline: {PAPER_LINK}.",
    f"Official implementation of Another Method\nPaper: {PAPER_LINK}",
    f"This repository uses the official implementation of {PAPER_LINK} as a baseline.",
    f"We build on the official implementation of {PAPER_LINK}.",
    f"This is not the official implementation of {PAPER_LINK}.",
    f"# {PAPER_LINK}\n\nOfficial implementation of Another Method.",
    f"# {PAPER_LINK}\n\n## Another Method\nOfficial implementation.",
    f"# Project\n\n## Acknowledgments\nOfficial implementation of {PAPER_LINK}.",
    f"# Project\n\n## Baselines\nOfficial implementation of {PAPER_LINK}.",
    f"# Project\n\n## References\n### Referenced method\nOfficial implementation of {PAPER_LINK}.",
    f"# Project\n\n```text\nOfficial implementation of {PAPER_LINK}.\n```",
    f"# Project\n\n<!-- Official implementation of {PAPER_LINK}. -->",
    f"# Project\n\n## 3. Acknowledgements\nOfficial implementation of {PAPER_LINK}.",
    f"Acknowledgements\n----------------\n\nOfficial implementation of {PAPER_LINK}.",
    f"<h2>Acknowledgements</h2>\nOfficial implementation of {PAPER_LINK}.",
    f"# Project\n\n    Official implementation of {PAPER_LINK}.",
    f"# Project\n\n> Official implementation of {PAPER_LINK}.",
    f"# Project\n\n- A useful baseline:\n  Official implementation of {PAPER_LINK}.",
    f"# Project\n\n- A useful baseline:\nOfficial implementation of {PAPER_LINK}.",
    f"[Official implementation of {PAPER_TITLE}](https://github.com/another/method)",
    f"Official implementation of [Another Method](https://github.com/another/method). Paper: {PAPER_LINK}.",
])
def test_verifier_rejects_citations_and_unrelated_implementation_claims(readme):
    with mock.patch.object(codelink, "_fetch_readme", return_value=readme):
        assert not codelink.verify_code_link(PAPER_ID, PAPER_TITLE, REPO_URL)


def response(*, incomplete, items):
    result = mock.Mock(status_code=200)
    result.json.return_value = {"incomplete_results": incomplete, "items": items}
    return result


@pytest.mark.parametrize("partial_items", [[], [{"html_url": "https://github.com/example/partial"}]])
def test_incomplete_search_retries_same_query_before_using_results(partial_items):
    partial = response(incomplete=True, items=partial_items)
    complete = response(incomplete=False, items=[{"html_url": REPO_URL}])
    with mock.patch.object(codelink, "_github_get", side_effect=[partial, complete]) as get, \
         mock.patch.object(codelink.time, "sleep") as sleep:
        assert codelink._search_repositories(f'"{PAPER_ID}" in:readme') == [REPO_URL]
    assert get.call_count == 2
    assert get.call_args_list[0] == get.call_args_list[1]
    sleep.assert_called_once()


def test_complete_empty_search_is_a_reliable_no_match():
    with mock.patch.object(codelink, "_github_get", return_value=response(incomplete=False, items=[])) as get:
        assert codelink._search_repositories(f'"{PAPER_ID}" in:readme') == []
    get.assert_called_once()


@pytest.mark.parametrize("partial_items", [[], [{"html_url": REPO_URL}]])
def test_exhausted_incomplete_search_preserves_the_previous_link(partial_items):
    data = {"IRSTD": {PAPER_ID: {
        "id": PAPER_ID, "title": PAPER_TITLE, "code": REPO_URL, "code_source": "github_verified",
    }}}
    original = deepcopy(data)
    with mock.patch.object(codelink, "_github_get", return_value=response(incomplete=True, items=partial_items)) as get, \
         mock.patch.object(codelink, "verify_code_link", return_value=False), \
         mock.patch.object(codelink.time, "sleep") as sleep:
        with pytest.raises(codelink.CodeLookupError, match="不完整"):
            codelink.backfill_code_links(data)
    assert get.call_count == 3
    assert all(call == get.call_args_list[0] for call in get.call_args_list)
    assert sleep.call_count == 2
    assert data == original


@pytest.mark.parametrize("mode", ["run", "run_backfill"])
def test_incomplete_search_does_not_persist_partial_backfill_or_advance_state(tmp_path, mode):
    config = {
        "data_path": str(tmp_path / "papers.json"), "state_path": str(tmp_path / "state.json"),
        "md_readme_path": str(tmp_path / "README.md"),
        "publish_readme": True, "publish_gitpage": False, "publish_wechat": False,
        "refresh_history": True, "enable_code_lookup": True,
        "kv": {"IRSTD": "all:IRSTD"}, "domain_max_results": {"IRSTD": None},
        "domain_start_dates": {"IRSTD": "2025-01-01"},
    }
    first_id = "2605.00001"
    data = {"IRSTD": {
        first_id: {"id": first_id, "title": "Another infrared method", "code": None},
        PAPER_ID: {"id": PAPER_ID, "title": PAPER_TITLE, "code": REPO_URL},
    }}
    storage.save_data(config["data_path"], data)
    storage.save_data(config["state_path"], {"last_successful_update": {"IRSTD": "2026-10-07"}})
    Path(config["md_readme_path"]).write_text("Previous output", encoding="utf-8")
    paths = [Path(config[key]) for key in ("data_path", "state_path", "md_readme_path")]
    original = {path: path.read_bytes() for path in paths}

    def get(url, **kwargs):
        if first_id in kwargs["params"]["q"]:
            return response(incomplete=False, items=[{"html_url": "https://github.com/example/new-code"}])
        return response(incomplete=True, items=[])

    with mock.patch.object(daily_arxiv, "fetch_daily_papers", return_value=[]), \
         mock.patch.object(daily_arxiv, "fetch_papers_by_id", return_value=[]), \
         mock.patch.object(codelink, "_github_get", side_effect=get), \
         mock.patch.object(codelink, "verify_code_link", side_effect=lambda pid, title, url: pid == first_id), \
         mock.patch.object(codelink.time, "sleep"), \
         mock.patch.object(daily_arxiv, "notify_daily_update") as notify:
        with pytest.raises(codelink.CodeLookupError, match="不完整"):
            getattr(daily_arxiv, mode)(config, notify_wechat=True)
    assert {path: path.read_bytes() for path in paths} == original
    notify.assert_not_called()
