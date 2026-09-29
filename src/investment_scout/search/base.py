"""검색 제공자 인터페이스와 오류 정의.

[원칙]
- 실제 검색 모드(live)에서 설정이 없으면 **명확한 설정 오류**를 던집니다.
- 설정 오류나 검색 장애를 숨기고 mock 으로 자동 전환하지 않습니다.
- API 키는 환경변수로만 읽고 저장소에 넣지 않습니다.
- mock 모드와 자동 테스트는 API 키가 없어도 동작합니다.
"""

from __future__ import annotations

import os
from typing import Any, Dict, List, Optional, Protocol, TypedDict, runtime_checkable

MODE_LIVE = "live"
MODE_MOCK = "mock"


class SearchConfigurationError(RuntimeError):
    """검색 설정이 없거나 잘못된 경우 (예: API 키 미설정).

    자료 부족(INSUFFICIENT_DATA)이 아니라 **시스템 오류**입니다.
    """


class SearchFailure(RuntimeError):
    """검색 호출 자체가 실패한 경우 (네트워크/레이트리밋/제공자 오류).

    "검색 결과가 0건"과는 다릅니다. 0건은 정상 응답이며 자료 부족으로 다룹니다.
    """


class SearchHit(TypedDict):
    """검색 결과 한 건 (JSON 직렬화 가능)."""

    url: str
    title: str
    snippet: str
    published_at: Optional[str]
    publisher: Optional[str]
    is_mock: bool


def make_hit(
    *,
    url: str,
    title: str,
    snippet: str,
    published_at: Optional[str] = None,
    publisher: Optional[str] = None,
    is_mock: bool = False,
) -> SearchHit:
    return SearchHit(
        url=url,
        title=title,
        snippet=snippet,
        published_at=published_at,
        publisher=publisher,
        is_mock=is_mock,
    )


@runtime_checkable
class SearchProvider(Protocol):
    """검색 제공자. 교체 가능하도록 이 인터페이스만 의존합니다."""

    mode: str

    def search(self, query: str, *, max_results: int = 5) -> List[SearchHit]:
        """질의에 대한 검색 결과를 돌려줍니다.

        raises:
            SearchConfigurationError: 설정 누락/오류
            SearchFailure: 검색 호출 실패
        """
        ...


def get_search_provider(
    mode: Optional[str] = None,
    *,
    mock_fixtures: Optional[Dict[str, Any]] = None,
) -> SearchProvider:
    """모드에 맞는 검색 제공자를 만듭니다.

    mode 가 None 이면 환경변수 SEARCH_MODE 를 보고, 그것도 없으면 mock 입니다.
    live 모드에서 설정이 없으면 SearchConfigurationError 를 던지며,
    절대 mock 으로 자동 전환하지 않습니다.
    """
    resolved = (mode or os.getenv("SEARCH_MODE") or MODE_MOCK).strip().lower()

    if resolved == MODE_MOCK:
        from investment_scout.search.mock import MockSearchProvider

        return MockSearchProvider(fixtures=mock_fixtures)

    if resolved == MODE_LIVE:
        from investment_scout.search.tavily import TavilySearchProvider

        return TavilySearchProvider()

    raise SearchConfigurationError(
        f"알 수 없는 검색 모드: {resolved!r} (사용 가능: {MODE_LIVE!r}, {MODE_MOCK!r})"
    )
