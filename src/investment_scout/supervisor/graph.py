"""Supervisor 패턴 그래프.

    START → scout → supervisor ─(add_conditional_edges: State 기반 라우팅)─┬→ tech ───────┐
                        ▲                                                ├→ market ─────┤
                        │                                                ├→ competitor ─┤
                        │                                                ├→ judge ──────┤
                        │                                                ├→ record ─────┤
                        │                                                ├→ rank ───────┤
                        │                                                ├→ report ─────┤
                        │                                                ├→ quality ────┤
                        └────────────── 모든 하위 노드는 supervisor로만 복귀 ◀───────────┘
                                                                         └→ END

하위 에이전트 사이에는 엣지가 없다(직접 통신 금지). 다음 노드는 매번 Supervisor가 State를 보고 정한다.
"""

from __future__ import annotations

from langgraph.graph import END, START, StateGraph

from investment_scout.supervisor.nodes import Nodes
from investment_scout.supervisor.state import SupervisorState

WORKERS = ("tech", "market", "competitor", "judge", "record", "rank", "report", "quality")


def build_supervisor_graph(nodes: Nodes, *, checkpointer=None):
    graph = StateGraph(SupervisorState)
    graph.add_node("scout", nodes.scout_node)
    graph.add_node("supervisor", nodes.supervisor)
    for name in WORKERS:
        graph.add_node(name, getattr(nodes, name))
        graph.add_edge(name, "supervisor")          # 하위 노드 → Supervisor (유일한 경로)
    graph.add_edge(START, "scout")
    graph.add_edge("scout", "supervisor")
    graph.add_conditional_edges("supervisor", nodes.route, {**{name: name for name in WORKERS}, "end": END})
    return graph.compile(checkpointer=checkpointer)


SUPERVISOR_MERMAID = """graph TD
    S[🔍 스타트업 탐색] --> V{🧭 Supervisor<br/>State 기반 라우팅·근거 충분성 판단}
    V -->|결과 없음·근거 부족 재작업| T[🗜️ 기술 요약·분류]
    V -->|결과 없음·근거 부족 재작업| M[📊 시장성 평가]
    V -->|결과 없음·근거 부족 재작업| C[🥊 경쟁사 비교]
    V -->|세 관점 확보| J[🧮 투자 판단]
    V -->|판단 완료| R[💾 평가 저장]
    V -->|전체 평가 완료| K[🏆 순위 선정]
    V -->|순위 확정·품질 미달 재작성| P[📝 보고서 작성]
    V -->|보고서 생성| Q[✅ 품질 평가]
    T --> V
    M --> V
    C --> V
    J --> V
    R --> V
    K --> V
    P --> V
    Q --> V
    V -->|품질 통과·상한 도달| E((END))
"""
