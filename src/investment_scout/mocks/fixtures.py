"""테스트/데모용 가짜 후보 프로필.

[경고] 실제 기업이 아닙니다. 기업명에 `[TEST]` 접두사가 붙고,
출처는 `is_mock=True` 이며 URL 도메인은 존재하지 않는 `mock.invalid` 입니다.
실제 조사 데이터(`data/candidates_verified.json`)와 절대 섞지 마세요.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any, Dict, List, Optional, Sequence

from investment_scout.eligibility import EXIT_NONE_CONFIRMED
from investment_scout.evidence import make_source_id, new_source
from investment_scout.search.mock import MOCK_URL_PREFIX

TEST_PREFIX = "[TEST]"


def recent_date(days_ago: int = 30) -> str:
    return (date.today() - timedelta(days=days_ago)).isoformat()


def mock_source(startup: str, index: int = 1, *, supports: Sequence[str] = ("funding_stage",)) -> Dict[str, Any]:
    """테스트용 가짜 출처 한 건."""
    return new_source(
        source_id=make_source_id(startup, index),
        url=f"{MOCK_URL_PREFIX}/{startup}/funding",
        title=f"[MOCK] {startup} 투자 유치 자료",
        publisher="MOCK_PUBLISHER",
        published_at=recent_date(30),
        accessed_at=recent_date(0),
        evidence=f"[MOCK] {startup} 는 최근 시리즈 A 라운드를 마감했다고 보도되었다.",
        supports=list(supports),
        url_fetched=True,
        is_mock=True,
    )


def mock_profile(
    name: str,
    *,
    region: str = "KR",
    country: str = "KR",
    funding_stage: str = "SERIES_A",
    is_public: bool = False,
    is_ai_core: bool = True,
    exit_status: str = EXIT_NONE_CONFIRMED,
    stage_confirmed_days_ago: int = 30,
    website: Optional[str] = None,
    aliases: Optional[List[str]] = None,
    with_source: bool = True,
) -> Dict[str, Any]:
    """적격성 판정을 통과하도록 채워진 가짜 프로필."""
    full_name = name if name.startswith(TEST_PREFIX) else f"{TEST_PREFIX}{name}"
    slug = full_name.replace(TEST_PREFIX, "").strip().lower().replace(" ", "-")
    # 도메인 기준 중복 제거에 걸리지 않도록 기업마다 호스트를 다르게 둡니다.
    default_website = f"https://{slug}.mock.invalid"

    profile: Dict[str, Any] = {
        "name": full_name,
        "aliases": aliases or [],
        "country": country,
        "region": region,
        "website": website or default_website,
        "website_verified": False,
        "founded_year": 2020,
        "main_products": ["[MOCK] AI 추론 가속기"],
        "funding_stage": funding_stage,
        "funding_stage_confirmed_at": recent_date(stage_confirmed_days_ago),
        "is_public": is_public,
        "exit_status": exit_status,
        "is_ai_core": is_ai_core,
        "data_origin": "MOCK_TEST_DATA",
    }
    if with_source:
        profile["sources"] = [mock_source(full_name)]
    return profile


def mock_seed_profiles(count: int = 2, **kwargs: Any) -> List[Dict[str, Any]]:
    """`[TEST]테스트기업A`, `[TEST]테스트기업B`, ... 형태의 가짜 후보 목록."""
    letters = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    if count > len(letters):
        names = [f"테스트기업{i + 1:02d}" for i in range(count)]
    else:
        names = [f"테스트기업{letters[i]}" for i in range(count)]
    return [mock_profile(name, **kwargs) for name in names]
