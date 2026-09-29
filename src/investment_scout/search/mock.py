"""테스트/데모용 검색 제공자. API 키가 필요 없습니다.

여기서 나오는 모든 결과는 `is_mock=True` 로 표시되며,
URL 도 `https://mock.invalid/...` 형태의 실제로 존재하지 않는 도메인입니다.
실제 조사 결과와 혼동되지 않도록 하기 위함입니다.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from investment_scout.search.base import MODE_MOCK, SearchFailure, SearchHit, make_hit

MOCK_URL_PREFIX = "https://mock.invalid"


class MockSearchProvider:
    """미리 정해둔 결과를 돌려주는 검색 제공자.

    fixtures: {질의 부분문자열: [SearchHit, ...]}
    fail_queries: 이 부분문자열이 포함된 질의는 SearchFailure 를 던집니다.
                  (검색 장애와 자료 부족을 구분하는 테스트용)
    """

    mode = MODE_MOCK

    def __init__(
        self,
        *,
        fixtures: Optional[Dict[str, Any]] = None,
        fail_queries: Optional[List[str]] = None,
    ) -> None:
        self.fixtures: Dict[str, List[SearchHit]] = dict(fixtures or {})
        self.fail_queries = list(fail_queries or [])
        self.calls: List[str] = []

    def search(self, query: str, *, max_results: int = 5) -> List[SearchHit]:
        self.calls.append(query)

        for marker in self.fail_queries:
            if marker in query:
                raise SearchFailure(f"[MOCK] 검색 장애를 시뮬레이션했습니다 (query={query!r})")

        for marker, hits in self.fixtures.items():
            if marker in query:
                return [dict(hit) for hit in hits][:max_results]  # type: ignore[misc]

        # 결과 0건은 정상 응답입니다 (검색 장애와 구분).
        return []


def mock_hit(*, path: str, title: str, snippet: str, published_at: Optional[str] = None) -> SearchHit:
    """존재하지 않는 도메인을 쓰는 mock 검색 결과."""
    return make_hit(
        url=f"{MOCK_URL_PREFIX}/{path.lstrip('/')}",
        title=f"[MOCK] {title}",
        snippet=snippet,
        published_at=published_at,
        publisher="MOCK_PUBLISHER",
        is_mock=True,
    )
