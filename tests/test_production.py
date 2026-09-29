"""검증 데이터와 운영용 노드의 통합 테스트."""

from investment_scout.agents.startup_scout import StartupScout
from investment_scout.graph import (
    build_graph,
    create_initial_state,
    recommended_recursion_limit,
)
from investment_scout.nodes.production import make_production_nodes
from investment_scout.search import MockSearchProvider
from investment_scout.state import DECISION_HOLD, TERMINATION_LIMIT_REACHED


def test_production_nodes_use_real_evidence_and_terminate_without_mock_content():
    scout = StartupScout(search_provider=MockSearchProvider())
    app = build_graph(scout=scout, **make_production_nodes(retriever=None))

    final = app.invoke(
        create_initial_state("Semiconductor", max_candidates=2),
        config={"recursion_limit": recommended_recursion_limit(2)},
    )

    assert final["candidate_index"] == 2
    assert final["termination_reason"] == TERMINATION_LIMIT_REACHED
    assert [item["startup"] for item in final["evaluation_history"]] == [
        "Mobilint",
        "HyperAccel",
    ]
    assert all(
        item["investment_decision"] == DECISION_HOLD
        for item in final["evaluation_history"]
    )
    assert all(item["evidence_ref"]["source_ids"] for item in final["evaluation_history"])
    assert all(item["tech_category"] for item in final["evaluation_history"])
    assert "[MOCK" not in final["final_report"]
    assert "https://www.mobilint.com" in final["final_report"]
