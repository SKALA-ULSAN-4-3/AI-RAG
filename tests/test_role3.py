"""역할 3 검증: 설계서 평가표 일치, 코드 채점 규칙, 시장·경쟁 노드의 근거 등록 (API 호출 없음)."""

import pytest

from investment_scout.agents.competitor import CompetitorAnalyst
from investment_scout.agents.investment_judge import (
    SCORECARD,
    InvestmentJudge,
    ItemScore,
    Judgement,
    Risk,
    score_judgement,
)
from investment_scout.agents.market_analyst import MarketAnalyst, segments_for
from investment_scout.evidence import drop_unsupported_claims, validate_source
from investment_scout.rag.chunking import Chunk
from investment_scout.rag.generation import Citation, Fact, GroundedResponse
from investment_scout.rag.index import SearchHit

# RAG-Design_울산-4반 "투자판단 기준" Score Table·Checklist 원문 그대로
DESIGN = {
    "시장성 (Opportunity Size)": (25, [("이 시장은 얼마나 큰가?", 10),
                                   ("고객이 실제로 이 제품에 비용을 지불할 이유가 있는가?", 10),
                                   ("초기 고객의 반응은 어떠한가?", 5)]),
    "제품/기술력": (30, [("제품이 시장의 실제 문제를 해결하는가?", 5),
                     ("독창적인 기술이나 핵심 상용화 역량을 갖추고 있는가?", 15),
                     ("수익 모델은 명확한가?", 10)]),
    "경쟁 우위": (20, [("경쟁사 보다 뚜렷한 차별성이 있는가?", 5),
                    ("타사가 쉽게 모방할 수 없는 진입장벽(특허, 기술 격차, 파트너십 등)이 존재하는가?", 10),
                    ("시장을 선점할 수 있는 명확한 핵심 우위 요소가 있는가?", 5)]),
    "성장가능성": (15, [("고객 및 매출을 지속적으로 확장(Scale-up)할 수 있는 구조인가?", 8),
                    ("10년후에도 경쟁력이 있는가?", 7)]),
    "투자조건 (Deal Terms)": (10, [("팀은 이 분야에서 믿을만한가?", 5),
                               ("투자 조건(Valuation 등)이 적정한 수준인가?", 5)]),
}
BUNDLE = [{"id": "tech_summary:tech_001", "text": "CEO 정한울은 삼성 출신이며 고객사에 샘플을 공급했다. 밸류에이션 공개, 특허 보유"},
          {"id": "market_analysis:market_001", "text": "market size"},
          {"id": "profile:facts", "text": "{'funding_stage': 'SERIES_C'}"}]


def test_scorecard_matches_design_document():
    actual = {label: (weight, [(q, p) for _, q, p in items]) for _, label, weight, items in SCORECARD}
    assert actual == DESIGN


def judgement(scores: dict, risks=()):
    return Judgement(items=[ItemScore(key=k, score=v, rationale="r", evidence_ids=["tech_summary:tech_001"])
                            for k, v in scores.items()], risks=list(risks))


def full_marks(**overrides):
    marks = {key: points for _, _, _, items in SCORECARD for key, _, points in items}
    return {**marks, **overrides}


def test_code_caps_scores_and_zeroes_items_without_evidence():
    proposal = Judgement(items=[
        ItemScore(key="market_size", score=99, rationale="큰 시장", evidence_ids=["market_analysis:market_001"]),
        ItemScore(key="team", score=5, rationale="추정", evidence_ids=[]),
        ItemScore(key="deal_terms", score=5, rationale="추정", evidence_ids=["invented:id"]),
    ], risks=[])
    details = score_judgement(proposal, BUNDLE)
    items = {i["key"]: i for i in details["items"]}
    assert items["market_size"]["score"] == 10          # 배점 상한
    assert items["team"]["score"] == 0 and items["team"]["rationale"] == "근거 부족"
    assert items["deal_terms"]["score"] == 0            # 존재하지 않는 근거 ID
    assert items["early_traction"]["score"] == 0        # LLM이 빠뜨린 항목
    assert details["total"] == 10


def test_risk_penalty_is_ten_per_risk_type():
    risks = [Risk(type="기술", description="수율", fatal=True, evidence_ids=["tech_summary:tech_001"]),
             Risk(type="기술", description="발열", fatal=True, evidence_ids=["tech_summary:tech_001"]),
             Risk(type="법률", description="특허 분쟁", fatal=True, evidence_ids=["tech_summary:tech_001"])]
    details = score_judgement(judgement(full_marks(), risks), BUNDLE)
    assert details["penalized_risk_types"] == ["기술", "법률"]
    assert details["risk_penalty"] == -20 and details["total"] == 80


def test_fatal_risk_deducts_ten_and_unsupported_risk_is_ignored():
    risks = [Risk(type="법률", description="특허 분쟁", fatal=True, evidence_ids=["tech_summary:tech_001"]),
             Risk(type="운영", description="공급망", fatal=False, evidence_ids=["tech_summary:tech_001"]),
             Risk(type="기술", description="추정 리스크", fatal=True, evidence_ids=[])]
    details = score_judgement(judgement(full_marks(), risks), BUNDLE)
    assert details["risk_penalty"] == -10
    assert details["total"] == 90
    assert [r["description"] for r in details["risks"]] == ["특허 분쟁", "공급망"]


@pytest.mark.parametrize("deal_terms,decision", [(5, "RECOMMENDED"), (4, "HOLD")])
def test_threshold_boundary(deal_terms, decision):
    # 만점 100에서 30점을 빼 70점 경계 확인: team 5 + market_leadership 5 + long_term 7
    # + early_traction 5 + differentiation 5 + solves_problem 3 = 30, deal_terms 로 70/69 조정
    scores = full_marks(team=0, market_leadership=0, long_term=0, early_traction=0,
                        differentiation=0, solves_problem=2, deal_terms=deal_terms)
    details = score_judgement(judgement(scores), BUNDLE)
    assert details["total"] == 65 + deal_terms
    assert details["decision"] == decision


def test_judge_node_returns_scores_details_and_hold_reason():
    judge = InvestmentJudge(parse=lambda **kwargs: judgement({"market_size": 8}))
    state = {"current_startup": {"name": "A", "source_ids": []}, "source_evidence": {},
             "tech_summary": {"claims": [{"claim_id": "tech_001", "text": "t", "citations": [{"url": "u"}]}]}}
    update = judge(state)
    assert update["investment_decision"] == "HOLD"
    assert update["evaluation_scores"]["market"] == 8.0
    assert update["evaluation_scores"]["market.market_size"] == 8.0
    assert update["evaluation_scores"]["total"] == 8.0
    assert "총점 8점 < 추천 기준 70점" in update["hold_reason"]
    assert len(update["evaluation_details"]["items"]) == 13


def chunk(chunk_id, company, text):
    return Chunk(chunk_id=chunk_id, document_id=chunk_id, company=company, url="https://example.com/r",
                 title="Report", document_type="press_release", published_at=None, page=1, heading="",
                 text=text, publisher="example.com", accessed_at="2026-09-30T00:00:00+00:00")


class FakeIndex:
    def __init__(self, chunks):
        self.chunks = chunks

    def search(self, query, *, company, top_k, min_score):
        return [SearchHit(c, 0.9, {}) for c in self.chunks if c.company == company][:top_k]


class Quoting:
    """각 필드에 첫 청크 원문을 인용하는 가짜 LLM."""

    def __init__(self, texts=None):
        self.calls = 0
        self.texts = texts or {}

    def generate(self, *, company, question, hits, fields):
        self.calls += 1
        first = hits[0].chunk
        return GroundedResponse(facts=[
            Fact(field=f, text=self.texts.get(f, f"{f} 설명"), category=None,
                 citations=[Citation(chunk_id=first.chunk_id, quote=first.text[:30])]) for f in fields
        ], missing_information=[])


def test_segments_follow_tech_category():
    available = {"AI_CHIP", "CXL_MEMORY", "SILICON_PHOTONICS", "IN_MEMORY_COMPUTE", "CHIPLET"}
    assert segments_for("NPU / AI_ACCELERATOR", available) == ["AI_CHIP"]
    assert segments_for("IN_MEMORY_COMPUTE", available) == ["IN_MEMORY_COMPUTE", "AI_CHIP"]
    assert segments_for("GPU / CXL", available) == ["CXL_MEMORY", "AI_CHIP"]
    assert segments_for("근거 부족", available) == sorted(available)


def test_market_analyst_cites_segment_reports_and_registers_sources():
    index = FakeIndex([chunk("m1", "AI_CHIP", "The market is projected to reach $56.8 billion by 2030"),
                       chunk("m2", "CXL_MEMORY", "CXL memory appliance market grows at 33.3% CAGR")])
    state = {"current_startup": {"name": "A"}, "tech_category": "NPU", "source_evidence": {"A": []}}
    update = MarketAnalyst(index, generator=Quoting())(state)
    result = update["market_analysis"]
    assert result["status"] == "OK" and result["segments"] == ["AI_CHIP"]
    assert {c["source_ids"][0] for c in result["claims"]} == {"m1"}
    for source in update["source_evidence"]["A"]:
        validate_source(source)
    cleaned, dropped = drop_unsupported_claims(result, source_evidence=update["source_evidence"],
                                               field_name="market_analysis", startup="A")
    assert dropped == [] and len(cleaned["claims"]) == 3


class FakeSearch:
    def __init__(self, hits):
        self.hits = hits

    def search(self, query, *, max_results):
        return self.hits


def test_competitor_analyst_lists_competitors_from_web_evidence():
    hit = {"url": "https://news.example.com/a", "title": "Hailo rivals Mobilint",
           "snippet": "Mobilint competes with Hailo and DEEPX in edge NPUs", "published_at": None}
    generator = Quoting({"main_competitors": "Hailo"})
    state = {"current_startup": {"name": "Mobilint", "main_products": ["ARIES"]},
             "tech_category": "NPU", "tech_summary": {}, "source_evidence": {}}
    update = CompetitorAnalyst(FakeSearch([hit]), generator=generator)(state)
    result = update["competitor_analysis"]
    assert result["status"] == "OK"
    assert result["data"]["main_competitors"] == ["Hailo"]
    assert update["source_evidence"]["Mobilint"][0]["publisher"] == "news.example.com"


def test_competitor_analyst_without_results_does_not_call_llm():
    generator = Quoting()
    state = {"current_startup": {"name": "A", "main_products": []}, "tech_category": "",
             "tech_summary": {}, "source_evidence": {}}
    update = CompetitorAnalyst(FakeSearch([]), generator=generator)(state)
    assert update["competitor_analysis"]["status"] == "INSUFFICIENT_DATA"
    assert generator.calls == 0


def test_items_needing_specific_information_are_capped_without_it():
    proposal = Judgement(items=[
        ItemScore(key="team", score=5, rationale="설립 2019", evidence_ids=["profile:facts"]),
        ItemScore(key="deal_terms", score=5, rationale="Series C", evidence_ids=["profile:facts"]),
        ItemScore(key="early_traction", score=5, rationale="투자 유치", evidence_ids=["profile:facts"]),
    ], risks=[])
    items = {i["key"]: i for i in score_judgement(proposal, BUNDLE)["items"]}
    assert (items["team"]["score"], items["deal_terms"]["score"], items["early_traction"]["score"]) == (0, 2, 2)
    assert items["deal_terms"]["evidence_ids"] == ["profile:facts"]
    assert "상한 적용" in items["team"]["rationale"]
    # 필요한 정보(대표 경력·고객 공급)가 인용 근거에 있으면 상한 없음
    ok = Judgement(items=[ItemScore(key="team", score=5, rationale="대표 경력", evidence_ids=["tech_summary:tech_001"]),
                          ItemScore(key="early_traction", score=5, rationale="샘플 공급",
                                    evidence_ids=["tech_summary:tech_001"])], risks=[])
    items = {i["key"]: i for i in score_judgement(ok, BUNDLE)["items"]}
    assert (items["team"]["score"], items["early_traction"]["score"]) == (5, 5)


def test_judge_uses_median_of_repeated_scoring():
    proposals = iter([judgement({"market_size": 2}), judgement({"market_size": 9}), judgement({"market_size": 6})])
    judge = InvestmentJudge(parse=lambda **kwargs: next(proposals), samples=3)
    state = {"current_startup": {"name": "A", "source_ids": []}, "source_evidence": {},
             "tech_summary": {"claims": [{"claim_id": "tech_001", "text": "t", "citations": [{"url": "u"}]}]}}
    details = judge(state)["evaluation_details"]
    market_size = next(i for i in details["items"] if i["key"] == "market_size")
    assert market_size["score"] == 6 and market_size["samples"] == [2, 9, 6]
    assert details["sample_totals"] == [2, 9, 6] and details["total"] == 6


def test_competitor_names_must_appear_in_quote_and_not_be_the_company():
    from investment_scout.agents.competitor import valid_competitors

    def fact(name, quote):
        return Fact(field="main_competitors", text=name, category=None,
                    citations=[Citation(chunk_id="c", quote=quote)])
    response = GroundedResponse(facts=[fact("Hailo", "Mobilint competes with Hailo in edge"),
                                       fact("UALink", "Mobilint competes with Hailo in edge"),
                                       fact("Mobilint", "Mobilint competes with Hailo")], missing_information=[])
    kept, dropped = valid_competitors(response, "Mobilint")
    assert [f.text for f in kept.facts] == ["Hailo"]
    assert len(dropped) == 2
