"""시장성 평가 노드 (RAG): 기술 분야 → 세부 시장 → 시장 보고서 근거로 규모·성장률·수요 정리."""

import re

from investment_scout.contracts import DEFAULT_REQUIRED_ANALYSIS_DATA, empty_analysis_result, make_claim
from investment_scout.rag.generation import (
    MARKET_FIELDS,
    MARKET_INSTRUCTIONS,
    GroundedResponse,
    OpenAIGenerator,
    analysis_status,
    grounded_result,
)

REQUIRED = tuple(DEFAULT_REQUIRED_ANALYSIS_DATA["market_analysis"])
CAGR = re.compile(r"[^.;]*(?:CAGR|compound annual growth|연평균)[^.;]*", re.I)

# 기술 분류 → 세부 시장 (data/market_sources.json의 companies). 분류가 없으면 전체 시장에서 검색.
SEGMENTS_BY_CATEGORY = {
    # 가속기·GPU·NPU는 엣지(AI_CHIP)와 데이터센터(DATACENTER_AI) 모두 검색, 제품 용도로 LLM이 선택.
    "NPU": ["AI_CHIP", "DATACENTER_AI"], "AI_ACCELERATOR": ["DATACENTER_AI", "AI_CHIP"],
    "GPU": ["DATACENTER_AI", "AI_CHIP"],
    # HBM·DRAM은 제외: HBM을 쓰는 가속기를 HBM으로 오분류하는 경우가 있어 메모리 시장으로 보내지 않음.
    "CXL": ["CXL_MEMORY"],
    "PHOTONICS": ["SILICON_PHOTONICS"],
    "IN_MEMORY_COMPUTE": ["IN_MEMORY_COMPUTE", "AI_CHIP"],
    "OTHER": ["CHIPLET"],
}
QUESTION = ("세부 시장의 시장 규모, 성장률(CAGR), 고객 수요와 수요처는? "
            "(market size, revenue forecast, CAGR growth rate, demand drivers, end users, customers)")


GENERAL_SEGMENTS = {"AI_CHIP", "DATACENTER_AI"}


def segments_for(category: str, available: set[str]) -> list[str]:
    """세부 시장 순서: 기업에 가까운 특화 시장 먼저, 범용 AI 칩(엣지·데이터센터) 시장은 뒤."""
    labels = [label.strip() for label in (category or "").split("/")]
    segments = [s for label in labels for s in SEGMENTS_BY_CATEGORY.get(label, [])]
    segments = [s for s in dict.fromkeys(segments) if s in available]
    segments.sort(key=lambda segment: segment in GENERAL_SEGMENTS)
    return segments or sorted(available)


def register_sources(state: dict, company: str, evidence: list[dict]) -> dict:
    """인용 출처 등록: 평가 저장 단계의 출처 검증(drop_unsupported_claims)을 통과하도록 기업 출처에 추가."""
    sources = {name: list(rows) for name, rows in (state.get("source_evidence") or {}).items()}
    rows = sources.setdefault(company, [])
    known = {row.get("source_id") for row in rows}
    rows.extend(item for item in evidence if item["source_id"] not in known)
    return sources


def growth_from_size_quotes(result: dict) -> dict:
    """성장률 보완: 시장 규모 인용문에 CAGR 문구가 있으면 같은 인용을 근거로 growth_rate를 채움.

    보도자료는 '규모 A→B, CAGR x%'가 한 문장이라 LLM이 규모 항목에만 넣는 경우가 많음.
    """
    if result["data"].get("growth_rate"):
        return result
    for claim in result["claims"]:
        if "market_size" not in claim.get("data_keys", []):
            continue
        for citation in claim.get("citations", []):
            match = CAGR.search(citation["quote"])
            if match:
                growth = make_claim(f"market_{len(result['claims']) + 1:03d}", match.group(0).strip(),
                                    source_ids=claim["source_ids"], data_keys=["growth_rate"])
                growth["citations"] = [citation]
                result["claims"].append(growth)
                result["data"]["growth_rate"] = growth["text"]
                result["missing_information"] = [m for m in result["missing_information"]
                                                 if "growth" not in m.lower()]
                result["status"] = analysis_status(result, REQUIRED)
                return result
    return result


class MarketAnalyst:
    def __init__(self, index, *, generator=None, min_score=0.30, top_k=10):
        self.index = index
        self.generator = generator or OpenAIGenerator(MARKET_INSTRUCTIONS)
        self.min_score = min_score
        self.top_k = top_k
        self.segments = {chunk.company for chunk in index.chunks}

    def __call__(self, state: dict) -> dict:
        company = state["current_startup"]["name"]
        segments = segments_for(state.get("tech_category", ""), self.segments)
        # 세부 시장별 균등 배분: 점수 순 통합 시 범용 시장 자료가 특화 시장을 밀어내는 문제 방지.
        per_segment = max(3, self.top_k // len(segments))
        hits = [hit for segment in segments
                for hit in self.index.search(QUESTION, company=segment, top_k=per_segment,
                                             min_score=self.min_score)]
        if not hits:
            result = empty_analysis_result(missing_information=["근거 부족: 관련 시장 자료 없음"])
            result["evidence"] = []
        else:
            # API·설정 오류는 예외로 전달: 자료 부족으로 위장하지 않음.
            products = ", ".join(state["current_startup"].get("main_products") or []) or "미상"
            question = f"{QUESTION} 세부 시장: {', '.join(segments)}. 기업 주요 제품: {products}"
            response = self.generator.generate(company=company, question=question, hits=hits,
                                               fields=MARKET_FIELDS)
            result = growth_from_size_quotes(self._ground(response, hits, segments))
            missing = [field for field in REQUIRED if not result["data"].get(field)]
            if missing:
                # 필수 항목 재요청 1회: 인용 검증 실패(페이지 경계에서 잘린 문장 등)나 누락만 다시 요청.
                retry = self.generator.generate(
                    company=company, hits=hits, fields=tuple(missing),
                    question=(f"{question}. {', '.join(missing)}만 답하라. 수치가 한 chunk 안에 온전히 적힌 "
                              f"문장(제목 포함)을 그대로 인용하라."),
                )
                merged = GroundedResponse(facts=[*response.facts, *retry.facts],
                                          missing_information=response.missing_information)
                result = growth_from_size_quotes(self._ground(merged, hits, segments))
        result["segments"] = segments
        return {"market_analysis": result,
                "source_evidence": register_sources(state, company, result["evidence"])}

    @staticmethod
    def _ground(response, hits, segments):
        return grounded_result(response, hits, company=segments, fields=MARKET_FIELDS,
                               claim_prefix="market", supports=("market_size", "growth_rate"),
                               required=REQUIRED)
