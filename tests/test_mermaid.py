"""발표용 Mermaid 그래프 검증."""

from pathlib import Path

from investment_scout.graph import BUSINESS_FLOW_MERMAID, build_mock_graph


def test_documented_mermaid_matches_generated_business_flow():
    graph_path = Path(__file__).resolve().parents[1] / "docs" / "graph.mmd"

    assert graph_path.read_text(encoding="utf-8") == BUSINESS_FLOW_MERMAID
    assert "A[스타트업 탐색] --> B[기술 요약]" in BUSINESS_FLOW_MERMAID
    assert "B --> C[기술 분류]" in BUSINESS_FLOW_MERMAID
    assert "F -->|투자 추천| G[보고서 생성]" in BUSINESS_FLOW_MERMAID
    assert "A <-->|보류| F" in BUSINESS_FLOW_MERMAID


def test_actual_graph_places_classification_between_summary_and_market():
    graph = build_mock_graph(seed_profiles=[]).get_graph()
    edges = {(edge.source, edge.target) for edge in graph.edges}

    assert ("select_candidate", "tech_analysis") in edges
    assert ("tech_analysis", "tech_classification") in edges
    assert ("tech_classification", "market_analysis") in edges
    assert ("tech_analysis", "market_analysis") not in edges
