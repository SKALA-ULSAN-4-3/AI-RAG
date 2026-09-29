"""출처(source) 레코드 관리와 근거 검증.

[원칙]
- 실제로 확인한 자료만 출처로 등록합니다.
- "URL 이 존재한다"와 "그 자료가 주장을 뒷받침한다"는 다른 사실입니다.
  전자는 `url_fetched`, 후자는 `evidence` + `supports` 로 구분해 기록합니다.
- 뒷받침 근거가 없는 사실/수치는 확정 결과와 최종 보고서에서 제외하고,
  제외 사유를 진단 기록으로 남깁니다.

[참조 규칙 - 다른 담당자와 공유]
- `source_evidence` 의 키는 **기업명**이며, `candidate_startups` /
  `candidate_profiles` / `current_startup["name"]` 과 동일한 문자열입니다.
- `source_id` 는 기업 단위로 부여합니다: `"{기업 slug}_src_001"`.
  기업이 달라도 전역에서 유일하므로 claim 의 source_ids 로 바로 참조할 수 있습니다.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from investment_scout.contracts import (
    KIND_FACT,
    find_dangling_source_ids,
)
from investment_scout.state import DIAG_DATA_GAP, DIAG_SYSTEM_ERROR, make_diagnostic

# supports 에 쓸 수 있는 주장 종류 (기업 사실 관계)
SUPPORT_TOPICS = frozenset(
    {
        "company_identity",
        "founded_year",
        "product",
        "funding_stage",
        "funding_amount",
        "public_listing",
        "exit_status",
        "ai_core_business",
        "market_size",
        "growth_rate",
        "competitors",
    }
)


class EvidenceError(ValueError):
    """출처 레코드 자체가 잘못된 경우."""


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def slugify(name: str) -> str:
    """기업명을 source_id 접두사로 쓸 수 있는 형태로 바꿉니다."""
    slug = re.sub(r"[^0-9a-zA-Z가-힣]+", "_", name.strip().lower()).strip("_")
    return slug or "unknown"


def make_source_id(startup_name: str, index: int) -> str:
    """기업 단위로 유일한 source_id 를 만듭니다."""
    return f"{slugify(startup_name)}_src_{index:03d}"


def new_source(
    *,
    source_id: str,
    url: str,
    title: str,
    publisher: str,
    evidence: str,
    supports: Sequence[str],
    published_at: Optional[str] = None,
    accessed_at: Optional[str] = None,
    url_fetched: bool = True,
    is_mock: bool = False,
) -> Dict[str, Any]:
    """출처 레코드 한 건을 만듭니다.

    published_at: 자료 게시일. 확인하지 못했으면 None (0 이나 추측값 금지).
    accessed_at:  확인일.
    url_fetched:  URL 을 실제로 열어 내용을 확인했는지 여부.
                  False 이면 "URL 이 존재한다"는 것조차 확인되지 않은 상태입니다.
    evidence:     그 자료에서 주장을 뒷받침하는 실제 인용/요약.
    supports:     이 출처가 뒷받침하는 주장 종류 목록.
    is_mock:      테스트용 가짜 출처임을 표시 (실제 조사 데이터와 구분).
    """
    unknown_topics = sorted(set(supports) - SUPPORT_TOPICS)
    if unknown_topics:
        raise EvidenceError(f"알 수 없는 supports 항목: {unknown_topics}")
    if not evidence or not evidence.strip():
        raise EvidenceError(f"{source_id}: evidence 는 비어 있을 수 없습니다")

    return {
        "source_id": source_id,
        "url": url,
        "title": title,
        "publisher": publisher,
        "published_at": published_at,
        "accessed_at": accessed_at or utc_now_iso(),
        "evidence": evidence,
        "supports": list(supports),
        "url_fetched": url_fetched,
        "is_mock": is_mock,
    }


def validate_source(source: Any, *, where: str = "source") -> None:
    if not isinstance(source, dict):
        raise EvidenceError(f"{where}: 출처는 dict 여야 합니다")
    for key in ("source_id", "url", "title", "publisher", "published_at", "accessed_at", "evidence", "supports"):
        if key not in source:
            raise EvidenceError(f"{where}: 출처에 '{key}' 가 없습니다")
    if not isinstance(source["supports"], list):
        raise EvidenceError(f"{where}: supports 는 list 여야 합니다")


def collect_source_ids(source_evidence: Dict[str, List[Dict[str, Any]]]) -> set[str]:
    """source_evidence 전체에 등록된 source_id 집합."""
    ids: set[str] = set()
    for sources in (source_evidence or {}).values():
        for source in sources or []:
            if isinstance(source, dict) and "source_id" in source:
                ids.add(source["source_id"])
    return ids


def build_source_index(source_evidence: Dict[str, List[Dict[str, Any]]]) -> Dict[str, Dict[str, Any]]:
    """source_id -> 출처 레코드 조회용 인덱스."""
    index: Dict[str, Dict[str, Any]] = {}
    for sources in (source_evidence or {}).values():
        for source in sources or []:
            if isinstance(source, dict) and "source_id" in source:
                index[source["source_id"]] = source
    return index


def claim_is_supported(
    claim: Dict[str, Any],
    source_index: Dict[str, Dict[str, Any]],
) -> Tuple[bool, str]:
    """주장이 실제로 뒷받침되는지 판정합니다.

    반환: (뒷받침됨 여부, 사유)

    FACT 는 반드시 출처가 있어야 합니다. INFERENCE 는 추론 과정(reasoning)과
    함께 근거가 된 출처를 참조해야 합니다. 어느 쪽이든 source_ids 가 비어 있으면
    뒷받침되지 않은 것으로 봅니다.
    """
    source_ids = claim.get("source_ids") or []
    if not source_ids:
        return False, "출처가 지정되지 않음"

    dangling = [sid for sid in source_ids if sid not in source_index]
    if dangling:
        return False, f"존재하지 않는 source_id 참조: {dangling}"

    # URL 존재 사실과 "그 자료가 주장을 뒷받침한다"는 사실을 구분합니다.
    confirmed = [sid for sid in source_ids if source_index[sid].get("url_fetched")]
    if not confirmed:
        return False, "참조한 출처가 모두 미확인(url_fetched=False) 상태"

    has_evidence = [sid for sid in confirmed if (source_index[sid].get("evidence") or "").strip()]
    if not has_evidence:
        return False, "참조한 출처에 주장을 뒷받침하는 인용(evidence)이 없음"

    return True, "출처 확인됨"


def drop_unsupported_claims(
    result: Dict[str, Any],
    *,
    source_evidence: Dict[str, List[Dict[str, Any]]],
    field_name: str,
    startup: Optional[str] = None,
) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    """출처가 뒷받침하지 않는 주장을 제거한 결과와 진단 기록을 반환합니다.

    원본은 수정하지 않습니다. 제거된 주장은 모두 진단 기록으로 남습니다.
    """
    import copy

    cleaned = copy.deepcopy(result)
    source_index = build_source_index(source_evidence)
    diagnostics: List[Dict[str, Any]] = []
    kept: List[Dict[str, Any]] = []

    for claim in cleaned.get("claims", []) or []:
        supported, reason = claim_is_supported(claim, source_index)
        if supported:
            kept.append(claim)
            continue

        # 존재하지 않는 source_id 참조는 자료 부족이 아니라 시스템(참조) 오류입니다.
        is_reference_error = "존재하지 않는 source_id" in reason
        diagnostics.append(
            make_diagnostic(
                kind="DANGLING_SOURCE_REFERENCE" if is_reference_error else "UNSUPPORTED_CLAIM_DROPPED",
                category=DIAG_SYSTEM_ERROR if is_reference_error else DIAG_DATA_GAP,
                message=f"{field_name} 의 주장을 확정 결과에서 제외했습니다: {reason}",
                startup=startup,
                details={
                    "field": field_name,
                    "claim_id": claim.get("claim_id"),
                    "claim_text": claim.get("text"),
                    "kind": claim.get("kind", KIND_FACT),
                    "source_ids": claim.get("source_ids", []),
                    "reason": reason,
                },
            )
        )

    cleaned["claims"] = kept

    # 제외된 주장이 뒷받침하던 data 키는 확정 결과에서도 제거합니다.
    # (출처 없는 수치가 data 에 남아 보고서로 흘러가는 것을 막습니다.)
    supported_keys = {k for claim in kept for k in (claim.get("data_keys") or [])}
    dropped_keys = {
        k
        for diag in diagnostics
        for k in _claim_data_keys(result, diag["details"].get("claim_id"))
    } - supported_keys

    data = cleaned.get("data")
    if isinstance(data, dict):
        for key in sorted(dropped_keys):
            if key in data:
                removed_value = data.pop(key)
                diagnostics.append(
                    make_diagnostic(
                        kind="UNSUPPORTED_DATA_DROPPED",
                        category=DIAG_DATA_GAP,
                        message=f"{field_name}.data['{key}'] 를 뒷받침하는 출처가 없어 확정 결과에서 제외했습니다",
                        startup=startup,
                        details={"field": field_name, "key": key, "value": removed_value},
                    )
                )
        missing = cleaned.setdefault("missing_information", [])
        for key in sorted(dropped_keys):
            note = f"{field_name}.data.{key}: 출처 미확보로 제외됨"
            if note not in missing:
                missing.append(note)

    return cleaned, diagnostics


def _claim_data_keys(result: Dict[str, Any], claim_id: Optional[str]) -> List[str]:
    """원본 결과에서 특정 claim 이 뒷받침하던 data 키 목록."""
    if not claim_id:
        return []
    for claim in result.get("claims", []) or []:
        if isinstance(claim, dict) and claim.get("claim_id") == claim_id:
            return list(claim.get("data_keys") or [])
    return []


def check_source_references(
    state_like: Dict[str, Any],
    *,
    fields: Iterable[str],
    source_evidence: Dict[str, List[Dict[str, Any]]],
    startup: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """지정한 분석 필드들에서 존재하지 않는 source_id 참조를 찾아 진단으로 반환합니다."""
    known = collect_source_ids(source_evidence)
    diagnostics: List[Dict[str, Any]] = []

    for field in fields:
        result = state_like.get(field)
        if not isinstance(result, dict) or not result:
            continue
        dangling = find_dangling_source_ids(result, known)
        if dangling:
            diagnostics.append(
                make_diagnostic(
                    kind="DANGLING_SOURCE_REFERENCE",
                    category=DIAG_SYSTEM_ERROR,
                    message=f"{field} 가 존재하지 않는 source_id 를 참조합니다",
                    startup=startup,
                    details={"field": field, "dangling_source_ids": dangling},
                )
            )

    return diagnostics
