"""실제 LangGraph 실행 통합 테스트 (필수 테스트 1~11, 14).

네트워크와 API 키 없이 동작합니다.
"""

from __future__ import annotations

import pytest

from conftest import names_of, run_graph
from investment_scout.graph import UnknownDecisionError
from investment_scout.mocks.fixtures import mock_seed_profiles
from investment_scout.nodes import CandidateIndexError
from investment_scout.state import (
    TERMINATION_ALL_HOLD,
    TERMINATION_LIMIT_REACHED,
    TERMINATION_NO_ELIGIBLE_CANDIDATES,
    TERMINATION_RECOMMENDED_FOUND,
    TERMINATION_ZERO_LIMIT,
)


# 1. 후보 1개 RECOMMENDED: 이력 1개 저장 후 종료
def test_single_candidate_recommended_stops_after_one_record():
    name = names_of(1)[0]
    final = run_graph(candidates=1, decisions={name: "RECOMMENDED"})

    assert final["evaluated_startups"] == [name]
    assert len(final["evaluation_history"]) == 1
    assert final["evaluation_history"][0]["investment_decision"] == "RECOMMENDED"
    assert final["candidate_index"] == 1
    assert final["termination_reason"] == TERMINATION_RECOMMENDED_FOUND
    assert final["final_report"]


# 2. 후보 2개 모두 HOLD: 순서대로 한 번씩 평가 후 종료
def test_two_candidates_all_hold_evaluated_in_order_once_each():
    expected = names_of(2)
    final = run_graph(candidates=2)  # 기본 결정은 HOLD

    assert final["evaluated_startups"] == expected
    assert [r["startup"] for r in final["evaluation_history"]] == expected
    assert len(set(final["evaluated_startups"])) == 2  # 중복 평가 없음
    assert final["candidate_index"] == 2
    assert final["termination_reason"] == TERMINATION_ALL_HOLD


# 3. HOLD → RECOMMENDED → 다음 후보: 세 번째 후보 미평가
def test_hold_then_recommended_skips_remaining_candidate():
    a, b, c = names_of(3)
    final = run_graph(candidates=3, decisions={b: "RECOMMENDED"})

    assert final["evaluated_startups"] == [a, b]
    assert c not in final["evaluated_startups"]
    assert len(final["evaluation_history"]) == 2
    assert final["candidate_index"] == 2
    assert final["termination_reason"] == TERMINATION_RECOMMENDED_FOUND


# 4. 빈 후보 목록: 평가 없이 보고서 생성
def test_empty_candidate_list_generates_report_without_evaluation():
    final = run_graph(profiles=[])

    assert final["candidate_startups"] == []
    assert final["evaluation_history"] == []
    assert final["evaluated_startups"] == []
    assert final["candidate_index"] == 0
    assert final["termination_reason"] == TERMINATION_NO_ELIGIBLE_CANDIDATES
    assert "평가할 적격(ELIGIBLE) 후보가 없어" in final["final_report"]


# 5. 후보 1개 HOLD: 범위 오류 없이 종료
def test_single_candidate_hold_terminates_without_index_error():
    final = run_graph(candidates=1)  # HOLD

    assert len(final["evaluation_history"]) == 1
    assert final["candidate_index"] == 1  # 평가 한도와 같아지는 정상 종료 상태
    assert final["termination_reason"] == TERMINATION_ALL_HOLD


# 6. 최대 평가 수가 후보 수보다 작거나 큰 경우
def test_max_candidates_smaller_than_candidate_count():
    final = run_graph(candidates=5, max_candidates=2)

    assert len(final["evaluation_history"]) == 2
    assert final["candidate_index"] == 2
    # 미평가 후보가 남았으므로 LIMIT_REACHED
    assert final["termination_reason"] == TERMINATION_LIMIT_REACHED
    assert "미검토 기업 수: 3" in final["final_report"]


def test_max_candidates_larger_than_candidate_count():
    final = run_graph(candidates=3, max_candidates=20)

    assert len(final["evaluation_history"]) == 3
    assert final["candidate_index"] == 3
    assert final["termination_reason"] == TERMINATION_ALL_HOLD


# 7. max_candidates=0: 평가 없이 종료
def test_zero_max_candidates_skips_evaluation():
    final = run_graph(candidates=3, max_candidates=0)

    assert final["evaluation_history"] == []
    assert final["candidate_index"] == 0
    assert final["termination_reason"] == TERMINATION_ZERO_LIMIT
    assert "max_candidates=0 이라 평가를 수행하지 않았습니다" in final["final_report"]


# 8. 다음 후보 선택: 이전 분석값 초기화 및 이력 보존
def test_next_candidate_resets_analysis_but_preserves_history():
    a, b = names_of(2)
    final = run_graph(candidates=2)

    history = final["evaluation_history"]
    assert [r["startup"] for r in history] == [a, b]

    # 이력은 서로 독립적인 객체여야 합니다 (깊은 복사).
    assert history[0]["tech_summary"] is not history[1]["tech_summary"]
    assert history[0]["profile"]["name"] == a
    assert history[1]["profile"]["name"] == b

    # 두 번째 기업 평가 시 첫 기업의 값이 남아 있지 않아야 합니다.
    assert history[1]["tech_summary"]["claims"][0]["text"].find(b) != -1

    # 이력을 바꿔도 다른 기록에 영향이 없어야 합니다.
    history[0]["tech_summary"]["data"]["core_technology"] = "CHANGED"
    assert history[1]["tech_summary"]["data"]["core_technology"] != "CHANGED"


def test_select_candidate_resets_previous_fields():
    """select_candidate 가 후보별 필드 7개를 초기화하는지 직접 확인."""
    from investment_scout.nodes import select_candidate

    profiles = mock_seed_profiles(2)
    state = {
        "candidate_startups": [p["name"] for p in profiles],
        "candidate_profiles": {p["name"]: p for p in profiles},
        "candidate_index": 1,
        "max_candidates": 20,
        "evaluated_startups": [profiles[0]["name"]],
        # 이전 기업의 잔여 값
        "tech_summary": {"status": "OK"},
        "tech_category": "STALE",
        "market_analysis": {"status": "OK"},
        "competitor_analysis": {"status": "OK"},
        "evaluation_scores": {"tech": 5.0},
        "investment_decision": "HOLD",
        "hold_reason": "이전 사유",
    }
    update = select_candidate(state)

    assert update["current_startup"]["name"] == profiles[1]["name"]
    assert update["tech_summary"] == {}
    assert update["tech_category"] == ""
    assert update["market_analysis"] == {}
    assert update["competitor_analysis"] == {}
    assert update["evaluation_scores"] == {}
    assert update["investment_decision"] == ""
    assert update["hold_reason"] is None


# 10. 후보 20개 모두 HOLD: recursion 오류 없이 종료
def test_twenty_candidates_all_hold_completes_without_recursion_error():
    final = run_graph(candidates=20, max_candidates=20)

    assert len(final["evaluation_history"]) == 20
    assert len(final["evaluated_startups"]) == 20
    assert len(set(final["evaluated_startups"])) == 20
    assert final["candidate_index"] == 20
    assert final["termination_reason"] == TERMINATION_ALL_HOLD


# 11. 잘못된 결정 값: 명확한 오류
def test_unknown_decision_value_raises_clear_error():
    name = names_of(1)[0]
    with pytest.raises(Exception) as exc_info:
        run_graph(candidates=1, invalid_decision_for={name: "MAYBE"})

    message = str(exc_info.value)
    assert "MAYBE" in message
    assert "RECOMMENDED" in message and "HOLD" in message


def test_route_after_record_rejects_unknown_decision():
    from investment_scout.graph import route_after_record

    with pytest.raises(UnknownDecisionError):
        route_after_record(
            {"investment_decision": "???", "candidate_index": 0,
             "candidate_startups": ["x"], "max_candidates": 1}
        )


# 14. 핵심 정보 부족: INSUFFICIENT_DATA 및 HOLD, 구체적 사유
def test_insufficient_core_information_forces_hold_with_reason():
    name = names_of(1)[0]
    final = run_graph(
        candidates=1,
        decisions={name: "RECOMMENDED"},  # 결정 노드는 추천했지만
        insufficient_for={name},           # 시장 분석 등이 INSUFFICIENT_DATA
    )

    record = final["evaluation_history"][0]
    assert record["investment_decision"] == "HOLD"
    assert record["hold_reason"]
    assert "누락 정보" in record["hold_reason"]
    assert "추가 확인 사항" in record["hold_reason"]
    assert record["missing_core_information"]  # 어떤 필드가 부족한지 구체적으로 기록
    assert final["termination_reason"] != TERMINATION_RECOMMENDED_FOUND

    # 결정 변경이 진단으로 남아야 합니다.
    kinds = {d["kind"] for d in final["diagnostics"]}
    assert "DECISION_OVERRIDDEN_TO_HOLD" in kinds


# 인덱스 안전성: 범위를 벗어난 인덱스는 후보 접근 전에 잡힙니다.
def test_select_candidate_rejects_out_of_range_index():
    from investment_scout.nodes import select_candidate

    profiles = mock_seed_profiles(1)
    state = {
        "candidate_startups": [profiles[0]["name"]],
        "candidate_profiles": {profiles[0]["name"]: profiles[0]},
        "candidate_index": 1,
        "max_candidates": 20,
        "evaluated_startups": [],
    }
    with pytest.raises(CandidateIndexError, match="평가 한도"):
        select_candidate(state)


def test_select_candidate_rejects_already_evaluated_company():
    from investment_scout.nodes import select_candidate

    profiles = mock_seed_profiles(1)
    name = profiles[0]["name"]
    state = {
        "candidate_startups": [name],
        "candidate_profiles": {name: profiles[0]},
        "candidate_index": 0,
        "max_candidates": 20,
        "evaluated_startups": [name],
    }
    with pytest.raises(CandidateIndexError, match="이미 평가"):
        select_candidate(state)
