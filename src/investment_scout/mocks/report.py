"""보고서 생성 노드의 mock.

실제 보고서는 다른 담당자의 영역입니다. 이 mock 은 **완성 보고서가 아니며**,
아래 5가지 종료 상황을 구분해 요약만 만듭니다.

  1. RECOMMENDED_FOUND        추천 기업 있음
  2. ALL_HOLD                 평가한 기업이 모두 보류
  3. NO_ELIGIBLE_CANDIDATES   평가할 적격 후보 없음
  4. ZERO_LIMIT               평가 한도가 0이라 평가 미수행
  5. LIMIT_REACHED            최대 평가 수 도달, 미평가 후보 남음

보고서는 마지막 기업의 분석값이 아니라 **evaluation_history 전체**와
source_evidence 를 사용합니다.
"""

from __future__ import annotations

from typing import Any, Callable, Dict, List

from investment_scout.contracts import validate_node_update
from investment_scout.state import (
    DECISION_RECOMMENDED,
    TERMINATION_ALL_HOLD,
    TERMINATION_LIMIT_REACHED,
    TERMINATION_NO_ELIGIBLE_CANDIDATES,
    TERMINATION_RECOMMENDED_FOUND,
    TERMINATION_ZERO_LIMIT,
    InvestmentAgentState,
    evaluation_limit,
)

MOCK_HEADER = "[MOCK REPORT - 실제 보고서 아님]"

TERMINATION_TEXT = {
    TERMINATION_RECOMMENDED_FOUND: "전체 평가 후 추천 기준을 통과한 기업 중 1순위를 추천했습니다.",
    TERMINATION_ALL_HOLD: "평가한 기업이 모두 보류되어 후보를 모두 소진했습니다.",
    TERMINATION_NO_ELIGIBLE_CANDIDATES: "평가할 적격(ELIGIBLE) 후보가 없어 평가를 수행하지 않았습니다.",
    TERMINATION_ZERO_LIMIT: "max_candidates=0 이라 평가를 수행하지 않았습니다.",
    TERMINATION_LIMIT_REACHED: "최대 평가 수에 도달해 종료했습니다. 미평가 후보가 남아 있습니다.",
}


def make_report_node() -> Callable[[Dict[str, Any]], Dict[str, Any]]:
    """보고서 생성 mock 노드를 만듭니다."""

    # 보고서 생성 노드 (mock)
    #   읽는 필드   : evaluation_history, candidate_startups, max_candidates, scout_result,
    #                 termination_reason, diagnostics
    #   갱신하는 필드: final_report
    def generate_report(state: InvestmentAgentState) -> Dict[str, Any]:
        """[MOCK] 종료 사유별로 구분한 요약 보고서를 만들어 반환합니다."""
        history: List[Dict[str, Any]] = state.get("evaluation_history", []) or []
        candidates: List[str] = state.get("candidate_startups", []) or []
        limit = evaluation_limit(state)
        reviewed = len(history)
        not_reviewed = max(0, len(candidates) - reviewed)

        termination = state.get("termination_reason") or _infer_termination(state, history, limit)
        recommended = [r for r in history if r.get("investment_decision") == DECISION_RECOMMENDED]
        scout = state.get("scout_result") or {}

        lines: List[str] = [
            MOCK_HEADER,
            "",
            f"# [MOCK] {state.get('target_domain', '')} 도메인 AI 스타트업 투자 검토 요약",
            "",
            "## 실행 요약",
            f"- 종료 사유: {termination} — {TERMINATION_TEXT.get(termination, '알 수 없음')}",
            f"- 적격 후보 수: {len(candidates)}",
            f"- 평가 한도: {limit} (max_candidates={state.get('max_candidates')})",
            f"- 검토한 기업 수: {reviewed}",
            f"- 미검토 기업 수: {not_reviewed}",
            f"- 추천 기업 수: {len(recommended)}",
            "",
        ]

        counts = scout.get("counts") or {}
        if counts:
            lines.append("## 후보 확보 현황 (목표 대비)")
            for region, info in counts.items():
                lines.append(
                    f"- {region}: 목표 {info['target']} / 확보 {info['secured']} / "
                    f"부족 {info['shortfall']} (심사 {info['screened']})"
                )
            lines.append("")

        if not history:
            lines += ["## 평가 결과", "- 평가를 수행하지 않았습니다.", ""]
        else:
            lines.append("## 기업별 평가 결과 (evaluation_history 전체)")
            for order, record in enumerate(history, start=1):
                lines.append(
                    f"{order}. {record['startup']} — {record['investment_decision']}"
                )
                if record.get("hold_reason"):
                    lines.append(f"   - 보류 사유: {record['hold_reason']}")
                if record.get("missing_core_information"):
                    lines.append(f"   - 부족 정보: {record['missing_core_information']}")
                if record.get("score_gaps"):
                    lines.append(f"   - 점수 생략: {record['score_gaps']}")
                # 출처가 뒷받침한 주장만 인용합니다.
                for field in ("tech_summary", "market_analysis", "competitor_analysis"):
                    for claim in (record.get(field) or {}).get("claims", []):
                        lines.append(
                            f"   - [{field}] {claim['text']} (출처: {claim['source_ids']})"
                        )
            lines.append("")

        sources = state.get("source_evidence") or {}
        lines.append("## 참고 출처")
        if not sources:
            lines.append("- 등록된 출처가 없습니다.")
        else:
            for startup, items in sources.items():
                for source in items:
                    mock_tag = " [MOCK]" if source.get("is_mock") else ""
                    lines.append(
                        f"- ({startup}) {source['source_id']}{mock_tag}: "
                        f"{source['title']} — {source['url']} "
                        f"(게시일: {source['published_at']}, 확인일: {source['accessed_at']})"
                    )
        lines += [
            "",
            "## 한계",
            "- 이 문서는 mock 보고서이며 실제 투자 검토 결론이 아닙니다.",
            "- 실제 보고서 담당자는 '최종 5장 이내' 요구사항과 입력 계약(README의 노드 계약표)을 따라야 합니다.",
        ]

        diagnostics = state.get("diagnostics") or []
        if diagnostics:
            lines += ["", "## 진단 기록 (제외/오류)"]
            for diag in diagnostics:
                lines.append(
                    f"- [{diag['category']}/{diag['kind']}] "
                    f"{diag.get('startup') or '-'}: {diag['message']}"
                )

        update = {"final_report": "\n".join(lines)}
        return validate_node_update(
            update, allowed_fields=("final_report",), node_name="generate_report"
        )

    return generate_report


def _infer_termination(state: Dict[str, Any], history: List[Dict[str, Any]], limit: int) -> str:
    """termination_reason 이 비어 있을 때의 보수적 추론 (정상 경로에서는 쓰이지 않음)."""
    if state.get("max_candidates") == 0:
        return TERMINATION_ZERO_LIMIT
    if not state.get("candidate_startups"):
        return TERMINATION_NO_ELIGIBLE_CANDIDATES
    if any(r.get("investment_decision") == DECISION_RECOMMENDED for r in history):
        return TERMINATION_RECOMMENDED_FOUND
    if limit < len(state.get("candidate_startups", [])):
        return TERMINATION_LIMIT_REACHED
    return TERMINATION_ALL_HOLD
