"""LangGraph State 정의와 초기화 / 업데이트 규칙.

[리스트 필드 업데이트 규칙 - 중요]
이 그래프의 리스트 필드에는 누적 reducer(`operator.add`, `add_messages` 등)를
**사용하지 않습니다**. 모든 리스트 필드는 "노드가 전체 리스트를 만들어 반환하면
State 를 덮어쓴다(overwrite)"는 단일 규칙만 따릅니다.

누적 reducer 방식과 전체 리스트 반환 방식을 섞으면 노드가 전체 리스트를 반환할 때
기존 값 뒤에 다시 붙어 중복 누적이 발생합니다. 그래서 규칙을 하나로 통일했습니다.
리스트에 항목을 추가하는 노드는 반드시 다음 형태로 작성합니다.

    new_list = [*state["evaluated_startups"], name]
    return {"evaluated_startups": new_list}

State 의 각 필드는 `Annotated[타입, "라벨"]` 로 적어 타입과 의미를 함께 남깁니다.
라벨은 문서용이며 reducer 가 아니므로 덮어쓰기 규칙에 영향을 주지 않습니다.
"""

from __future__ import annotations

import copy
from typing import Annotated, Any, Dict, List, Optional, TypedDict

# 투자 결정으로 허용되는 값
DECISION_RECOMMENDED = "RECOMMENDED"
DECISION_HOLD = "HOLD"
VALID_DECISIONS = frozenset({DECISION_RECOMMENDED, DECISION_HOLD})

# 그래프 종료 사유. 보고서 노드가 상황을 구분하는 데 사용합니다.
TERMINATION_RECOMMENDED_FOUND = "RECOMMENDED_FOUND"
TERMINATION_ALL_HOLD = "ALL_HOLD"
TERMINATION_NO_ELIGIBLE_CANDIDATES = "NO_ELIGIBLE_CANDIDATES"
TERMINATION_ZERO_LIMIT = "ZERO_LIMIT"
TERMINATION_LIMIT_REACHED = "LIMIT_REACHED"

VALID_TERMINATION_REASONS = frozenset(
    {
        TERMINATION_RECOMMENDED_FOUND,
        TERMINATION_ALL_HOLD,
        TERMINATION_NO_ELIGIBLE_CANDIDATES,
        TERMINATION_ZERO_LIMIT,
        TERMINATION_LIMIT_REACHED,
    }
)

# 한 실행에서 평가할 후보 수 기본값 (목표: 한국 10 + 해외 10)
DEFAULT_MAX_CANDIDATES = 20

# 진단 기록의 category 값 (자료 부족 / 시스템 오류)
DIAG_DATA_GAP = "DATA_GAP"
DIAG_SYSTEM_ERROR = "SYSTEM_ERROR"

# 다음 후보로 넘어갈 때 초기화해야 하는 필드와 그 초기값
# (현재 기업의 분석 결과가 다음 기업으로 새어나가는 것을 막습니다.)
PER_CANDIDATE_FIELDS: Dict[str, Any] = {
    "tech_summary": {},
    "tech_category": "",
    "market_analysis": {},
    "competitor_analysis": {},
    "evaluation_scores": {},
    "investment_decision": "",
    "hold_reason": None,
}


class InvestmentAgentState(TypedDict):
    """그래프 전체가 공유하는 State (노드들이 읽고 업데이트하는 공유 데이터 구조)."""

    # ---- 기존 팀 정의 (이름·타입 유지) ----
    target_domain: Annotated[str, "Target Domain"]                              # 평가 대상 도메인
    candidate_startups: Annotated[List[str], "Candidate Startups"]              # 평가할 적격 후보 기업명 (덮어쓰기)
    evaluated_startups: Annotated[List[str], "Evaluated Startups"]              # 평가를 마친 기업명 (덮어쓰기)
    current_startup: Annotated[Dict[str, Any], "Current Startup"]               # 현재 평가 중인 기업 프로필

    tech_summary: Annotated[Dict[str, Any], "Tech Summary"]                     # 기술 분석 결과
    tech_category: Annotated[str, "Tech Category"]                              # 기술 분류
    market_analysis: Annotated[Dict[str, Any], "Market Analysis"]               # 시장 분석 결과
    competitor_analysis: Annotated[Dict[str, Any], "Competitor Analysis"]       # 경쟁사 분석 결과

    evaluation_scores: Annotated[Dict[str, float], "Evaluation Scores"]         # 평가 점수 (근거 없는 항목은 생략)
    investment_decision: Annotated[str, "Investment Decision"]                  # 'RECOMMENDED' or 'HOLD'
    hold_reason: Annotated[Optional[str], "Hold Reason"]                        # 보류 사유 (HOLD 일 때 작성)

    final_report: Annotated[str, "Final Report"]                                # 최종 보고서 본문

    # ---- 요구사항으로 추가된 필드 ----
    candidate_index: Annotated[int, "Candidate Index"]                          # 다음에 평가할 후보 위치 (record 에서만 +1)
    max_candidates: Annotated[int, "Max Candidates"]                            # 이번 실행의 평가 한도
    evaluation_history: Annotated[List[Dict[str, Any]], "Evaluation History"]   # 기업별 확정 평가 이력 (덮어쓰기)
    source_evidence: Annotated[Dict[str, List[Dict[str, Any]]], "Source Evidence"]  # 기업명 → 출처 목록
    candidate_profiles: Annotated[Dict[str, Dict[str, Any]], "Candidate Profiles"]  # 기업명 → 프로필

    # ---- 보조 필드 (최소한으로 추가, 이유는 우측 주석 참고) ----
    # scout_result: candidate_startups 에는 ELIGIBLE 만 들어가므로 제외(INELIGIBLE)/검증대기
    #   (NEEDS_VERIFICATION) 기업과 그 사유, 한국·해외 확보 수와 부족 수를 보존할 곳이 필요합니다. (요구사항 3)
    scout_result: Annotated[Dict[str, Any], "Scout Result"]                     # 후보 탐색의 전체 결과

    # termination_reason: 보고서 노드가 5가지 종료 상황(추천 있음 / 전원 보류 / 적격 후보 없음 /
    #   한도 0 / 한도 도달)을 구분하려면 분기 시점의 판단을 남겨야 합니다. (요구사항 8, 11)
    termination_reason: Annotated[str, "Termination Reason"]                    # 그래프 종료 사유

    # diagnostics: "출처 없어 제외된 주장", "존재하지 않는 source_id 참조", "검색 장애", "설정 오류",
    #   "스키마 위반"을 한곳에 모읍니다. 각 항목의 category 가 DATA_GAP / SYSTEM_ERROR 를 구분합니다. (요구사항 6, 15)
    diagnostics: Annotated[List[Dict[str, Any]], "Diagnostics"]                 # 진단 기록 (덮어쓰기)


def make_diagnostic(
    *,
    kind: str,
    category: str,
    message: str,
    startup: Optional[str] = None,
    details: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """진단 기록 한 건 생성: category 로 자료 부족(DATA_GAP)과 시스템 오류(SYSTEM_ERROR)를 구분합니다."""
    if category not in (DIAG_DATA_GAP, DIAG_SYSTEM_ERROR):
        raise ValueError(f"알 수 없는 diagnostics category: {category!r}")

    return {
        "kind": kind,
        "category": category,
        "message": message,
        "startup": startup,
        "details": details or {},
    }


def create_initial_state(
    target_domain: str,
    *,
    max_candidates: int = DEFAULT_MAX_CANDIDATES,
) -> InvestmentAgentState:
    """모든 필드에 초기값을 채운 State 생성: 후보 관련 필드는 scout_candidates 노드가 채웁니다."""
    validate_max_candidates(max_candidates)

    return InvestmentAgentState(
        target_domain=target_domain,
        candidate_startups=[],
        evaluated_startups=[],
        current_startup={},
        tech_summary={},
        tech_category="",
        market_analysis={},
        competitor_analysis={},
        evaluation_scores={},
        investment_decision="",
        hold_reason=None,
        final_report="",
        candidate_index=0,
        max_candidates=max_candidates,
        evaluation_history=[],
        source_evidence={},
        candidate_profiles={},
        scout_result={},
        termination_reason="",
        diagnostics=[],
    )


def validate_max_candidates(value: Any) -> int:
    """max_candidates 가 0 이상의 정수인지 검증합니다."""
    # bool 은 int 의 서브클래스라 별도로 걸러냅니다.
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"max_candidates 는 정수여야 합니다: {value!r}")
    if value < 0:
        raise ValueError(f"max_candidates 는 0 이상이어야 합니다: {value!r}")

    return value


def evaluation_limit(state: InvestmentAgentState) -> int:
    """이번 실행의 평가 한도 = min(max_candidates, len(candidate_startups))."""
    max_candidates = validate_max_candidates(state.get("max_candidates", 0))

    return min(max_candidates, len(state.get("candidate_startups", [])))


def reset_per_candidate_fields() -> Dict[str, Any]:
    """다음 후보로 넘어갈 때 초기화할 필드들의 업데이트 딕셔너리."""
    return copy.deepcopy(PER_CANDIDATE_FIELDS)
