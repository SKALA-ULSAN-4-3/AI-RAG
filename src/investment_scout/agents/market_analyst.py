"""시장성 평가 노드 (RAG): 기술 분야 → 세부 시장 → 시장 보고서 근거로 규모·성장률·수요 정리."""

from investment_scout.contracts import DEFAULT_REQUIRED_ANALYSIS_DATA, empty_analysis_result
from investment_scout.rag.generation import (
    MARKET_FIELDS,
    MARKET_INSTRUCTIONS,
    OpenAIGenerator,
    grounded_result,
)

import json
import re

from investment_scout.agents.market_rules import market_fact_supported

# 분류 실패를 전체 시장 검색으로 확대하지 않는다.
SEGMENTS_BY_CATEGORY = {
    "NPU": [], "AI_ACCELERATOR": [], "GPU": [],
    "HBM": ["HBM"], "DRAM": ["DRAM"], "EDA_PROCESS_AI": ["EDA_PROCESS_AI"],
    "CXL": ["CXL_MEMORY"],
    "PHOTONICS": ["SILICON_PHOTONICS"],
    "IN_MEMORY_COMPUTE": ["IN_MEMORY_COMPUTE"],
    "OTHER": [],
}
QUESTION = ("세부 시장의 시장 규모, 성장률(CAGR), 고객 수요와 수요처는? "
            "(market size, revenue forecast, CAGR growth rate, demand drivers, end users, customers)")


def segments_for(category: str, available: set[str], context: str = "") -> list[str]:
    """기술과 제품 사용처를 함께 확인하며, 불명확하거나 자료가 없으면 빈 목록."""
    labels = [label.strip() for label in (category or "").split("/")]
    segments = [s for label in labels for s in SEGMENTS_BY_CATEGORY.get(label, [])]
    if set(labels) & {"NPU", "AI_ACCELERATOR", "GPU"}:
        edge = bool(re.search(r"\bedge\b|on.device|엣지|온디바이스", context, re.I))
        datacenter = bool(re.search(r"data.center|datacenter|데이터.?센터", context, re.I))
        if edge != datacenter:
            specific = "EDGE_AI" if edge else "DATACENTER_AI_CHIP"
            segments.append(specific if specific in available else "AI_CHIP")
        elif "AI_CHIP" in available:
            # 기존 자료집 호환: 사용처를 하나로 특정하지 못하면 범용 AI 칩 자료만 사용한다.
            segments.append("AI_CHIP")
    if "OTHER" in labels and re.search(r"chiplet|die.to.die|칩렛|다이.?간", context, re.I):
        segments.append("CHIPLET")
    segments = [s for s in dict.fromkeys(segments) if s in available]
    return segments


def register_sources(state: dict, company: str, evidence: list[dict]) -> dict:
    """인용 출처 등록: 평가 저장 단계의 출처 검증(drop_unsupported_claims)을 통과하도록 기업 출처에 추가."""
    sources = {name: list(rows) for name, rows in (state.get("source_evidence") or {}).items()}
    rows = sources.setdefault(company, [])
    known = {row.get("source_id") for row in rows}
    rows.extend(item for item in evidence if item["source_id"] not in known)
    return sources


class MarketAnalyst:
    def __init__(self, index, *, generator=None, min_score=0.30, top_k=10):
        self.index = index
        self.generator = generator or OpenAIGenerator(MARKET_INSTRUCTIONS)
        self.min_score = min_score
        self.top_k = top_k
        self.segments = {chunk.company for chunk in index.chunks}

    def __call__(self, state: dict) -> dict:
        company = state["current_startup"]["name"]
        profile = state["current_startup"]
        context = json.dumps({"products": profile.get("main_products", []),
                              "technology": (state.get("tech_summary") or {}).get("data", {})},
                             ensure_ascii=False)
        segments = segments_for(state.get("tech_category", ""), self.segments, context)
        if not segments:
            result = empty_analysis_result(missing_information=[
                "근거 부족: 기술·제품 사용처에 맞는 시장 자료 없음 (시장 범위 확인 또는 자료 수집 필요)"])
            result.update(evidence=[], segments=[], market_context=context)
            return {"market_analysis": result,
                    "source_evidence": register_sources(state, company, [])}
        # 세부 시장별 균등 배분: 점수 순 통합 시 범용 시장 자료가 특화 시장을 밀어내는 문제 방지.
        per_segment = max(3, self.top_k // len(segments))
        query = f"{QUESTION} 대상 제품 및 사용처: {context}"
        hits = [hit for segment in segments
                for hit in self.index.search(query, company=segment, top_k=per_segment,
                                             min_score=self.min_score)]
        if not hits:
            result = empty_analysis_result(missing_information=["근거 부족: 관련 시장 자료 없음"])
            result["evidence"] = []
        else:
            # API·설정 오류는 예외로 전달: 자료 부족으로 위장하지 않음.
            response = self.generator.generate(
                company=company, question=f"{query} 세부 시장: {', '.join(segments)}",
                hits=hits, fields=MARKET_FIELDS,
            )
            accepted, rejected = [], []
            for fact in response.facts:
                quote = " ".join(c.quote for c in fact.citations)
                if market_fact_supported(fact.field, quote):
                    accepted.append(fact)
                else:
                    rejected.append(f"근거 부족: {fact.field} 인용에 필수 수치·연도 또는 수요 정보 없음")
            response = response.model_copy(update={"facts": accepted,
                "missing_information": [*response.missing_information, *rejected]})
            result = grounded_result(
                response, hits, company=segments, fields=MARKET_FIELDS, claim_prefix="market",
                supports=MARKET_FIELDS,
                required=tuple(DEFAULT_REQUIRED_ANALYSIS_DATA["market_analysis"]),
            )
        result["segments"] = segments
        result["market_context"] = context
        return {"market_analysis": result,
                "source_evidence": register_sources(state, company, result["evidence"])}
