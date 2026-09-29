"""테스트 공통 헬퍼. 네트워크와 API 키 없이 동작합니다."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

import pytest

from investment_scout.graph import (
    build_graph,
    create_initial_state,
    recommended_recursion_limit,
)
from investment_scout.agents.startup_scout import StartupScout
from investment_scout.mocks import build_mock_nodes
from investment_scout.mocks.fixtures import mock_profile, mock_seed_profiles
from investment_scout.search import MockSearchProvider


def run_graph(
    *,
    profiles: Optional[List[Dict[str, Any]]] = None,
    candidates: int = 2,
    decisions: Optional[Dict[str, str]] = None,
    max_candidates: int = 20,
    domain: str = "Semiconductor",
    **mock_kwargs: Any,
) -> Dict[str, Any]:
    """mock 노드를 주입한 실제 LangGraph 를 끝까지 실행합니다."""
    seed = profiles if profiles is not None else mock_seed_profiles(candidates)
    scout = StartupScout(search_provider=MockSearchProvider(), seed_profiles=seed)
    nodes = build_mock_nodes(decisions=decisions, **mock_kwargs)
    app = build_graph(scout=scout, **nodes)

    state = create_initial_state(domain, max_candidates=max_candidates)
    return app.invoke(
        state,
        config={"recursion_limit": recommended_recursion_limit(max(max_candidates, 1))},
    )


def names_of(count: int) -> List[str]:
    return [p["name"] for p in mock_seed_profiles(count)]


@pytest.fixture
def two_candidates() -> List[Dict[str, Any]]:
    return mock_seed_profiles(2)


@pytest.fixture
def mock_search() -> MockSearchProvider:
    return MockSearchProvider()


__all__ = ["run_graph", "names_of", "mock_profile", "mock_seed_profiles"]
