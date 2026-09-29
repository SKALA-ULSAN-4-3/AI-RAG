"""스타트업 적격성 판정 규칙.

[포함 조건]
1. AI 기술 또는 AI 기반 제품이 핵심 사업
2. 비상장 기업
3. 투자 단계가 Seed ~ Series C
4. Exit(인수 완료 / IPO)이 완료되지 않은 기업

[판정 값]
- ELIGIBLE            : 4개 조건을 모두 출처로 확인
- INELIGIBLE          : 조건 위반을 출처로 확인
- NEEDS_VERIFICATION  : 확인하지 못한 항목이 있어 확정할 수 없음

[중요한 보수적 규칙]
- 과거 투자 기사만으로 현재 투자 단계를 확정하지 않습니다.
  `funding_stage_confirmed_at` 이 없거나 기준일보다 오래되면 NEEDS_VERIFICATION 입니다.
- "인수 기사를 찾지 못했다"는 사실은 "Exit 이 없다"는 뜻이 아닙니다.
  `exit_status` 가 NOT_FOUND / NOT_CHECKED 이면 NEEDS_VERIFICATION 입니다.
  NONE_CONFIRMED(최신 자료로 미Exit 확인) 만 통과합니다.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any, Dict, List, Optional

ELIGIBLE = "ELIGIBLE"
INELIGIBLE = "INELIGIBLE"
NEEDS_VERIFICATION = "NEEDS_VERIFICATION"
VALID_VERDICTS = frozenset({ELIGIBLE, INELIGIBLE, NEEDS_VERIFICATION})

# 허용 투자 단계 (Seed ~ Series C)
ALLOWED_FUNDING_STAGES = (
    "PRE_SEED",
    "SEED",
    "PRE_SERIES_A",
    "SERIES_A",
    "SERIES_B",
    "SERIES_C",
)
# 명시적으로 제외하는 단계
DISALLOWED_FUNDING_STAGES = ("SERIES_D", "SERIES_E", "SERIES_F_PLUS", "PRE_IPO", "GROWTH", "NONE")

# exit_status 값
EXIT_NONE_CONFIRMED = "NONE_CONFIRMED"  # 최신 자료로 "Exit 없음"을 확인
EXIT_ACQUIRED = "ACQUIRED"
EXIT_IPO = "IPO"
EXIT_NOT_FOUND = "NOT_FOUND"  # 인수 기사를 찾지 못함 (= 확인되지 않음)
EXIT_NOT_CHECKED = "NOT_CHECKED"
# 검색에서 인수/상장 키워드를 발견했을 뿐 확정하지 못한 상태.
# 키워드 일치는 단서이지 사실이 아니므로 부적격(INELIGIBLE)이 아니라 검증대기로 보냅니다.
EXIT_SIGNAL_FOUND = "SIGNAL_FOUND"

# [가정 - ASSUMPTION] 투자 단계 확인 자료의 유효 기간.
# 팀 기준이 없어 기본값을 제안합니다. 이보다 오래된 확인은 재검증 대상입니다.
DEFAULT_STAGE_FRESHNESS_DAYS = 365

# 적격성 확정에 반드시 필요한 프로필 항목
REQUIRED_PROFILE_FIELDS = (
    "name",
    "country",
    "website",
    "is_ai_core",
    "is_public",
    "funding_stage",
    "funding_stage_confirmed_at",
    "exit_status",
)


def _parse_date(value: Optional[str]) -> Optional[date]:
    if not value or not isinstance(value, str):
        return None
    for fmt in ("%Y-%m-%d", "%Y-%m", "%Y"):
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).date()
    except ValueError:
        return None


def judge_eligibility(
    profile: Dict[str, Any],
    *,
    today: Optional[date] = None,
    stage_freshness_days: int = DEFAULT_STAGE_FRESHNESS_DAYS,
) -> Dict[str, Any]:
    """기업 프로필로 적격성을 판정합니다.

    반환값(JSON 직렬화 가능):
        {
          "verdict": "ELIGIBLE" | "INELIGIBLE" | "NEEDS_VERIFICATION",
          "reasons": [...],             # 판정 사유
          "unverified_fields": [...],   # 확인하지 못한 항목
          "criteria_used": {...},       # 판정에 사용한 기준
          "verification_scope": {...},  # 어디까지 확인했는지
        }
    """
    today = today or datetime.now(timezone.utc).date()

    reasons: List[str] = []
    unverified: List[str] = []

    criteria_used = {
        "allowed_funding_stages": list(ALLOWED_FUNDING_STAGES),
        "requires_unlisted": True,
        "requires_no_exit": True,
        "requires_ai_core_business": True,
        "stage_freshness_days": stage_freshness_days,
        "stage_freshness_is_assumption": True,
    }
    verification_scope = {
        "checked_fields": list(REQUIRED_PROFILE_FIELDS),
        "evidence_source_ids": list(profile.get("source_ids") or []),
        "data_origin": profile.get("data_origin", "UNKNOWN"),
        "note": (
            "판정은 프로필에 기록된 값과 그 출처에만 근거합니다. "
            "값이 None 이면 확인되지 않은 것으로 처리합니다."
        ),
    }

    # 1) 확인되지 않은 항목 수집 (None = 미확인)
    for field in REQUIRED_PROFILE_FIELDS:
        if profile.get(field) is None:
            unverified.append(field)

    # 2) 명확한 부적격 사유 (확인된 값으로만 판단)
    is_ineligible = False

    if profile.get("is_ai_core") is False:
        reasons.append("AI 기술/AI 기반 제품이 핵심 사업이 아님")
        is_ineligible = True

    if profile.get("is_public") is True:
        reasons.append("상장 기업이므로 제외")
        is_ineligible = True

    exit_status = profile.get("exit_status")
    if exit_status in (EXIT_ACQUIRED, EXIT_IPO):
        reasons.append(f"Exit 완료({exit_status})로 제외")
        is_ineligible = True

    stage = profile.get("funding_stage")
    if stage is not None and stage in DISALLOWED_FUNDING_STAGES:
        reasons.append(f"투자 단계 {stage} 는 Seed~Series C 범위를 벗어남")
        is_ineligible = True
    elif stage is not None and stage not in ALLOWED_FUNDING_STAGES:
        reasons.append(f"알 수 없는 투자 단계 값: {stage!r}")
        unverified.append("funding_stage")

    if is_ineligible:
        return {
            "verdict": INELIGIBLE,
            "reasons": reasons,
            "unverified_fields": sorted(set(unverified)),
            "criteria_used": criteria_used,
            "verification_scope": verification_scope,
        }

    # 3) 보수적 재검증 규칙
    if exit_status in (EXIT_NOT_FOUND, EXIT_NOT_CHECKED):
        reasons.append(
            "인수/상장 자료를 찾지 못했다는 사실만으로 Exit 없음을 확정할 수 없음 "
            f"(exit_status={exit_status})"
        )
        unverified.append("exit_status")
    elif exit_status == EXIT_SIGNAL_FOUND:
        # 검색 키워드 일치만으로는 Exit 을 확정하지도, 부정하지도 않습니다.
        reasons.append(
            "검색에서 인수/상장 신호를 발견했으나 1차 자료로 확정하지 못함 — 사람이 확인 필요 "
            f"(exit_status={exit_status})"
        )
        unverified.append("exit_status")

    confirmed_at = _parse_date(profile.get("funding_stage_confirmed_at"))
    if stage is not None:
        if confirmed_at is None:
            reasons.append("투자 단계 확인일(funding_stage_confirmed_at)이 없어 현재 단계를 확정할 수 없음")
            unverified.append("funding_stage_confirmed_at")
        elif (today - confirmed_at).days > stage_freshness_days:
            reasons.append(
                f"투자 단계 확인 자료가 {(today - confirmed_at).days}일 전으로 "
                f"기준({stage_freshness_days}일)보다 오래되어 최신 단계 재확인 필요"
            )
            unverified.append("funding_stage_confirmed_at")

    unverified = sorted(set(unverified))

    if unverified:
        return {
            "verdict": NEEDS_VERIFICATION,
            "reasons": reasons or [f"확인되지 않은 항목이 있어 확정할 수 없음: {unverified}"],
            "unverified_fields": unverified,
            "criteria_used": criteria_used,
            "verification_scope": verification_scope,
        }

    reasons.append("AI 핵심 사업 / 비상장 / Seed~Series C / Exit 미완료를 모두 출처로 확인")
    return {
        "verdict": ELIGIBLE,
        "reasons": reasons,
        "unverified_fields": [],
        "criteria_used": criteria_used,
        "verification_scope": verification_scope,
    }
