"""다른 담당자 노드를 대신하는 교체 가능한 mock.

[경고] 이 패키지의 모든 데이터와 결과는 **테스트용 가짜 값**입니다.
실제 조사·평가 결과가 아닙니다. 다음으로 구분할 수 있습니다.
- 기업명 접두사 `[TEST]`
- 출처의 `is_mock=True`, URL 도메인 `mock.invalid`
- 분석 결과 data 의 `"_mock": True`
- 보고서 본문 첫 줄의 `[MOCK REPORT - 실제 보고서 아님]`

실제 담당자 구현이 준비되면 `build_graph(...)` 에 해당 노드를 주입해 교체합니다.
"""

from typing import Any, Dict, Optional

from investment_scout.mocks.analysis import (
    make_category_node,
    make_competitor_node,
    make_market_node,
    make_tech_node,
)
from investment_scout.mocks.decision import make_decision_node
from investment_scout.mocks.fixtures import mock_seed_profiles
from investment_scout.mocks.report import make_report_node

MOCK_MARKER = "_mock"


def build_mock_nodes(
    *,
    decisions: Optional[Dict[str, str]] = None,
    default_decision: str = "HOLD",
    insufficient_for: Optional[Any] = None,
    unsourced_for: Optional[Any] = None,
    dangling_for: Optional[Any] = None,
    invalid_decision_for: Optional[Dict[str, str]] = None,
    broken_schema_for: Optional[Any] = None,
) -> Dict[str, Any]:
    """graph.build_graph 에 넘길 mock 노드 묶음."""
    shared = {
        "insufficient_for": set(insufficient_for or ()),
        "unsourced_for": set(unsourced_for or ()),
        "dangling_for": set(dangling_for or ()),
        "broken_schema_for": set(broken_schema_for or ()),
    }
    return {
        "tech_node": make_tech_node(**shared),
        "category_node": make_category_node(**shared),
        "market_node": make_market_node(**shared),
        "competitor_node": make_competitor_node(**shared),
        "decision_node": make_decision_node(
            decisions=decisions,
            default_decision=default_decision,
            invalid_decision_for=invalid_decision_for,
        ),
        "report_node": make_report_node(),
    }


__all__ = [
    "MOCK_MARKER",
    "build_mock_nodes",
    "make_category_node",
    "make_competitor_node",
    "make_decision_node",
    "make_market_node",
    "make_report_node",
    "make_tech_node",
    "mock_seed_profiles",
]
