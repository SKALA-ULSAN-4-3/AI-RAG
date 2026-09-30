"""역할 3 -> 역할 4 입력 계약과 현재 State 호환 어댑터.

역할 4는 점수나 투자 판단을 만들지 않습니다. 역할 3이 계산한 결과를 검산하고,
보고서 표시용 정규형으로 변환합니다. 독립 ``role3_handoff`` JSON과 현재
``evaluation_history`` State를 모두 지원하므로 두 역할의 병합 시점을 분리할 수 있습니다.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from investment_scout.state import DECISION_HOLD, DECISION_RECOMMENDED

REPORT_SCHEMA_VERSION = 1
SCORE_LIMITS = {
    "market": ("시장성", 25.0),
    "technology": ("제품/기술력", 30.0),
    "competitive_advantage": ("경쟁 우위", 20.0),
    "growth": ("성장가능성", 15.0),
    "deal_terms": ("투자조건", 10.0),
}
SCORE_ALIASES = {
    "시장성": "market",
    "제품/기술력": "technology",
    "기술력": "technology",
    "competition": "competitive_advantage",  # 역할 3 현재 키
    "경쟁 우위": "competitive_advantage",
    "경쟁우위": "competitive_advantage",
    "성장가능성": "growth",
    "deal": "deal_terms",                    # 역할 3 현재 키
    "투자조건": "deal_terms",
}


class Role3HandoffError(ValueError):
    """역할 3 전달값이 보고서 계약과 맞지 않을 때 발생합니다."""


def _text(value: Any, default: str = "근거 미제공") -> str:
    if value is None:
        return default
    if isinstance(value, str):
        return value.strip() or default
    if isinstance(value, (list, tuple)):
        joined = ", ".join(str(item) for item in value if str(item).strip())
        return joined or default
    return str(value)


def _source_ids(item: Any) -> list[str]:
    if not isinstance(item, dict):
        return []
    ids = item.get("source_ids", [])
    if not isinstance(ids, list) or not all(isinstance(value, str) for value in ids):
        raise Role3HandoffError("source_ids는 문자열 리스트여야 합니다.")
    return list(dict.fromkeys(ids))


def _scorecard(raw: Any) -> tuple[list[dict], float, set[str]]:
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise Role3HandoffError("scorecard는 객체여야 합니다.")
    canonical = {}
    for supplied_key, value in raw.items():
        key = SCORE_ALIASES.get(supplied_key, supplied_key)
        # total/risk_penalty 및 역할 3의 세부항목 점수는 표의 분야 소계가 아닙니다.
        if key not in SCORE_LIMITS:
            continue
        if key in canonical:
            raise Role3HandoffError(f"scorecard 항목이 중복되었습니다: {key}")
        canonical[key] = value

    rows, subtotal, used = [], 0.0, set()
    for key, (label, maximum) in SCORE_LIMITS.items():
        value = canonical.get(key)
        if value is None:
            rows.append({"key": key, "label": label, "score": None, "max_score": maximum,
                         "reason": "역할 3 결과 미제공", "source_ids": []})
            continue
        item = {"score": value} if isinstance(value, (int, float)) and not isinstance(value, bool) else value
        if not isinstance(item, dict):
            raise Role3HandoffError(f"scorecard.{key}는 숫자 또는 객체여야 합니다.")
        score = item.get("score")
        supplied_maximum = float(item.get("max_score", maximum))
        if supplied_maximum != maximum:
            raise Role3HandoffError(f"scorecard.{key}.max_score는 {maximum:g}여야 합니다.")
        if isinstance(score, bool) or not isinstance(score, (int, float)):
            raise Role3HandoffError(f"scorecard.{key}.score는 숫자여야 합니다.")
        score = float(score)
        if not 0 <= score <= maximum:
            raise Role3HandoffError(f"scorecard.{key}.score는 0~{maximum:g} 범위여야 합니다.")
        ids = _source_ids(item)
        rows.append({"key": key, "label": label, "score": score, "max_score": maximum,
                     "reason": _text(item.get("reason")), "source_ids": ids})
        subtotal += score
        used.update(ids)
    return rows, subtotal, used


def _risks(raw: Any) -> tuple[list[dict], float, set[str]]:
    if raw is None:
        raw = []
    if not isinstance(raw, list):
        raise Role3HandoffError("risks는 리스트여야 합니다.")
    normalized, total_penalty, used = [], 0.0, set()
    for index, item in enumerate(raw):
        if not isinstance(item, dict):
            raise Role3HandoffError(f"risks[{index}]는 객체여야 합니다.")
        fatal = bool(item.get("fatal", item.get("severity") == "fatal"))
        penalty = item.get("penalty", -10 if fatal else 0)
        if isinstance(penalty, bool) or not isinstance(penalty, (int, float)) or penalty > 0:
            raise Role3HandoffError(f"risks[{index}].penalty는 0 이하 숫자여야 합니다.")
        ids = _source_ids(item)
        normalized.append({
            "type": _text(item.get("type"), "일반"),
            "description": _text(item.get("description")),
            "fatal": fatal,
            "penalty": float(penalty),
            "mitigation": _text(item.get("mitigation"), "대응 방안 미제공"),
            "source_ids": ids,
        })
        total_penalty += float(penalty)
        used.update(ids)
    return normalized, total_penalty, used


def _references(raw: Any) -> dict[str, dict]:
    if raw is None:
        raw = []
    if not isinstance(raw, list):
        raise Role3HandoffError("references는 리스트여야 합니다.")
    result = {}
    for index, item in enumerate(raw):
        if not isinstance(item, dict) or not isinstance(item.get("source_id"), str):
            raise Role3HandoffError(f"references[{index}]에 source_id 문자열이 필요합니다.")
        if item["source_id"] in result:
            raise Role3HandoffError(f"reference source_id가 중복되었습니다: {item['source_id']}")
        if not item.get("url"):
            raise Role3HandoffError(f"reference {item['source_id']}에 url이 필요합니다.")
        result[item["source_id"]] = deepcopy(item)
    return result


def _state_sources(state: dict) -> list[dict]:
    # 시장 보고서 청크는 세부 시장 단위라 같은 출처가 여러 기업에 등록됨: 같은 source_id는 하나로 병합.
    sources: dict[str, dict] = {}
    for items in (state.get("source_evidence") or {}).values():
        for item in items if isinstance(items, list) else []:
            if not isinstance(item, dict):
                continue
            known = sources.get(item.get("source_id"))
            if known is not None and known.get("url") != item.get("url"):
                raise Role3HandoffError(f"같은 source_id에 서로 다른 출처가 있습니다: {item.get('source_id')}")
            sources.setdefault(item.get("source_id"), item)
    return list(sources.values())


def _select_record(state: dict) -> dict:
    history = state.get("evaluation_history") or []
    if not history:
        return {}
    selected = state.get("recommended_startup")
    if not selected and state.get("final_ranking"):
        selected = state["final_ranking"][0].get("startup")
    return next((record for record in history if record.get("startup") == selected), history[-1])


def _claim_ids(result: Any) -> list[str]:
    ids = []
    if isinstance(result, dict):
        for claim in result.get("claims", []):
            if isinstance(claim, dict):
                ids.extend(value for value in claim.get("source_ids", []) if isinstance(value, str))
    return list(dict.fromkeys(ids))


def _claim_text(result: Any) -> str:
    if not isinstance(result, dict):
        return "근거 미제공"
    claims = [item.get("text", "") for item in result.get("claims", []) if isinstance(item, dict)]
    return " ".join(value for value in claims if value) or "근거 미제공"


def _evidence_id_map(record: dict) -> dict[str, list[str]]:
    """역할 3 Judge의 evidence ID를 최종 source_evidence ID로 변환합니다."""
    mapping = {}
    for field in ("tech_summary", "market_analysis", "competitor_analysis"):
        result = record.get(field) or {}
        for claim in result.get("claims", []):
            mapping[f"{field}:{claim.get('claim_id')}"] = list(claim.get("source_ids") or [])
    profile = record.get("profile") or {}
    for source_id in profile.get("source_ids") or []:
        mapping[f"profile:{source_id}"] = [source_id]
    mapping["profile:facts"] = list(profile.get("source_ids") or [])
    return mapping


def _resolve_evidence_ids(values: list[str], mapping: dict[str, list[str]]) -> list[str]:
    result = []
    for value in values:
        result.extend(mapping.get(value, [value] if ":" not in value else []))
    return list(dict.fromkeys(result))


def _state_scorecard(record: dict) -> dict:
    scores = record.get("evaluation_scores") or {}
    details = record.get("evaluation_details") or {}
    mapping = _evidence_id_map(record)
    result = {}
    for group, score in (details.get("groups") or {}).items():
        key = SCORE_ALIASES.get(group, group)
        if key not in SCORE_LIMITS:
            continue
        related = [item for item in details.get("items", []) if item.get("group") == group]
        evidence = _resolve_evidence_ids(
            [value for item in related for value in item.get("evidence_ids", [])], mapping
        )
        reasons = [item.get("rationale") for item in related if item.get("rationale")]
        result[key] = {"score": score, "reason": "; ".join(reasons), "source_ids": evidence}
    if not result:
        for key, value in scores.items():
            canonical = SCORE_ALIASES.get(key, key)
            if canonical in SCORE_LIMITS:
                result[canonical] = value
    return result


def _state_risks(record: dict) -> list[dict]:
    details = record.get("evaluation_details") or {}
    mapping = _evidence_id_map(record)
    penalized = set(details.get("penalized_risk_types") or [])
    already_penalized = set()
    result = []
    for risk in details.get("risks", []):
        kind = risk.get("type", "일반")
        fatal = bool(risk.get("fatal"))
        penalty = -10 if fatal and kind in penalized and kind not in already_penalized else 0
        if penalty:
            already_penalized.add(kind)
        result.append({
            "type": kind,
            "description": risk.get("description"),
            "fatal": fatal,
            "penalty": penalty,
            "source_ids": _resolve_evidence_ids(risk.get("evidence_ids") or [], mapping),
        })
    return result


def _state_growth(record: dict) -> list[dict]:
    details = record.get("evaluation_details") or {}
    mapping = _evidence_id_map(record)
    return [
        {
            "description": item.get("rationale") or "성장 근거 미제공",
            "source_ids": _resolve_evidence_ids(item.get("evidence_ids") or [], mapping),
        }
        for item in details.get("items", [])
        if item.get("group") == "growth"
    ]


def _handoff_from_state(state: dict) -> dict:
    record = _select_record(state)
    profile = record.get("profile") or state.get("current_startup") or {}
    market = record.get("market_analysis") or state.get("market_analysis") or {}
    technology = record.get("tech_summary") or state.get("tech_summary") or {}
    competition = record.get("competitor_analysis") or state.get("competitor_analysis") or {}
    ranking = state.get("final_ranking") or [
        {"startup": item.get("startup"), "total": (item.get("evaluation_scores") or {}).get("total"),
         "qualified": item.get("investment_decision") == DECISION_RECOMMENDED}
        for item in state.get("evaluation_history", [])
    ]
    scorecard = _state_scorecard(record)
    risks = _state_risks(record)
    summary = (
        f"{profile.get('name', '선정 기업 없음')}에 대해 총 {len(state.get('evaluation_history', []))}개 후보를 "
        f"평가했습니다. 최종 판단은 {record.get('investment_decision') or DECISION_HOLD}이며, "
        f"상세 근거는 점수표와 리스크 항목에 제시합니다."
    )
    return {
        "schema_version": REPORT_SCHEMA_VERSION,
        "title": f"{state.get('target_domain', '반도체')} 스타트업 투자 검토 보고서",
        "selected_company": {**profile, "tech_category": record.get("tech_category")},
        "evaluated_companies": ranking,
        "market": {**(market.get("data") or {}), "summary": _claim_text(market),
                   "source_ids": _claim_ids(market)},
        "technology": {**(technology.get("data") or {}), "summary": _claim_text(technology),
                       "source_ids": _claim_ids(technology)},
        "competition": {**(competition.get("data") or {}), "summary": _claim_text(competition),
                        "source_ids": _claim_ids(competition)},
        "scorecard": scorecard,
        "risks": risks,
        "growth_outlook": _state_growth(record),
        "decision": record.get("investment_decision") or DECISION_HOLD,
        "decision_reason": record.get("hold_reason") or "역할 3 판단 근거 미제공",
        "total_score": (record.get("evaluation_scores") or {}).get("total"),
        "references": _state_sources(state),
        "summary": summary,
        "termination_reason": state.get("termination_reason", ""),
    }


def normalize_report_input(payload: dict) -> dict:
    """독립 역할 3 계약 또는 전체 LangGraph State를 PDF 정규형으로 변환합니다."""
    if not isinstance(payload, dict):
        raise Role3HandoffError("보고서 입력은 객체여야 합니다.")
    if isinstance(payload.get("role3_handoff"), dict) and payload["role3_handoff"]:
        handoff = deepcopy(payload["role3_handoff"])
        handoff.setdefault("references", _state_sources(payload))
    elif "selected_company" in payload or "scorecard" in payload:
        handoff = deepcopy(payload)
    else:
        handoff = _handoff_from_state(payload)

    if handoff.get("schema_version", REPORT_SCHEMA_VERSION) != REPORT_SCHEMA_VERSION:
        raise Role3HandoffError("지원하지 않는 role3_handoff schema_version입니다.")
    decision = handoff.get("decision", DECISION_HOLD)
    if decision not in {DECISION_RECOMMENDED, DECISION_HOLD}:
        raise Role3HandoffError("decision은 RECOMMENDED 또는 HOLD여야 합니다.")

    scorecard, subtotal, score_sources = _scorecard(handoff.get("scorecard"))
    risks, penalty, risk_sources = _risks(handoff.get("risks"))
    total = subtotal + penalty
    supplied_total = handoff.get("total_score")
    if supplied_total is not None:
        if isinstance(supplied_total, bool) or not isinstance(supplied_total, (int, float)):
            raise Role3HandoffError("total_score는 숫자여야 합니다.")
        if abs(float(supplied_total) - total) > 1e-6:
            raise Role3HandoffError(
                f"total_score가 재계산값과 다릅니다: 제공={supplied_total}, 재계산={total}"
            )

    used = score_sources | risk_sources
    for key in ("market", "technology", "competition"):
        used.update(_source_ids(handoff.get(key) or {}))
    growth = handoff.get("growth_outlook") or []
    if not isinstance(growth, list):
        raise Role3HandoffError("growth_outlook은 리스트여야 합니다.")
    for item in growth:
        used.update(_source_ids(item))
    references = _references(handoff.get("references"))
    missing = sorted(used - set(references))
    if missing:
        raise Role3HandoffError(f"사용된 source_id의 reference가 없습니다: {missing}")

    company = handoff.get("selected_company") or {}
    if not isinstance(company, dict):
        raise Role3HandoffError("selected_company는 객체여야 합니다.")
    return {
        "schema_version": REPORT_SCHEMA_VERSION,
        "title": _text(handoff.get("title"), "반도체 스타트업 투자 검토 보고서"),
        "is_demo": bool(handoff.get("is_demo")),
        "company": {**company, "name": company.get("name") or "선정 기업 없음"},
        "evaluated_companies": handoff.get("evaluated_companies") or [],
        "market": handoff.get("market") or {},
        "technology": handoff.get("technology") or {},
        "competition": handoff.get("competition") or {},
        "scorecard": scorecard,
        "score_subtotal": subtotal,
        "risk_penalty": penalty,
        "total_score": total,
        "risks": risks,
        "growth_outlook": growth,
        "decision": decision,
        "decision_reason": _text(handoff.get("decision_reason")),
        "summary": _text(handoff.get("summary")),
        "references": [references[source_id] for source_id in sorted(used)],
        "used_source_ids": sorted(used),
        "termination_reason": handoff.get("termination_reason", ""),
    }
