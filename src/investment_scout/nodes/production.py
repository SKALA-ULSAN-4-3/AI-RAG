"""실제 출처 기반 분석·결정·보고서 노드.

LLM이 없는 상태에서도 확인된 프로필과 근거만 사용한다. 시장 규모나 경쟁사처럼
현재 출처에 없는 정보는 만들어내지 않고 ``INSUFFICIENT_DATA`` 로 남긴다.
선택적으로 HybridEmbeddingRetriever를 주입하면 KURE/Jina 하이브리드 검색으로
각 분석에 가장 관련 있는 출처를 먼저 고른다.
"""

from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional, Sequence

from investment_scout.contracts import (
    STATUS_INSUFFICIENT_DATA,
    STATUS_OK,
    make_claim,
    validate_node_update,
)
from investment_scout.retrieval import HybridEmbeddingRetriever
from investment_scout.state import DECISION_HOLD, InvestmentAgentState, evaluation_limit


def _sources(state: Dict[str, Any]) -> List[Dict[str, Any]]:
    name = (state.get("current_startup") or {}).get("name")
    return list((state.get("source_evidence") or {}).get(name, []))


def _select_sources(
    state: Dict[str, Any],
    query: str,
    *,
    retriever: Optional[HybridEmbeddingRetriever],
    topics: Sequence[str],
) -> List[Dict[str, Any]]:
    sources = _sources(state)
    topical = [s for s in sources if set(s.get("supports") or []) & set(topics)]
    candidates = topical or sources
    if retriever is None or len(candidates) <= 1:
        return candidates
    docs = [f"{s.get('title', '')}\n{s.get('evidence', '')}" for s in candidates]
    return [
        result.metadata
        for result in retriever.search(
            query, docs, metadata=candidates, top_k=min(3, len(candidates)), strategy="hybrid"
        )
    ]


def _source_ids(sources: Sequence[Dict[str, Any]]) -> List[str]:
    return [s["source_id"] for s in sources if s.get("source_id")]


def make_production_nodes(
    *, retriever: Optional[HybridEmbeddingRetriever] = None
) -> Dict[str, Callable[[Dict[str, Any]], Dict[str, Any]]]:
    def tech_analysis(state: InvestmentAgentState) -> Dict[str, Any]:
        profile = state.get("current_startup") or {}
        name = profile.get("name", "UNKNOWN")
        products = profile.get("main_products") or []
        sources = _select_sources(
            state,
            f"{name} 핵심 기술 주요 제품 차별점 AI semiconductor products",
            retriever=retriever,
            topics=("product", "ai_core_business"),
        )
        ids = _source_ids(sources)
        if not products or not ids:
            result = {
                "status": STATUS_INSUFFICIENT_DATA,
                "data": {},
                "claims": [],
                "missing_information": ["출처로 확인된 주요 제품/핵심 기술"],
                "errors": [],
            }
        else:
            core = ", ".join(products)
            result = {
                "status": STATUS_OK,
                "data": {
                    "core_technology": core,
                    "differentiation": "확인된 제품군을 중심으로 한 AI 반도체/인프라 특화",
                },
                "claims": [
                    make_claim(
                        "tech_claim_001",
                        f"{name}의 확인된 핵심 제품은 {core}이다.",
                        source_ids=ids,
                        data_keys=["core_technology", "differentiation"],
                    )
                ],
                "missing_information": [],
                "errors": [],
            }
        return validate_node_update(
            {"tech_summary": result},
            allowed_fields=("tech_summary",),
            node_name="tech_analysis",
        )

    def tech_classification(state: InvestmentAgentState) -> Dict[str, Any]:
        profile = state.get("current_startup") or {}
        name = profile.get("name", "UNKNOWN")
        products = " ".join(profile.get("main_products") or [])
        summary = (state.get("tech_summary") or {}).get("data", {})
        text = f"{products} {summary.get('core_technology', '')}".lower()

        # 분류에 사용할 제품 근거를 하이브리드 검색으로 확인합니다. 분류값 자체는
        # 문자열 State 계약을 유지하고, 근거는 tech_summary의 claim과 source_ids에 남습니다.
        sources = _select_sources(
            state,
            f"{name} 적용 기술 분야 DRAM HBM GPU NPU PIM CXL photonics",
            retriever=retriever,
            topics=("product", "ai_core_business"),
        )
        if not sources or not text.strip():
            category = "UNCLASSIFIED"
        elif any(token in text for token in ("pim", "cim", "in-memory")):
            category = "IN_MEMORY_AI_COMPUTE"
        elif any(token in text for token in ("photon", "optical", "serdes", "interconnect")):
            category = "AI_INTERCONNECT_PHOTONICS"
        elif "gpu" in text:
            category = "GPU_ARCHITECTURE"
        elif any(token in text for token in ("npu", "accelerator", "inference")):
            category = "AI_ACCELERATOR"
        elif any(token in text for token in ("cxl", "dpu", "memory")):
            category = "AI_INFRASTRUCTURE_SEMICONDUCTOR"
        else:
            category = "AI_SEMICONDUCTOR"

        return validate_node_update(
            {"tech_category": category},
            allowed_fields=("tech_category",),
            node_name="tech_classification",
        )

    def market_analysis(state: InvestmentAgentState) -> Dict[str, Any]:
        name = (state.get("current_startup") or {}).get("name", "UNKNOWN")
        sources = _select_sources(
            state,
            f"{name} market size CAGR growth rate TAM semiconductor",
            retriever=retriever,
            topics=("market_size", "growth_rate"),
        )
        supported_topics = {topic for s in sources for topic in (s.get("supports") or [])}
        missing = [
            key for key in ("market_size", "growth_rate") if key not in supported_topics
        ]
        result = {
            "status": STATUS_INSUFFICIENT_DATA if missing else STATUS_OK,
            "data": {},
            "claims": [],
            "missing_information": [f"출처로 확인된 {key}" for key in missing],
            "errors": [],
        }
        return validate_node_update(
            {"market_analysis": result},
            allowed_fields=("market_analysis",),
            node_name="market_analysis",
        )

    def competitor_analysis(state: InvestmentAgentState) -> Dict[str, Any]:
        name = (state.get("current_startup") or {}).get("name", "UNKNOWN")
        sources = _select_sources(
            state,
            f"{name} main competitors competitive landscape",
            retriever=retriever,
            topics=("competitors",),
        )
        ids = _source_ids(sources)
        result = {
            "status": STATUS_INSUFFICIENT_DATA,
            "data": {},
            "claims": [],
            "missing_information": ["출처로 확인된 주요 경쟁사"],
            "errors": [],
        }
        # competitors topic과 실제 근거가 있을 때만 확정한다.
        if ids and any("competitors" in (s.get("supports") or []) for s in sources):
            result["missing_information"] = []
            result["status"] = STATUS_OK
        return validate_node_update(
            {"competitor_analysis": result},
            allowed_fields=("competitor_analysis",),
            node_name="competitor_analysis",
        )

    def investment_decision(state: InvestmentAgentState) -> Dict[str, Any]:
        missing_sections = [
            field
            for field in ("tech_summary", "market_analysis", "competitor_analysis")
            if (state.get(field) or {}).get("status") != STATUS_OK
        ]
        reason = (
            "실제 근거 기반 분석에서 필수 정보가 부족합니다: " + ", ".join(missing_sections)
            if missing_sections
            else "정량 투자평가 기준이 별도로 승인되지 않아 보수적으로 보류합니다."
        )
        update = {
            "investment_decision": DECISION_HOLD,
            "hold_reason": reason,
            "evaluation_scores": {"evidence_completeness": float(3 - len(missing_sections)) / 3.0},
        }
        return validate_node_update(
            update,
            allowed_fields=("investment_decision", "hold_reason", "evaluation_scores"),
            node_name="investment_decision",
        )

    def generate_report(state: InvestmentAgentState) -> Dict[str, Any]:
        history = state.get("evaluation_history") or []
        scout = state.get("scout_result") or {}
        lines = [
            f"# {state.get('target_domain', '')} 스타트업 투자 검토 보고서",
            "",
            "## 실행 요약",
            f"- 종료 사유: {state.get('termination_reason')}",
            f"- 적격 후보: {len(state.get('candidate_startups') or [])}개",
            f"- 평가 한도: {evaluation_limit(state)}개",
            f"- 실제 평가: {len(history)}개",
        ]
        counts = scout.get("counts") or {}
        for region, info in counts.items():
            lines.append(
                f"- {region}: 목표 {info['target']} / 확보 {info['secured']} / 부족 {info['shortfall']}"
            )
        lines += ["", "## 기업별 결과"]
        if not history:
            lines.append("- 평가된 기업이 없습니다.")
        else:
            lines.extend(
                [
                    "| 기업 | 결정 | 설립 | 단계 | 주요 제품 | 근거 |",
                    "| --- | --- | ---: | --- | --- | --- |",
                ]
            )
        for record in history:
            products = ", ".join(record["profile"].get("main_products") or [])
            ids = ", ".join(record.get("evidence_ref", {}).get("source_ids") or [])
            lines.append(
                "| "
                f"[{record['startup']}]({record['profile'].get('website')}) | "
                f"{record['investment_decision']} | "
                f"{record['profile'].get('founded_year')} | "
                f"{record['profile'].get('funding_stage')} | "
                f"{products} | {ids} |"
            )
        lines += [
            "",
            "## 한계",
            "- 확인되지 않은 수치나 사실은 생성하지 않았습니다.",
            "- 시장 규모·성장률·경쟁사 근거가 부족한 기업은 자동으로 HOLD 처리했습니다.",
        ]
        return validate_node_update(
            {"final_report": "\n".join(lines)},
            allowed_fields=("final_report",),
            node_name="generate_report",
        )

    return {
        "tech_node": tech_analysis,
        "category_node": tech_classification,
        "market_node": market_analysis,
        "competitor_node": competitor_analysis,
        "decision_node": investment_decision,
        "report_node": generate_report,
    }


__all__ = ["make_production_nodes"]
