"""에이전트 출력 스키마와 런타임 검증.

TypedDict 는 정적 타입 힌트일 뿐 런타임에 검증되지 않으므로,
같은 구조에 대한 검증 함수를 함께 제공합니다.

[핵심 구분]
- `AnalysisResult` 는 **State 필드 하나의 값**입니다. 노드 반환값 전체가 아닙니다.
- 노드는 자신이 담당하는 State 필드의 업데이트만 반환합니다.

    # 올바름
    # 기술 요약 노드
    {"tech_summary": <AnalysisResult>}

    # 기술 분류 노드
    {"tech_category": "NPU"}

    # 잘못됨 (AnalysisResult 를 노드 반환값으로 그대로 사용)
    {"status": "OK", "data": {}, "claims": [], ...}
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional, Sequence, TypedDict

STATUS_OK = "OK"
STATUS_INSUFFICIENT_DATA = "INSUFFICIENT_DATA"
STATUS_ERROR = "ERROR"
VALID_STATUSES = frozenset({STATUS_OK, STATUS_INSUFFICIENT_DATA, STATUS_ERROR})

KIND_FACT = "FACT"
KIND_INFERENCE = "INFERENCE"
VALID_CLAIM_KINDS = frozenset({KIND_FACT, KIND_INFERENCE})


class SchemaViolationError(ValueError):
    """에이전트 출력이 계약을 위반했을 때 발생합니다.

    자료 부족(INSUFFICIENT_DATA)과 구분되는 **시스템 오류**입니다.
    """


class Claim(TypedDict, total=False):
    claim_id: str
    text: str
    kind: str  # FACT | INFERENCE
    source_ids: List[str]
    # kind == INFERENCE 일 때 필수: 근거에서 결론에 이른 추론 과정
    reasoning: str
    # 수치 계산을 제공할 때 사용: 입력값의 출처와 계산 방법을 함께 보존
    computation: Dict[str, Any]
    # 이 주장이 뒷받침하는 data 의 키 목록.
    # 주장이 출처 부족으로 제외되면 여기 적힌 data 키도 확정 결과에서 제거됩니다.
    data_keys: List[str]


class AnalysisResult(TypedDict):
    status: str  # OK | INSUFFICIENT_DATA | ERROR
    data: Dict[str, Any]
    claims: List[Claim]
    missing_information: List[str]
    errors: List[Dict[str, Any]]


def empty_analysis_result(
    *,
    status: str = STATUS_INSUFFICIENT_DATA,
    missing_information: Optional[Sequence[str]] = None,
    errors: Optional[Sequence[Dict[str, Any]]] = None,
) -> AnalysisResult:
    """기본값으로 채운 분석 결과."""
    return AnalysisResult(
        status=status,
        data={},
        claims=[],
        missing_information=list(missing_information or []),
        errors=list(errors or []),
    )


def make_claim(
    claim_id: str,
    text: str,
    *,
    kind: str = KIND_FACT,
    source_ids: Optional[Sequence[str]] = None,
    reasoning: Optional[str] = None,
    computation: Optional[Dict[str, Any]] = None,
    data_keys: Optional[Sequence[str]] = None,
) -> Claim:
    """주장 한 건을 만듭니다."""
    claim: Claim = {
        "claim_id": claim_id,
        "text": text,
        "kind": kind,
        "source_ids": list(source_ids or []),
    }
    if reasoning is not None:
        claim["reasoning"] = reasoning
    if computation is not None:
        claim["computation"] = computation
    if data_keys is not None:
        claim["data_keys"] = list(data_keys)
    return claim


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise SchemaViolationError(message)


def validate_claim(claim: Any, *, where: str) -> None:
    """주장 한 건의 구조를 검증합니다."""
    _require(isinstance(claim, dict), f"{where}: claim 은 dict 여야 합니다 (got {type(claim).__name__})")

    for key in ("claim_id", "text", "kind"):
        _require(key in claim, f"{where}: claim 에 '{key}' 가 없습니다")
        _require(isinstance(claim[key], str), f"{where}: claim['{key}'] 은 str 이어야 합니다")

    _require(
        claim["kind"] in VALID_CLAIM_KINDS,
        f"{where}: claim['kind'] 은 {sorted(VALID_CLAIM_KINDS)} 중 하나여야 합니다 (got {claim['kind']!r})",
    )

    _require("source_ids" in claim, f"{where}: claim 에 'source_ids' 가 없습니다")
    source_ids = claim["source_ids"]
    _require(isinstance(source_ids, list), f"{where}: claim['source_ids'] 는 list 여야 합니다")
    for sid in source_ids:
        _require(isinstance(sid, str), f"{where}: source_ids 의 항목은 str 이어야 합니다 (got {sid!r})")

    # 추론은 근거(source_ids)와 추론 과정(reasoning)을 구분해서 표현해야 합니다.
    if claim["kind"] == KIND_INFERENCE:
        _require(
            isinstance(claim.get("reasoning"), str) and claim["reasoning"].strip(),
            f"{where}: kind=INFERENCE 인 claim 은 비어있지 않은 'reasoning' 이 필요합니다",
        )

    if "data_keys" in claim:
        _require(
            isinstance(claim["data_keys"], list)
            and all(isinstance(k, str) for k in claim["data_keys"]),
            f"{where}: claim['data_keys'] 는 str 의 list 여야 합니다",
        )

    # 수치 계산을 제공한다면 입력값의 출처와 계산 방법을 함께 보존해야 합니다.
    if "computation" in claim:
        comp = claim["computation"]
        _require(isinstance(comp, dict), f"{where}: claim['computation'] 은 dict 여야 합니다")
        _require(
            isinstance(comp.get("method"), str) and comp["method"].strip(),
            f"{where}: computation 에 'method'(계산 방법) 문자열이 필요합니다",
        )
        inputs = comp.get("inputs")
        _require(isinstance(inputs, list), f"{where}: computation['inputs'] 는 list 여야 합니다")
        for item in inputs:
            _require(isinstance(item, dict), f"{where}: computation['inputs'] 항목은 dict 여야 합니다")
            _require("name" in item and "value" in item, f"{where}: computation 입력값에 name/value 가 필요합니다")
            _require(
                isinstance(item.get("source_ids"), list),
                f"{where}: computation 입력값 {item.get('name')!r} 에 source_ids(list) 가 필요합니다",
            )


def validate_analysis_result(
    result: Any,
    *,
    field_name: str,
    known_source_ids: Optional[Iterable[str]] = None,
) -> None:
    """분석 결과(State 필드 하나의 값)의 구조를 검증합니다.

    known_source_ids 를 주면 존재하지 않는 source_id 참조도 함께 검사합니다.
    위반 시 SchemaViolationError 를 던집니다.
    """
    where = f"{field_name}"
    _require(isinstance(result, dict), f"{where}: 분석 결과는 dict 여야 합니다 (got {type(result).__name__})")

    for key in ("status", "data", "claims", "missing_information", "errors"):
        _require(key in result, f"{where}: 필수 키 '{key}' 가 없습니다")

    _require(
        result["status"] in VALID_STATUSES,
        f"{where}: status 는 {sorted(VALID_STATUSES)} 중 하나여야 합니다 (got {result['status']!r})",
    )
    _require(isinstance(result["data"], dict), f"{where}: data 는 dict 여야 합니다")
    _require(isinstance(result["claims"], list), f"{where}: claims 는 list 여야 합니다")
    _require(isinstance(result["missing_information"], list), f"{where}: missing_information 은 list 여야 합니다")
    _require(isinstance(result["errors"], list), f"{where}: errors 는 list 여야 합니다")

    seen_ids: set[str] = set()
    for index, claim in enumerate(result["claims"]):
        claim_where = f"{where}.claims[{index}]"
        validate_claim(claim, where=claim_where)
        _require(
            claim["claim_id"] not in seen_ids,
            f"{claim_where}: claim_id 가 중복되었습니다 ({claim['claim_id']!r})",
        )
        seen_ids.add(claim["claim_id"])

    if known_source_ids is not None:
        known = set(known_source_ids)
        dangling = find_dangling_source_ids(result, known)
        _require(
            not dangling,
            f"{where}: 존재하지 않는 source_id 를 참조합니다: {sorted(dangling)}",
        )

    # JSON 직렬화 가능해야 합니다.
    _require(is_json_serializable(result), f"{where}: JSON 직렬화가 불가능한 값이 포함되어 있습니다")


def find_dangling_source_ids(result: Dict[str, Any], known_source_ids: Iterable[str]) -> List[str]:
    """분석 결과가 참조하는 source_id 중 실제로 존재하지 않는 것들을 찾습니다."""
    known = set(known_source_ids)
    dangling: set[str] = set()

    for claim in result.get("claims", []):
        if not isinstance(claim, dict):
            continue
        for sid in claim.get("source_ids", []) or []:
            if sid not in known:
                dangling.add(sid)
        comp = claim.get("computation")
        if isinstance(comp, dict):
            for item in comp.get("inputs", []) or []:
                if isinstance(item, dict):
                    for sid in item.get("source_ids", []) or []:
                        if sid not in known:
                            dangling.add(sid)

    return sorted(dangling)


def is_json_serializable(value: Any) -> bool:
    import json

    try:
        json.dumps(value, ensure_ascii=False)
    except (TypeError, ValueError):
        return False
    return True


def validate_node_update(
    update: Any,
    *,
    allowed_fields: Iterable[str],
    node_name: str,
) -> Dict[str, Any]:
    """노드 반환값이 담당 State 필드만 갱신하는지 검증합니다.

    분석 결과(AnalysisResult)를 노드 반환값으로 그대로 쓰는 실수를 여기서 잡습니다.
    """
    _require(
        isinstance(update, dict),
        f"{node_name}: 노드 반환값은 dict 여야 합니다 (got {type(update).__name__})",
    )

    # AnalysisResult 를 그대로 반환한 경우를 구체적으로 안내
    if {"status", "data", "claims"} <= set(update):
        raise SchemaViolationError(
            f"{node_name}: 분석 결과(AnalysisResult)를 노드 반환값으로 그대로 반환했습니다. "
            f"담당 State 필드에 담아 반환해야 합니다. "
            f'예: {{"tech_summary": <AnalysisResult>}}'
        )

    allowed = set(allowed_fields)
    unexpected = sorted(set(update) - allowed)
    _require(
        not unexpected,
        f"{node_name}: 담당이 아닌 State 필드를 갱신하려 합니다: {unexpected} (허용: {sorted(allowed)})",
    )
    return update


# --------------------------------------------------------------------------
# 평가 필수 정보 기준
# --------------------------------------------------------------------------
# [가정 - ASSUMPTION]
# 팀에서 합의된 "평가 필수 정보" 기준이 아직 없습니다. 아래는 설정 가능한
# 기본 기준의 초안이며, 팀 확인 전까지는 가정입니다.
# 각 분석 필드의 data 안에 아래 키가 존재하고 값이 None 이 아니어야
# 해당 분석을 "평가 가능"으로 봅니다.
DEFAULT_REQUIRED_ANALYSIS_DATA: Dict[str, List[str]] = {
    # 팀 결정: 차별성은 점수표(경쟁 우위·제품/기술력)에서 평가하므로 필수 정보는 핵심 기술만.
    "tech_summary": ["core_technology"],
    "market_analysis": ["market_size", "growth_rate"],
    "competitor_analysis": ["main_competitors"],
}

REQUIRED_CRITERIA_IS_ASSUMPTION = True


def find_missing_core_information(
    state: Dict[str, Any],
    *,
    required: Optional[Dict[str, List[str]]] = None,
) -> Dict[str, List[str]]:
    """평가에 필수인 정보 중 빠진 것을 필드별로 찾습니다.

    - 분석 결과의 status 가 OK 가 아니면 그 자체로 정보 부족입니다.
    - status 가 OK 여도 필수 data 키가 없거나 None 이면 정보 부족입니다.
    """
    required = required or DEFAULT_REQUIRED_ANALYSIS_DATA
    missing: Dict[str, List[str]] = {}

    for field, keys in required.items():
        result = state.get(field) or {}
        if not isinstance(result, dict) or not result:
            missing[field] = [f"{field} 분석 결과 없음"]
            continue

        status = result.get("status")
        if status == STATUS_ERROR:
            missing[field] = [f"{field} 분석이 ERROR 로 종료됨"]
            continue
        if status == STATUS_INSUFFICIENT_DATA:
            reasons = list(result.get("missing_information") or [])
            missing[field] = reasons or [f"{field} 가 INSUFFICIENT_DATA 로 보고됨"]
            continue

        data = result.get("data") or {}
        gaps = [key for key in keys if data.get(key) is None]
        if gaps:
            missing[field] = [f"{field}.data.{key} 없음" for key in gaps]

    return missing
