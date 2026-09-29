"""검색 인터페이스 테스트 (필수 테스트 15)."""

from __future__ import annotations

import pytest

from investment_scout.agents.startup_scout import StartupScout, extract_signals
from investment_scout.mocks.fixtures import mock_profile
from investment_scout.search import (
    MockSearchProvider,
    SearchConfigurationError,
    SearchFailure,
    get_search_provider,
)
from investment_scout.search.base import MODE_LIVE, MODE_MOCK
from investment_scout.search.mock import mock_hit
from investment_scout.search.tavily import TavilySearchProvider


def test_mock_provider_needs_no_api_key(monkeypatch):
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    provider = get_search_provider(MODE_MOCK)
    assert provider.mode == MODE_MOCK
    assert provider.search("아무 질의") == []  # 0건은 정상 응답


def test_live_mode_without_api_key_raises_configuration_error(monkeypatch):
    """설정 오류를 mock 으로 대체하지 않고 명확히 실패합니다."""
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    with pytest.raises(SearchConfigurationError, match="TAVILY_API_KEY"):
        get_search_provider(MODE_LIVE)

    with pytest.raises(SearchConfigurationError):
        TavilySearchProvider()


def test_unknown_mode_raises_configuration_error():
    with pytest.raises(SearchConfigurationError, match="알 수 없는 검색 모드"):
        get_search_provider("semi-live")


def test_default_mode_is_mock(monkeypatch):
    monkeypatch.delenv("SEARCH_MODE", raising=False)
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    assert get_search_provider().mode == MODE_MOCK


# 15. 검색 장애는 정보 부족과 구분되는 오류
def test_search_failure_is_distinct_from_empty_results():
    provider = MockSearchProvider(fail_queries=["장애기업"])

    assert provider.search("정상기업 funding") == []  # 자료 부족 (정상 응답)
    with pytest.raises(SearchFailure):
        provider.search("장애기업 funding")


def test_scout_records_search_failure_as_system_error():
    """검색 장애는 DATA_GAP 이 아니라 SYSTEM_ERROR 로 기록됩니다."""
    provider = MockSearchProvider(fail_queries=["장애기업"])
    provider.mode = MODE_LIVE  # live 경로를 타도록 강제 (실제 네트워크 호출 없음)

    scout = StartupScout(
        search_provider=provider,
        seed_profiles=[mock_profile("장애기업", with_source=False)],
    )
    result = scout.run("Semiconductor")

    failures = [d for d in result["diagnostics"] if d["kind"] == "SEARCH_FAILURE"]
    assert failures
    assert failures[0]["category"] == "SYSTEM_ERROR"

    gaps = [d for d in result["diagnostics"] if d["category"] == "DATA_GAP"]
    assert gaps  # 자료 부족은 별도 항목으로 남습니다.


def test_configuration_error_is_not_swallowed_during_scout():
    """설정 오류는 진단으로 삼키지 않고 위로 전파합니다."""

    class BrokenProvider:
        mode = MODE_LIVE

        def search(self, query, *, max_results=5):
            raise SearchConfigurationError("키 없음")

    scout = StartupScout(
        search_provider=BrokenProvider(),
        seed_profiles=[mock_profile("기업", with_source=False)],
    )
    with pytest.raises(SearchConfigurationError):
        scout.run("Semiconductor")


def test_acquisition_signal_extracted():
    hits = [mock_hit(path="news/1", title="Company acquired by BigCorp", snippet="...")]
    signals = extract_signals(hits)
    assert any(s["topic"] == "exit_status" for s in signals)


def test_no_acquisition_article_does_not_confirm_no_exit():
    """인수 기사를 못 찾았다는 사실만으로 Exit 없음으로 확정하지 않습니다."""
    provider = MockSearchProvider(
        fixtures={"기업": [mock_hit(path="n/1", title="기업 소개", snippet="AI 반도체 기업")]}
    )
    provider.mode = MODE_LIVE

    scout = StartupScout(
        search_provider=provider,
        seed_profiles=[mock_profile("기업", with_source=False)],
    )
    result = scout.run("Semiconductor")
    profile = result["candidate_profiles"]["[TEST]기업"]

    assert profile["exit_status"] == "NOT_FOUND"
    assert result["candidate_startups"] == []  # 확정 못 했으므로 평가 목록에서 제외


# ---------------------------------------------------------------------------
# 검색 신호 오탐 방지 (live 실행에서 실제로 발생했던 오판정)
# ---------------------------------------------------------------------------
def test_signal_ignored_when_article_is_about_another_company():
    """질의에 'IPO' 가 들어가 딸려 온 '다른 기업 기사'를 그 기업의 신호로 잡지 않습니다."""
    hits = [
        mock_hit(
            path="news/other",
            title="Inside Korea's Four Strategic Sectors",
            snippet="DEEPX is in mass production. Rebellions plans an IPO next year.",
        )
    ]
    signals = extract_signals(hits, company_names=["Panmnesia", "파네시아"])

    assert signals == []


def test_signal_kept_when_article_mentions_the_company():
    hits = [
        mock_hit(
            path="news/own",
            title="Panmnesia acquired by BigCorp",
            snippet="The CXL startup Panmnesia was acquired by BigCorp.",
        )
    ]
    signals = extract_signals(hits, company_names=["Panmnesia"])

    assert any(s["topic"] == "exit_status" for s in signals)


def test_search_exit_signal_downgrades_to_needs_verification_not_ineligible():
    """키워드 일치만으로 부적격을 확정하지 않습니다 (사람이 1차 자료로 확인해야 함)."""
    provider = MockSearchProvider(
        fixtures={
            "기업": [
                mock_hit(
                    path="n/1",
                    title="[TEST]기업 acquired by BigCorp",
                    snippet="[TEST]기업 was acquired by BigCorp, reports say.",
                )
            ]
        }
    )
    provider.mode = MODE_LIVE

    scout = StartupScout(
        search_provider=provider,
        seed_profiles=[mock_profile("기업", with_source=False)],
    )
    result = scout.run("Semiconductor")
    profile = result["candidate_profiles"]["[TEST]기업"]

    assert profile["exit_status"] == "SIGNAL_FOUND"
    # 상장 여부는 검색 신호로 바꾸지 않습니다.
    assert profile["is_public"] is False
    assert profile["eligibility"]["verdict"] == "NEEDS_VERIFICATION"
    assert [c["name"] for c in result["scout_result"]["ineligible"]] == []
