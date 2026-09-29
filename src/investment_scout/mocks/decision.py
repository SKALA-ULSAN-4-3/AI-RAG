"""투자 결정 노드의 mock.

기업별 결과를 지정할 수 있습니다.

    make_decision_node(decisions={"[TEST]테스트기업A": "HOLD",
                                  "[TEST]테스트기업B": "RECOMMENDED"})
"""

from __future__ import annotations

from typing import Any, Callable, Dict, Optional

from investment_scout.contracts import validate_node_update
from investment_scout.state import InvestmentAgentState

DECISION_NODE_FIELDS = ("investment_decision", "hold_reason", "evaluation_scores")


def make_decision_node(
    *,
    decisions: Optional[Dict[str, str]] = None,
    default_decision: str = "HOLD",
    invalid_decision_for: Optional[Dict[str, str]] = None,
    scores: Optional[Dict[str, Any]] = None,
) -> Callable[[Dict[str, Any]], Dict[str, Any]]:
    """기업별 결정을 지정할 수 있는 mock 투자 결정 노드."""
    decisions = dict(decisions or {})
    invalid_decision_for = dict(invalid_decision_for or {})

    # 투자 결정 노드 (mock)
    #   읽는 필드   : current_startup
    #   갱신하는 필드: investment_decision, hold_reason, evaluation_scores
    def investment_decision(state: InvestmentAgentState) -> Dict[str, Any]:
        """[MOCK] 기업명으로 지정된 결정(RECOMMENDED/HOLD)을 반환합니다."""
        name = (state.get("current_startup") or {}).get("name", "UNKNOWN")

        if name in invalid_decision_for:
            decision = invalid_decision_for[name]
        else:
            decision = decisions.get(name, default_decision)

        hold_reason = None
        if decision == "HOLD":
            hold_reason = f"[MOCK] {name}: 테스트 시나리오에 따라 보류로 설정했습니다"

        # 근거가 부족한 점수는 만들지 않습니다.
        # mock 은 산출 가능한 항목만 숫자로 주고, 나머지는 None 으로 남겨
        # record_evaluation 이 생략 사유를 기록하게 합니다.
        default_scores: Dict[str, Any] = {
            "tech": 3.5,
            "market": 3.0,
            "team": None,  # 근거 없음 -> 확정 결과에서 생략됨
        }

        update = {
            "investment_decision": decision,
            "hold_reason": hold_reason,
            "evaluation_scores": dict(scores) if scores is not None else default_scores,
        }
        return validate_node_update(
            update, allowed_fields=DECISION_NODE_FIELDS, node_name="investment_decision"
        )

    return investment_decision
