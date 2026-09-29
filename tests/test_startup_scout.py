"""후보 탐색 / 적격성 검증 테스트 (필수 테스트 9)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from conftest import run_graph
from investment_scout.agents.startup_scout import (
    StartupScout,
    dedupe_profiles,
    normalize_name,
    registrable_domain,
)
from investment_scout.eligibility import (
    ELIGIBLE,
    EXIT_ACQUIRED,
    EXIT_NONE_CONFIRMED,
    EXIT_NOT_FOUND,
    INELIGIBLE,
    NEEDS_VERIFICATION,
    judge_eligibility,
)
from investment_scout.mocks.fixtures import mock_profile
from investment_scout.search import MockSearchProvider

VERIFIED_PATH = Path(__file__).resolve().parents[1] / "data" / "candidates_verified.json"


def test_normalize_name_and_domain():
    assert normalize_name("FuriosaAI Inc.") == normalize_name("furiosaai")
    assert registrable_domain("https://www.Rebellions.ai/about") == "rebellions.ai"
    assert registrable_domain(None) is None


# 9. 중복 후보는 실제 평가 목록에서 제외
def test_duplicates_removed_by_name_and_domain():
    profiles = [
        mock_profile("기업A", website="https://a.mock.invalid"),
        mock_profile("기업A", website="https://other.mock.invalid"),        # 이름 중복
        mock_profile("기업B", website="https://a.mock.invalid"),            # 도메인 중복
        mock_profile("기업C", website="https://c.mock.invalid"),
    ]
    unique, duplicates = dedupe_profiles(profiles)

    assert [p["name"] for p in unique] == ["[TEST]기업A", "[TEST]기업C"]
    assert {d["matched_by"] for d in duplicates} == {"name", "domain"}


def test_alias_is_used_for_dedup():
    # 별칭(한글명)으로도 같은 기업을 잡아냅니다.
    profiles = [
        {"name": "Rebellions", "aliases": ["리벨리온"], "website": "https://r1.mock.invalid"},
        {"name": "리벨리온", "aliases": [], "website": "https://r2.mock.invalid"},
    ]
    unique, duplicates = dedupe_profiles(profiles)
    assert [p["name"] for p in unique] == ["Rebellions"]
    assert duplicates[0]["matched_by"] == "name"


# 9. 적격성 미확인 후보는 실제 평가 목록에서 제외되지만 사유는 보존
def test_needs_verification_candidates_excluded_but_preserved():
    profiles = [
        mock_profile("적격기업"),
        mock_profile("미확인기업", exit_status=EXIT_NOT_FOUND),
        mock_profile("상장기업", is_public=True),
    ]
    final = run_graph(profiles=profiles, max_candidates=20)

    assert final["candidate_startups"] == ["[TEST]적격기업"]

    scout = final["scout_result"]
    assert [x["name"] for x in scout["needs_verification"]] == ["[TEST]미확인기업"]
    assert [x["name"] for x in scout["ineligible"]] == ["[TEST]상장기업"]
    assert scout["ineligible"][0]["reasons"]
    assert scout["needs_verification"][0]["unverified_fields"]

    # 평가 목록에서 빠진 사실이 진단으로도 남습니다.
    excluded = [d for d in final["diagnostics"] if d["kind"] == "CANDIDATE_NEEDS_VERIFICATION"]
    assert [d["startup"] for d in excluded] == ["[TEST]미확인기업"]


def test_duplicate_is_not_evaluated_twice():
    profiles = [
        mock_profile("중복기업", website="https://dup.mock.invalid"),
        mock_profile("중복기업", website="https://dup.mock.invalid"),
    ]
    final = run_graph(profiles=profiles, max_candidates=20)

    assert final["candidate_startups"] == ["[TEST]중복기업"]
    assert len(final["evaluation_history"]) == 1
    assert final["scout_result"]["duplicates_removed"]


# --- 적격성 판정 규칙 ---
def test_eligible_when_all_conditions_confirmed():
    verdict = judge_eligibility(mock_profile("정상기업"))
    assert verdict["verdict"] == ELIGIBLE
    assert verdict["unverified_fields"] == []
    assert verdict["criteria_used"]["allowed_funding_stages"]


@pytest.mark.parametrize(
    "kwargs, expected",
    [
        ({"is_public": True}, INELIGIBLE),
        ({"exit_status": EXIT_ACQUIRED}, INELIGIBLE),
        ({"is_ai_core": False}, INELIGIBLE),
        ({"funding_stage": "SERIES_D"}, INELIGIBLE),
    ],
)
def test_ineligible_conditions(kwargs, expected):
    verdict = judge_eligibility(mock_profile("기업", **kwargs))
    assert verdict["verdict"] == expected
    assert verdict["reasons"]


def test_exit_not_found_is_not_treated_as_no_exit():
    """인수 기사를 못 찾은 것과 Exit 없음을 확정한 것은 다릅니다."""
    verdict = judge_eligibility(mock_profile("기업", exit_status=EXIT_NOT_FOUND))
    assert verdict["verdict"] == NEEDS_VERIFICATION
    assert "exit_status" in verdict["unverified_fields"]
    assert any("확정할 수 없음" in r for r in verdict["reasons"])


def test_stale_funding_stage_requires_reverification():
    """과거 투자 기사만으로 현재 단계를 확정하지 않습니다."""
    verdict = judge_eligibility(
        mock_profile("기업", stage_confirmed_days_ago=800), stage_freshness_days=365
    )
    assert verdict["verdict"] == NEEDS_VERIFICATION
    assert "funding_stage_confirmed_at" in verdict["unverified_fields"]


def test_missing_field_becomes_needs_verification():
    profile = mock_profile("기업")
    profile["funding_stage"] = None
    verdict = judge_eligibility(profile)
    assert verdict["verdict"] == NEEDS_VERIFICATION
    assert "funding_stage" in verdict["unverified_fields"]


def test_scout_on_verified_data_secures_twenty_evidence_backed_candidates():
    scout = StartupScout(search_provider=MockSearchProvider())
    result = scout.run("Semiconductor")

    assert len(result["candidate_startups"]) == 20
    assert len(result["source_evidence"]) == 20
    assert result["scout_result"]["verification_performed"] is True

    counts = result["scout_result"]["counts"]
    assert counts["KR"]["target"] == 10
    assert counts["KR"]["secured"] == 10
    assert counts["KR"]["shortfall"] == 0
    assert counts["OVERSEAS"]["secured"] == 10
    assert counts["OVERSEAS"]["shortfall"] == 0


def test_verified_candidate_file_has_real_sources_and_required_fields():
    doc = json.loads(VERIFIED_PATH.read_text(encoding="utf-8"))
    assert doc["data_origin"] == "VERIFIED_WEB_RESEARCH"
    assert len(doc["companies"]) == 20
    assert sum(c["region"] == "KR" for c in doc["companies"]) == 10
    assert sum(c["region"] == "OVERSEAS" for c in doc["companies"]) == 10

    all_source_ids = []
    for company in doc["companies"]:
        assert company["website"].startswith("https://")
        assert company["website_verified"] is True
        assert company["founded_year"]
        assert company["main_products"]
        assert company["funding_stage"]
        assert company["is_public"] is False
        assert company["exit_status"] == EXIT_NONE_CONFIRMED
        assert company["is_ai_core"] is True
        assert len(company["sources"]) >= 2
        assert set(company["source_ids"]) == {
            source["source_id"] for source in company["sources"]
        }
        for source in company["sources"]:
            assert source["url"].startswith("https://")
            assert source["evidence"]
            assert source["supports"]
            assert source["url_fetched"] is True
            assert source["is_mock"] is False
            all_source_ids.append(source["source_id"])

    assert len(all_source_ids) == len(set(all_source_ids))
