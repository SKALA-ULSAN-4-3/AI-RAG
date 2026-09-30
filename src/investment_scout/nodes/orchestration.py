"""후보 순환과 평가 결과 확정을 담당하는 노드."""

from __future__ import annotations

import copy
from typing import Any, Dict, List

from investment_scout.contracts import (
    SchemaViolationError,
    find_missing_core_information,
    validate_analysis_result,
    validate_node_update,
)
from investment_scout.evidence import collect_source_ids, drop_unsupported_claims
from investment_scout.state import (
    DECISION_HOLD,
    DECISION_RECOMMENDED,
    DIAG_DATA_GAP,
    PER_CANDIDATE_FIELDS,
    TERMINATION_ALL_HOLD,
    TERMINATION_LIMIT_REACHED,
    TERMINATION_RECOMMENDED_FOUND,
    VALID_DECISIONS,
    InvestmentAgentState,
    evaluation_limit,
    make_diagnostic,
    reset_per_candidate_fields,
)

ANALYSIS_FIELDS = ("tech_summary", "market_analysis", "competitor_analysis")

SELECT_NODE_FIELDS = ("current_startup", *PER_CANDIDATE_FIELDS.keys())
RECORD_NODE_FIELDS = (
    "evaluation_history",
    "evaluated_startups",
    "candidate_index",
    "investment_decision",
    "hold_reason",
    "diagnostics",
    "termination_reason",
    *ANALYSIS_FIELDS,
    "evaluation_scores",
)


class CandidateIndexError(IndexError):
    """candidate_index 가 유효 범위를 벗어난 경우."""


class InvalidDecisionError(ValueError):
    """investment_decision 값이 RECOMMENDED / HOLD 가 아닌 경우."""


# ---------------------------------------------------------------------------
# 현재 후보 선택
# ---------------------------------------------------------------------------

# 후보 선택 노드
#   읽는 필드   : candidate_startups, candidate_index, max_candidates, candidate_profiles, evaluated_startups
#   갱신하는 필드: current_startup + 후보별 분석 필드 7개(초기화)
def select_candidate(state: InvestmentAgentState) -> Dict[str, Any]:
    """candidate_index 가 가리키는 후보를 current_startup 으로 올립니다(이미 평가한 기업은 재선택 금지)."""
    candidates: List[str] = state.get("candidate_startups", [])
    index: int = state.get("candidate_index", 0)
    limit = evaluation_limit(state)

    if not isinstance(index, int) or isinstance(index, bool):
        raise CandidateIndexError(f"candidate_index 는 정수여야 합니다: {index!r}")
    if index < 0:
        raise CandidateIndexError(f"candidate_index 가 음수입니다: {index}")
    if index >= limit:
        raise CandidateIndexError(
            f"candidate_index({index}) 가 평가 한도({limit})를 벗어났습니다. "
            f"후보 수={len(candidates)}, max_candidates={state.get('max_candidates')}"
        )

    name = candidates[index]
    if name in state.get("evaluated_startups", []):
        raise CandidateIndexError(
            f"'{name}' 은 이미 평가되었습니다. 한 실행에서 기업별 평가는 1회만 수행합니다."
        )

    profile = state.get("candidate_profiles", {}).get(name)
    if profile is None:
        raise CandidateIndexError(
            f"candidate_profiles 에 '{name}' 프로필이 없습니다. "
            f"candidate_startups 와 candidate_profiles 의 키가 일치해야 합니다."
        )

    update: Dict[str, Any] = {
        # 다음 노드들이 참조할 현재 기업 상세 정보
        "current_startup": copy.deepcopy(profile),
        # 이전 기업의 분석값이 새어나가지 않도록 초기화
        **reset_per_candidate_fields(),
    }
    return validate_node_update(
        update, allowed_fields=SELECT_NODE_FIELDS, node_name="select_candidate"
    )


# ---------------------------------------------------------------------------
# 평가 결과 저장
# ---------------------------------------------------------------------------

# 평가 저장 노드
#   읽는 필드   : current_startup, tech_summary, tech_category, market_analysis, competitor_analysis,
#                 evaluation_scores, investment_decision, hold_reason, source_evidence,
#                 evaluation_history, evaluated_startups, candidate_index, candidate_startups,
#                 max_candidates, diagnostics
#   갱신하는 필드: evaluation_history, evaluated_startups, candidate_index, investment_decision,
#                 hold_reason, diagnostics, termination_reason, tech_summary, market_analysis,
#                 competitor_analysis, evaluation_scores
#   candidate_index 는 이 노드 한 곳에서만 정확히 1 증가합니다 (무한 루프 방지).
def record_evaluation(state: InvestmentAgentState) -> Dict[str, Any]:
    """현재 기업의 평가 결과를 확정해 이력에 저장하고 candidate_index 를 1 증가시킵니다."""
    name = (state.get("current_startup") or {}).get("name")
    if not name:
        raise CandidateIndexError(
            "current_startup 이 비어 있어 평가 결과를 저장할 수 없습니다. "
            "select_candidate 를 먼저 실행해야 합니다."
        )

    decision = state.get("investment_decision")
    if decision not in VALID_DECISIONS:
        raise InvalidDecisionError(
            f"'{name}' 의 investment_decision 이 올바르지 않습니다: {decision!r} "
            f"(허용: {sorted(VALID_DECISIONS)})"
        )

    source_evidence = state.get("source_evidence", {})
    known_source_ids = collect_source_ids(source_evidence)
    diagnostics: List[Dict[str, Any]] = list(state.get("diagnostics", []))

    # 1) 스키마 검증 (자료 부족이 아니라 시스템 오류로 다룹니다)
    for field in ANALYSIS_FIELDS:
        result = state.get(field)
        if not isinstance(result, dict) or not result:
            raise SchemaViolationError(
                f"'{name}': {field} 가 비어 있습니다. 분석 노드가 결과를 채워야 합니다."
            )
        validate_analysis_result(result, field_name=field)

    # 2) 출처가 뒷받침하지 않는 주장을 확정 결과에서 제외
    confirmed: Dict[str, Any] = {}
    for field in ANALYSIS_FIELDS:
        cleaned, dropped = drop_unsupported_claims(
            state[field],
            source_evidence=source_evidence,
            field_name=field,
            startup=name,
        )
        confirmed[field] = cleaned
        diagnostics.extend(dropped)

    # 3) 평가 필수 정보 점검 -> 부족하면 HOLD 로 강제
    missing = find_missing_core_information(
        {**state, **confirmed}
    )
    final_decision = decision
    hold_reason = state.get("hold_reason")

    if missing:
        flat = [item for reasons in missing.values() for item in reasons]
        forced_reason = (
            "평가 핵심 정보 부족으로 보류합니다. "
            f"누락 정보: {flat}. "
            "추가 확인 사항: 해당 항목을 뒷받침하는 1차 자료(기업 공시/공식 발표/"
            "신뢰 가능한 매체 기사)를 확보한 뒤 재평가가 필요합니다."
        )
        if decision == DECISION_RECOMMENDED:
            diagnostics.append(
                make_diagnostic(
                    kind="DECISION_OVERRIDDEN_TO_HOLD",
                    category=DIAG_DATA_GAP,
                    message="핵심 정보가 부족해 RECOMMENDED 를 HOLD 로 변경했습니다",
                    startup=name,
                    details={"missing": missing},
                )
            )
        final_decision = DECISION_HOLD
        hold_reason = forced_reason if not hold_reason else f"{hold_reason} | {forced_reason}"

    # 4) 근거가 부족한 점수는 만들어내지 않습니다.
    #    타입(Dict[str, float])은 유지하고, 산출 불가능한 항목은 생략한 뒤 사유를 남깁니다.
    scores, score_gaps = _filter_scores(state.get("evaluation_scores") or {}, name)
    diagnostics.extend(score_gaps["diagnostics"])

    # 5) 이력에 독립적으로 저장 (깊은 복사)
    record = {
        "startup": name,
        "profile": copy.deepcopy(state.get("current_startup") or {}),
        "tech_category": state.get("tech_category", ""),
        "tech_summary": copy.deepcopy(confirmed["tech_summary"]),
        "market_analysis": copy.deepcopy(confirmed["market_analysis"]),
        "competitor_analysis": copy.deepcopy(confirmed["competitor_analysis"]),
        "evaluation_scores": copy.deepcopy(scores),
        "evaluation_details": copy.deepcopy(state.get("evaluation_details") or {}),
        "score_gaps": score_gaps["gaps"],
        "investment_decision": final_decision,
        "hold_reason": hold_reason,
        "missing_core_information": copy.deepcopy(missing),
        # 근거는 기업명으로 source_evidence 를 조회하도록 참조만 남깁니다.
        "evidence_ref": {
            "source_evidence_key": name,
            "source_ids": sorted(
                {s.get("source_id") for s in source_evidence.get(name, []) if isinstance(s, dict)}
                & known_source_ids
            ),
        },
    }

    next_index = state.get("candidate_index", 0) + 1
    limit = evaluation_limit(state)
    total_candidates = len(state.get("candidate_startups", []))

    if final_decision == DECISION_RECOMMENDED:
        termination_reason = TERMINATION_RECOMMENDED_FOUND
    elif next_index >= limit:
        termination_reason = (
            TERMINATION_LIMIT_REACHED if limit < total_candidates else TERMINATION_ALL_HOLD
        )
    else:
        termination_reason = ""

    update: Dict[str, Any] = {
        "evaluation_history": [*state.get("evaluation_history", []), record],
        "evaluated_startups": [*state.get("evaluated_startups", []), name],
        "candidate_index": next_index,
        "investment_decision": final_decision,
        "hold_reason": hold_reason,
        "diagnostics": diagnostics,
        "termination_reason": termination_reason,
        "evaluation_scores": scores,
        **confirmed,
    }
    return validate_node_update(
        update, allowed_fields=RECORD_NODE_FIELDS, node_name="record_evaluation"
    )


# 근거가 부족한 점수는 만들어내지 않는다: 타입(Dict[str, float])은 지키고 산출 불가 항목만 생략한다.
def _filter_scores(scores: Dict[str, Any], startup: str) -> tuple[Dict[str, float], Dict[str, Any]]:
    """숫자로 산출되지 않은 점수를 제거하고 생략 사유를 진단 기록으로 남깁니다."""
    kept: Dict[str, float] = {}
    gaps: List[Dict[str, Any]] = []
    diagnostics: List[Dict[str, Any]] = []

    for key, value in scores.items():
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            gaps.append({"metric": key, "reason": f"산출 불가 (값: {value!r})"})
            diagnostics.append(
                make_diagnostic(
                    kind="SCORE_OMITTED",
                    category=DIAG_DATA_GAP,
                    message=f"근거가 부족해 '{key}' 점수를 생략했습니다",
                    startup=startup,
                    details={"metric": key, "value": value},
                )
            )
            continue
        kept[key] = float(value)

    return kept, {"gaps": gaps, "diagnostics": diagnostics}
