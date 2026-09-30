"""LangGraph 그래프 조립과 조건 분기 (오케스트레이션 담당).

흐름:

    START
      → scout_candidates          (후보 탐색 + 적격성 검증)
      → [평가 가능 후보 확인]
           없음 → generate_report
           있음 → select_candidate
      → tech_analysis → tech_classification → market_analysis
      → competitor_analysis → investment_decision
      → record_evaluation         (결과 저장 + 인덱스 1 증가)
      → [조건 분기]  (팀 결정: 추천 기준을 통과해도 전체 후보를 평가)
           남은 후보 있음          → select_candidate
           한도 도달               → generate_report
                                     (record_evaluation 이 70점 이상 중 1순위를 recommended_startup 으로 선정,
                                      없으면 전원 보류)
      → END

종료 보장:
- 평가 한도 = min(max_candidates, len(candidate_startups)) 로 유한합니다.
- 인덱스는 record_evaluation 한 곳에서만 +1 되며 되돌아가지 않습니다.
- 따라서 select_candidate 진입 횟수는 평가 한도를 넘을 수 없습니다.
- recursion limit 은 안전망일 뿐 종료 로직의 대체 수단이 아닙니다.
"""

from __future__ import annotations

from typing import Any, Callable, Dict, Optional

from langgraph.graph import END, START, StateGraph

from investment_scout.agents.startup_scout import StartupScout, make_scout_node
from investment_scout.nodes import record_evaluation, select_candidate
from investment_scout.state import (
    DECISION_HOLD,
    DECISION_RECOMMENDED,
    InvestmentAgentState,
    VALID_DECISIONS,
    create_initial_state,
    evaluation_limit,
    validate_max_candidates,
)

# 노드 함수 시그니처: 현재 State 를 받아 갱신할 필드만 딕셔너리로 반환
NodeFn = Callable[[Dict[str, Any]], Dict[str, Any]]

# 노드 이름 (다른 담당자와 공유하는 식별자)
NODE_SCOUT = "scout_candidates"
NODE_SELECT = "select_candidate"
NODE_TECH = "tech_analysis"
NODE_CATEGORY = "tech_classification"
NODE_MARKET = "market_analysis"
NODE_COMPETITOR = "competitor_analysis"
NODE_DECISION = "investment_decision"
NODE_RECORD = "record_evaluation"
NODE_REPORT = "generate_report"

# 한 기업을 평가하는 데 거치는 노드 수
# (select → tech → category → market → comp → decision → record)
NODES_PER_CANDIDATE = 7

# 발표/문서용 업무 흐름도. LangGraph의 select_candidate와 record_evaluation은
# 각각 "스타트업 탐색"과 조건 분기에 포함해 간결하게 표시합니다.
BUSINESS_FLOW_MERMAID = """graph TD
    A[스타트업 탐색] --> B[기술 요약]
    B --> C[기술 분류]
    C --> D[시장성 평가]
    D --> E[경쟁사 비교]
    E --> F[투자 판단]
    F -->|남은 후보 있음| A
    F -->|전체 평가 완료| H[순위 선정: 70점 이상 중 1순위 추천, 없으면 전원 보류]
    H --> G[보고서 생성]
"""


class UnknownDecisionError(ValueError):
    """조건 분기에서 알 수 없는 investment_decision 을 만난 경우."""


def recommended_recursion_limit(max_candidates: int) -> int:
    """후보를 모두 순회하기에 충분한 recursion limit: 종료는 조건 분기가 보장하고 이 값은 안전망입니다."""
    validate_max_candidates(max_candidates)

    # scout + report + 여유분
    return NODES_PER_CANDIDATE * max_candidates + 10


# ---------------------------------------------------------------------------
# 조건 분기 (라우팅 함수: State 를 받아 경로 키 문자열을 반환)
# ---------------------------------------------------------------------------

# 후보 탐색 직후 분기: 평가할 적격 후보가 한 곳도 없으면 평가를 건너뛰고 보고서로 갑니다.
def route_after_scout(state: InvestmentAgentState) -> str:
    """평가 가능한 후보가 있으면 'evaluate', 없으면 'report'."""
    return "evaluate" if evaluation_limit(state) > 0 else "report"


# 평가 저장 직후 분기: 최초 RECOMMENDED 에서 종료하고, HOLD 면 한도 안에서 다음 후보로 순환합니다.
def route_after_record(state: InvestmentAgentState) -> str:
    """한도에 도달하면 'report', 남은 후보가 있으면 'next' (추천 기준 통과 여부와 무관)."""
    decision = state.get("investment_decision")
    if decision not in VALID_DECISIONS:
        raise UnknownDecisionError(
            f"알 수 없는 investment_decision 입니다: {decision!r} (허용: {sorted(VALID_DECISIONS)})"
        )

    # 팀 결정: 70점 이상 기업 중 1순위를 고르기 위해 모든 후보를 평가합니다.
    index = state.get("candidate_index", 0)
    limit = evaluation_limit(state)

    # 마지막 평가 저장 후 index == limit 이 되는 것은 정상 종료 상태입니다.
    return "next" if index < limit else "report"


# ---------------------------------------------------------------------------
# 그래프 조립
# ---------------------------------------------------------------------------
def build_graph(
    *,
    scout: Optional[StartupScout] = None,
    scout_node: Optional[NodeFn] = None,
    tech_node: NodeFn,
    category_node: NodeFn,
    market_node: NodeFn,
    competitor_node: NodeFn,
    decision_node: NodeFn,
    report_node: NodeFn,
    checkpointer: Any = None,
):
    """평가 노드를 주입받아 그래프를 조립하고 컴파일합니다.

    tech/category/market/competitor/decision/report 노드는 다른 담당자의 영역이므로
    반드시 주입받습니다. 기본 mock 구성은 `build_mock_graph()` 를 사용하세요.
    """
    if scout_node is None:
        if scout is None:
            raise ValueError("scout 또는 scout_node 중 하나는 반드시 주입해야 합니다")
        scout_node = make_scout_node(scout)

    workflow = StateGraph(InvestmentAgentState)

    # Node
    workflow.add_node(NODE_SCOUT, scout_node)
    workflow.add_node(NODE_SELECT, select_candidate)
    workflow.add_node(NODE_TECH, tech_node)
    workflow.add_node(NODE_CATEGORY, category_node)
    workflow.add_node(NODE_MARKET, market_node)
    workflow.add_node(NODE_COMPETITOR, competitor_node)
    workflow.add_node(NODE_DECISION, decision_node)
    workflow.add_node(NODE_RECORD, record_evaluation)
    workflow.add_node(NODE_REPORT, report_node)

    # Edge
    workflow.add_edge(START, NODE_SCOUT)

    # 조건부 엣지: 적격 후보가 있으면 평가 파이프라인으로, 없으면 바로 보고서로
    workflow.add_conditional_edges(
        NODE_SCOUT,
        route_after_scout,
        {
            "evaluate": NODE_SELECT,    # 적격 후보 있음 → 첫 후보 선택
            "report": NODE_REPORT,      # 적격 후보 없음 → 평가 없이 보고서
        },
    )

    # 한 기업의 평가 파이프라인 (고정 순서)
    workflow.add_edge(NODE_SELECT, NODE_TECH)
    workflow.add_edge(NODE_TECH, NODE_CATEGORY)
    workflow.add_edge(NODE_CATEGORY, NODE_MARKET)
    workflow.add_edge(NODE_MARKET, NODE_COMPETITOR)
    workflow.add_edge(NODE_COMPETITOR, NODE_DECISION)
    workflow.add_edge(NODE_DECISION, NODE_RECORD)

    # 조건부 엣지: 다음 후보로 순환할지 보고서로 끝낼지 결정
    workflow.add_conditional_edges(
        NODE_RECORD,
        route_after_record,
        {
            "next": NODE_SELECT,        # HOLD + 한도 내 → 다음 후보 평가
            "report": NODE_REPORT,      # RECOMMENDED 또는 한도 도달 → 보고서
        },
    )

    workflow.add_edge(NODE_REPORT, END)

    # Compile
    return workflow.compile(checkpointer=checkpointer)


def build_mock_graph(
    *,
    decisions: Optional[Dict[str, str]] = None,
    seed_profiles: Optional[list] = None,
    search_provider: Any = None,
    default_decision: str = DECISION_HOLD,
    **mock_kwargs: Any,
):
    """다른 담당자 노드를 mock 으로 채운 그래프 (데모·테스트 전용, 실제 평가 결과가 아닙니다)."""
    from investment_scout.mocks import build_mock_nodes
    from investment_scout.search import MockSearchProvider

    provider = search_provider or MockSearchProvider()
    scout = StartupScout(search_provider=provider, seed_profiles=seed_profiles)

    nodes = build_mock_nodes(
        decisions=decisions, default_decision=default_decision, **mock_kwargs
    )

    return build_graph(scout=scout, **nodes)


__all__ = [
    "NODE_SCOUT",
    "NODE_SELECT",
    "NODE_TECH",
    "NODE_CATEGORY",
    "NODE_MARKET",
    "NODE_COMPETITOR",
    "NODE_DECISION",
    "NODE_RECORD",
    "NODE_REPORT",
    "BUSINESS_FLOW_MERMAID",
    "UnknownDecisionError",
    "build_graph",
    "build_mock_graph",
    "create_initial_state",
    "recommended_recursion_limit",
    "route_after_record",
    "route_after_scout",
]
