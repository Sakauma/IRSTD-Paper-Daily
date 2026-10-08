"""Regression coverage for author links outside GitHub and same-name papers."""
import json
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import pytest

from arxiv_daily import codelink, fetcher
from arxiv_daily.wechat import build_wechat_data


SANET_ID = "2610.09875"
SANET_TITLE = "SANet: Selective Attention Network for Infrared Small Target Detection"
SANET_CODE = "https://gitcode.com/m0_61988291/SANet"
SANET_ANONYMOUS = "https://anonymous.4open.science/r/SANetE808/"
WRONG_SANET_CODE = "https://github.com/mj129/SANet"
SANET_STATEMENT = (
    "These results support the\n"
    "effectiveness of SANet in dim-target perception, discriminative\n"
    "feature representation, and background suppression. The source\n"
    f"code is available at {SANET_ANONYMOUS} and {SANET_CODE}"
)


@pytest.mark.parametrize("url", [
    SANET_CODE, SANET_ANONYMOUS,
    "https://github.com/example/project",
    "https://gitlab.com/example/project",
    "https://gitlab.com/example/research/project",
    "https://gitee.com/example/project",
])
def test_extracts_explicit_author_links_on_supported_hosts(url):
    assert codelink.extract_code_link(f"Our source code is available at {url}.") == url


@pytest.mark.parametrize("statement", [
    SANET_STATEMENT,
    SANET_STATEMENT.replace("https://", "https\\://"),
    f"The source code is available at {SANET_CODE} and {SANET_ANONYMOUS}.",
    f"Code: {SANET_ANONYMOUS}, {SANET_CODE}.",
    f"Code: {SANET_ANONYMOUS} (mirror: {SANET_CODE}).",
])
def test_shared_code_declaration_prefers_public_repo_over_anonymous_mirror(statement):
    assert codelink.extract_code_link(statement) == SANET_CODE


@pytest.mark.parametrize("statement", [
    f"Our code is available at\n{SANET_CODE}.",
    f"Our source\ncode is available at ({SANET_CODE}).",
    f"Code: http://gitcode.com/m0_61988291/SANet.git/",
])
def test_author_links_survive_line_wrapping_and_punctuation(statement):
    assert codelink.extract_code_link(statement) == SANET_CODE


@pytest.mark.parametrize("statement", [
    f"We compare against the code at {SANET_CODE}.",
    f"A baseline implementation is available at {SANET_ANONYMOUS}.",
    f"Our code: {SANET_CODE}. Our code: https://github.com/another/project.",
    "Our code is available at https://gitcode.com.example.org/user/project.",
])
def test_non_github_links_still_require_unambiguous_author_evidence(statement):
    assert codelink.extract_code_link(statement) is None


def test_baseline_after_author_link_is_not_treated_as_a_mirror():
    text = f"Our code: {SANET_ANONYMOUS}, baseline code: {WRONG_SANET_CODE}."
    assert codelink.extract_code_link(text) == SANET_ANONYMOUS


@pytest.mark.parametrize("cached", [False, True])
def test_fetcher_uses_non_github_author_link_before_cache_or_search(cached):
    result = SimpleNamespace(
        get_short_id=lambda: f"{SANET_ID}v1", title=SANET_TITLE,
        summary=SANET_STATEMENT, comment=None, authors=["Yingmei Zhang"],
        published=datetime(2026, 10, 7), updated=datetime(2026, 10, 7),
    )
    with mock.patch.object(fetcher.arxiv.Client, "results", return_value=[result]), \
         mock.patch.object(fetcher, "lookup_code_link") as lookup:
        records = fetcher.fetch_daily_papers(
            "IRSTD", "all:IRSTD", 1,
            known_codes={SANET_ID: WRONG_SANET_CODE} if cached else None,
            known_paper_ids={SANET_ID} if cached else None,
        )
    lookup.assert_not_called()
    assert records[0]["code"] == SANET_CODE
    assert records[0]["code_source"] == "arxiv_metadata"


def test_manually_confirmed_paper_link_survives_missing_api_link_and_backfill():
    # The real SANet API abstract omits the source-code sentence in the paper.
    result = SimpleNamespace(
        get_short_id=lambda: f"{SANET_ID}v1", title=SANET_TITLE,
        summary=SANET_STATEMENT.split(" The source", 1)[0], comment=None,
        authors=["Yingmei Zhang"],
        published=datetime(2026, 10, 7), updated=datetime(2026, 10, 7),
    )
    with mock.patch.object(fetcher.arxiv.Client, "results", return_value=[result]), \
         mock.patch.object(fetcher, "lookup_code_link") as fetch_lookup, \
         mock.patch.object(codelink, "lookup_code_link") as backfill_lookup, \
         mock.patch.object(codelink, "verify_code_link") as verify:
        records = fetcher.fetch_daily_papers(
            "IRSTD", "all:IRSTD", 1, known_codes={SANET_ID: SANET_CODE},
            known_paper_ids={SANET_ID}, known_code_sources={SANET_ID: "manual"},
        )
        data, changed = codelink.backfill_code_links({"IRSTD": {SANET_ID: records[0]}})
    fetch_lookup.assert_not_called()
    backfill_lookup.assert_not_called()
    verify.assert_not_called()
    assert changed == 0
    assert data["IRSTD"][SANET_ID]["code"] == SANET_CODE
    assert data["IRSTD"][SANET_ID]["code_source"] == "manual"


def test_github_search_rejects_sanet_for_a_different_paper():
    readme = (
        "## SANet: A Slice-Aware Network for Pulmonary Nodule Detection\n"
        "This paper has been accepted and early accessed in IEEE TPAMI 2021.\n"
        "This code and our data are licensed for non-commercial research purpose only."
    )
    with mock.patch.object(codelink, "_fetch_readme", return_value=readme), \
         mock.patch.object(codelink, "_search_repositories", return_value=[WRONG_SANET_CODE]), \
         mock.patch.object(codelink.time, "sleep"):
        assert codelink.lookup_code_link(SANET_ID, SANET_TITLE) is None


def test_sanet_catalog_and_generated_outputs_use_confirmed_link():
    repo = Path(__file__).resolve().parents[1]
    data = json.loads((repo / "docs/irstd-paper-daily.json").read_text(encoding="utf-8"))
    assert data["IRSTD"][SANET_ID]["code"] == SANET_CODE
    assert data["IRSTD"][SANET_ID]["code_source"] == "manual"
    assert build_wechat_data(data) == json.loads(
        (repo / "docs/irstd-paper-daily-wechat.json").read_text(encoding="utf-8")
    )
    for filename in ("README.md", "docs/index.md", "docs/wechat.md"):
        content = (repo / filename).read_text(encoding="utf-8")
        assert SANET_CODE in content
        assert WRONG_SANET_CODE not in content
