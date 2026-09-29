"""교체 가능한 웹 검색 인터페이스."""

from investment_scout.search.base import (
    SearchConfigurationError,
    SearchFailure,
    SearchHit,
    SearchProvider,
    get_search_provider,
)
from investment_scout.search.mock import MockSearchProvider

__all__ = [
    "SearchConfigurationError",
    "SearchFailure",
    "SearchHit",
    "SearchProvider",
    "MockSearchProvider",
    "get_search_provider",
]
