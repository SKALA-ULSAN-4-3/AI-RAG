"""Tavily 기반 실제 웹 검색 제공자.

설정 방법:
  1) `uv sync --extra live-search` 로 langchain-tavily 설치
  2) 환경변수 TAVILY_API_KEY 설정 (https://app.tavily.com 에서 발급)
  3) SEARCH_MODE=live

키가 없으면 SearchConfigurationError 를 던지며 mock 으로 전환하지 않습니다.
"""

from __future__ import annotations

import os
from typing import Any, List, Optional

from investment_scout.search.base import (
    MODE_LIVE,
    SearchConfigurationError,
    SearchFailure,
    SearchHit,
    make_hit,
)

API_KEY_ENV = "TAVILY_API_KEY"


class TavilySearchProvider:
    """실제 웹 검색. 생성 시점에 설정을 검증합니다."""

    mode = MODE_LIVE

    def __init__(self, *, api_key: Optional[str] = None, client: Any = None) -> None:
        self._client = client

        if client is not None:
            return

        key = api_key or os.getenv(API_KEY_ENV)
        if not key:
            raise SearchConfigurationError(
                f"실제 검색 모드(live)인데 {API_KEY_ENV} 환경변수가 없습니다. "
                f"키를 설정하거나 SEARCH_MODE=mock 으로 실행하세요. "
                f"(설정 오류를 mock 으로 대체하지 않습니다)"
            )

        try:
            from langchain_tavily import TavilySearch  # type: ignore import-not-found
        except ImportError as exc:
            raise SearchConfigurationError(
                "langchain-tavily 가 설치되어 있지 않습니다. "
                "`uv sync --extra live-search` 를 실행하세요."
            ) from exc

        self._client = TavilySearch(max_results=5, tavily_api_key=key)

    def search(self, query: str, *, max_results: int = 5) -> List[SearchHit]:
        try:
            raw = self._client.invoke({"query": query})
        except Exception as exc:  # 네트워크/레이트리밋/제공자 오류
            raise SearchFailure(f"Tavily 검색 실패 (query={query!r}): {exc}") from exc

        results = raw.get("results", []) if isinstance(raw, dict) else raw
        if not isinstance(results, list):
            raise SearchFailure(f"Tavily 응답 형식이 예상과 다릅니다: {type(raw).__name__}")

        hits: List[SearchHit] = []
        for item in results[:max_results]:
            if not isinstance(item, dict):
                continue
            hits.append(
                make_hit(
                    url=item.get("url", ""),
                    title=item.get("title", ""),
                    snippet=item.get("content", ""),
                    published_at=item.get("published_date"),
                    publisher=None,
                    is_mock=False,
                )
            )
        return hits
