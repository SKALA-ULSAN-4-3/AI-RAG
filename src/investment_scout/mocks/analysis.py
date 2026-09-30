"""기술/시장/경쟁사 분석 노드의 mock.

실제 구현은 다른 담당자의 영역입니다. 여기서는 출력 계약만 지키는 가짜 값을 만듭니다.
"""

from __future__ import annotations

from typing import Any, Callable, Dict, Set

from investment_scout.contracts import (
    STATUS_INSUFFICIENT_DATA,
    STATUS_OK,
    make_claim,
    validate_node_update,
)
from investment_scout.state import InvestmentAgentState


def _current_name(state: Dict[str, Any]) -> str:
    return (state.get("current_startup") or {}).get("name", "UNKNOWN")


def _first_source_id(state: Dict[str, Any], name: str) -> str | None:
    sources = (state.get("source_evidence") or {}).get(name) or []
    return sources[0]["source_id"] if sources else None


def _build_result(
    *,
    field: str,
    name: str,
    state: Dict[str, Any],
    data: Dict[str, Any],
    claim_text: str,
    data_keys: list[str],
    flags: Dict[str, Set[str]],
) -> Dict[str, Any]:
    if name in flags["broken_schema_for"]:
        # 스키마 위반을 일부러 만듭니다 (status 값이 계약 밖).
        return {
            "status": "MAYBE",
            "data": data,
            "claims": [],
            "missing_information": [],
            "errors": [],
        }

    if name in flags["insufficient_for"]:
        return {
            "status": STATUS_INSUFFICIENT_DATA,
            "data": {"_mock": True},
            "claims": [],
            "missing_information": [f"{field}: [MOCK] 핵심 정보를 확보하지 못했습니다"],
            "errors": [],
        }

    if name in flags["dangling_for"]:
        source_ids = ["src_does_not_exist_999"]
    elif name in flags["unsourced_for"]:
        source_ids = []
    else:
        sid = _first_source_id(state, name)
        source_ids = [sid] if sid else []

    return {
        "status": STATUS_OK,
        "data": {**data, "_mock": True},
        "claims": [
            make_claim(
                f"{field}_claim_001",
                claim_text,
                kind="FACT",
                source_ids=source_ids,
                data_keys=data_keys,
            )
        ],
        "missing_information": [],
        "errors": [],
    }


def make_tech_node(**flags: Set[str]) -> Callable[[Dict[str, Any]], Dict[str, Any]]:
    """기술 요약 mock. 갱신 필드: tech_summary"""

    # 기술 분석 노드 (mock)
    #   읽는 필드   : current_startup, source_evidence
    #   갱신하는 필드: tech_summary
    def tech_analysis(state: InvestmentAgentState) -> Dict[str, Any]:
        """[MOCK] 기술 요약을 만들어 반환합니다."""
        name = _current_name(state)
        result = _build_result(
            field="tech_summary",
            name=name,
            state=state,
            data={
                "core_technology": "[MOCK] AI 추론 가속 NPU",
                "differentiation": "[MOCK] 전력 효율 중심 아키텍처",
                # 알 수 없는 값은 0 이 아니라 None 으로 둡니다.
                "patent_count": None,
            },
            claim_text=f"[MOCK] {name} 의 핵심 기술은 AI 추론 가속 NPU 이다",
            data_keys=["core_technology", "differentiation"],
            flags=flags,
        )
        update = {"tech_summary": result}
        return validate_node_update(
            update, allowed_fields=("tech_summary",), node_name="tech_analysis"
        )

    return tech_analysis


def make_category_node(**_flags: Set[str]) -> Callable[[Dict[str, Any]], Dict[str, Any]]:
    """기술 분류 mock. 갱신 필드: tech_category"""

    def tech_classification(state: InvestmentAgentState) -> Dict[str, Any]:
        """[MOCK] 기술 요약을 입력으로 기술 적용 분야를 분류합니다."""
        category = "[MOCK] AI_SEMICONDUCTOR" if state.get("tech_summary") else ""
        return validate_node_update(
            {"tech_category": category},
            allowed_fields=("tech_category",),
            node_name="tech_classification",
        )

    return tech_classification


def make_market_node(**flags: Set[str]) -> Callable[[Dict[str, Any]], Dict[str, Any]]:
    """시장 분석 mock. 갱신 필드: market_analysis"""

    # 시장 분석 노드 (mock)
    #   읽는 필드   : current_startup, source_evidence
    #   갱신하는 필드: market_analysis
    def market_analysis(state: InvestmentAgentState) -> Dict[str, Any]:
        """[MOCK] 시장 규모·성장률을 만들어 반환합니다."""
        name = _current_name(state)
        result = _build_result(
            field="market_analysis",
            name=name,
            state=state,
            data={
                "market_size": "[MOCK] USD 10B (2026)",
                "growth_rate": "[MOCK] 25% CAGR",
                "customer_demand": ["[MOCK] 저전력 AI 추론 수요"],
                # 확인되지 않은 매출은 0 이 아니라 None
                "company_revenue": None,
            },
            claim_text=f"[MOCK] {name} 이 속한 시장은 연 25% 성장한다",
            data_keys=["market_size", "growth_rate", "customer_demand"],
            flags=flags,
        )
        update = {"market_analysis": result}
        return validate_node_update(
            update, allowed_fields=("market_analysis",), node_name="market_analysis"
        )

    return market_analysis


def make_competitor_node(**flags: Set[str]) -> Callable[[Dict[str, Any]], Dict[str, Any]]:
    """경쟁사 분석 mock. 갱신 필드: competitor_analysis"""

    # 경쟁사 분석 노드 (mock)
    #   읽는 필드   : current_startup, source_evidence
    #   갱신하는 필드: competitor_analysis
    def competitor_analysis(state: InvestmentAgentState) -> Dict[str, Any]:
        """[MOCK] 주요 경쟁사와 경쟁 구도를 만들어 반환합니다."""
        name = _current_name(state)
        result = _build_result(
            field="competitor_analysis",
            name=name,
            state=state,
            data={
                "main_competitors": ["[MOCK] 경쟁사X", "[MOCK] 경쟁사Y"],
                "market_share": None,
            },
            claim_text=f"[MOCK] {name} 의 주요 경쟁사는 2곳이다",
            data_keys=["main_competitors"],
            flags=flags,
        )
        update = {"competitor_analysis": result}
        return validate_node_update(
            update, allowed_fields=("competitor_analysis",), node_name="competitor_analysis"
        )

    return competitor_analysis
