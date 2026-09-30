"""State 정의와 초기값 테스트."""

from __future__ import annotations

import pytest

from investment_scout.state import (
    DEFAULT_MAX_CANDIDATES,
    InvestmentAgentState,
    PER_CANDIDATE_FIELDS,
    create_initial_state,
    evaluation_limit,
    make_diagnostic,
    reset_per_candidate_fields,
    validate_max_candidates,
)

REQUIRED_FIELDS = {
    "target_domain", "candidate_startups", "evaluated_startups", "current_startup",
    "tech_summary", "tech_category", "market_analysis", "competitor_analysis",
    "evaluation_scores", "investment_decision", "hold_reason", "final_report",
    "candidate_index", "max_candidates", "evaluation_history", "source_evidence",
    "candidate_profiles",
}


def test_all_required_fields_present_with_initial_values():
    state = create_initial_state("Semiconductor")

    assert REQUIRED_FIELDS <= set(state)
    assert state["target_domain"] == "Semiconductor"
    assert state["candidate_startups"] == []
    assert state["evaluated_startups"] == []
    assert state["current_startup"] == {}  # 미선택 상태는 빈 딕셔너리
    assert state["investment_decision"] == ""  # 초기화 시 빈 문자열
    assert state["hold_reason"] is None
    assert state["final_report"] == ""
    assert state["candidate_index"] == 0  # 최초 0
    assert state["max_candidates"] == DEFAULT_MAX_CANDIDATES  # 기본값 20
    assert state["evaluation_history"] == []
    assert state["source_evidence"] == {}
    assert state["candidate_profiles"] == {}
    # 보조 필드
    assert state["scout_result"] == {}
    assert state["termination_reason"] == ""
    assert state["diagnostics"] == []


def test_state_typeddict_keeps_original_field_names_and_types():
    import typing

    # state.py 가 `from __future__ import annotations` 를 쓰므로 실제 타입으로 해석합니다.
    hints = typing.get_type_hints(InvestmentAgentState)
    assert hints["target_domain"] is str
    assert str(hints["candidate_startups"]) == "typing.List[str]"
    assert str(hints["evaluated_startups"]) == "typing.List[str]"
    assert str(hints["current_startup"]) == "typing.Dict[str, typing.Any]"
    assert str(hints["evaluation_scores"]) == "typing.Dict[str, float]"
    assert hints["investment_decision"] is str
    assert hints["hold_reason"] == typing.Optional[str]
    assert hints["final_report"] is str
    assert hints["candidate_index"] is int
    assert hints["max_candidates"] is int


@pytest.mark.parametrize("bad", [-1, "3", 3.0, True, None])
def test_max_candidates_must_be_non_negative_int(bad):
    with pytest.raises(ValueError):
        validate_max_candidates(bad)


def test_max_candidates_zero_is_allowed():
    assert validate_max_candidates(0) == 0
    assert create_initial_state("X", max_candidates=0)["max_candidates"] == 0


@pytest.mark.parametrize(
    "candidates, max_candidates, expected",
    [(5, 2, 2), (2, 5, 2), (0, 20, 0), (3, 0, 0)],
)
def test_evaluation_limit(candidates, max_candidates, expected):
    state = {
        "candidate_startups": [f"c{i}" for i in range(candidates)],
        "max_candidates": max_candidates,
    }
    assert evaluation_limit(state) == expected


def test_reset_returns_independent_copies():
    a = reset_per_candidate_fields()
    b = reset_per_candidate_fields()
    a["tech_summary"]["x"] = 1

    assert b["tech_summary"] == {}
    assert PER_CANDIDATE_FIELDS["tech_summary"] == {}
    assert set(a) == {
        "tech_summary", "tech_category", "market_analysis", "competitor_analysis",
        "evaluation_scores", "evaluation_details", "investment_decision", "hold_reason",
    }


def test_diagnostic_category_is_validated():
    diag = make_diagnostic(kind="X", category="DATA_GAP", message="m")
    assert diag["category"] == "DATA_GAP"
    with pytest.raises(ValueError):
        make_diagnostic(kind="X", category="WHATEVER", message="m")
