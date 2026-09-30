"""역할 4 검증: 최종 State → 보고서 내용(요약·점수표·인용 번호·Reference 형식)과 5페이지 PDF."""

import copy

import pytest

from investment_scout.reporting.content import build_report_content, market_brief, reference_line
from investment_scout.reporting.node import make_report_node
from investment_scout.reporting.pdf_report import build_markdown_report

MARKET_QUOTE = ("The Die-to-Die IP Market Size is estimated at USD 1.80 Billion in 2025 and is projected to "
                "reach USD 3.72 Billion by 2033, growing at a CAGR of 9.57%.")


def source(source_id, **kw):
    return {"source_id": source_id, "url": f"https://example.com/{source_id.split(':')[0]}",
            "title": source_id.split(":")[0], "publisher": "Example", "published_at": None,
            "accessed_at": "2026-09-30T00:00:00+00:00", **kw}


def claim(claim_id, key, text, source_ids, quote="근거 문장입니다"):
    return {"claim_id": claim_id, "text": text, "kind": "FACT", "source_ids": source_ids, "data_keys": [key],
            "citations": [{"source_id": sid, "quote": quote, "url": "u", "page": 2} for sid in source_ids]}


def record(name, total, decision, *, qualified=True, missing=None):
    items = [
        {"group": group, "group_label": label, "key": key, "question": question, "max_points": points,
         "score": points if qualified else points // 2, "rationale": f"{key} 판단 이유",
         "evidence_ids": [f"market_analysis:{name}_m1"] if group == "market" else [f"tech_summary:{name}_t1"],
         "samples": [points] * 3}
        for group, label, key, question, points in [
            ("market", "시장성 (Opportunity Size)", "market_size", "이 시장은 얼마나 큰가?", 10),
            ("technology", "제품/기술력", "core_technology", "독창적인 기술이 있는가?", 15),
            ("deal", "투자조건 (Deal Terms)", "team", "팀은 이 분야에서 믿을만한가?", 5),
        ]
    ]
    return {
        "startup": name, "profile": {"name": name, "region": "KR", "country": "KR", "founded_year": 2021,
                                     "funding_stage": "SERIES_B", "main_products": ["NPU"], "source_ids": []},
        "tech_category": "NPU", "investment_decision": decision, "hold_reason": None if decision == "RECOMMENDED" else "미달",
        "missing_core_information": missing or {},
        "evaluation_scores": {"total": float(total)},
        "evaluation_details": {"decision": "RECOMMENDED" if qualified else "HOLD", "total": total, "threshold": 70,
                               "groups": {"market": 10, "technology": 15, "competition": 0, "growth": 0, "deal": 5},
                               "items": items, "risks": [{"type": "법률", "description": "특허 분쟁 가능성",
                                                          "fatal": False, "evidence_ids": []}],
                               "penalized_risk_types": [], "risk_penalty": 0, "sample_totals": [total] * 3},
        "tech_summary": {"data": {"core_technology": "NPU"}, "claims": [
            claim(f"{name}_t1", "core_technology", f"{name}은 저전력 NPU를 개발한다.", [f"{name}_doc:p1:s1:c1"])]},
        "market_analysis": {"segments": ["CHIPLET"], "data": {"market_size": "x"}, "claims": [
            claim(f"{name}_m1", "market_size", "다이 간 IP 시장은 2033년 37억 달러로 성장한다.",
                  ["m_d2d:p2:s1:c1"], MARKET_QUOTE)]},
        "competitor_analysis": {"data": {"main_competitors": ["Rival"]}, "claims": [
            claim(f"{name}_c1", "compare_performance", f"{name}은 Rival보다 전력 효율이 높다.", [f"web_{name}"])]},
    }


@pytest.fixture
def state():
    history = [record("Alpha", 85, "RECOMMENDED"), record("Beta", 90, "HOLD", missing={"tech_summary": ["x"]}),
               record("Gamma", 60, "HOLD", qualified=False)]
    sources = {name: [source(f"{name}_doc:p1:s1:c1", document_type="official_website", page=1, company=name),
                      source("m_d2d:p2:s1:c1", document_type="press_release", company="CHIPLET", page=2),
                      source(f"web_{name}", document_type="web_search", page_basis="web_search", company=name)]
               for name in ("Alpha", "Beta", "Gamma")}
    sources["Alpha"].append(source("unused:p9:s1:c1", document_type="paper", title="Never cited", page=9))
    return {
        "target_domain": "Semiconductor", "evaluation_history": history, "candidate_startups": ["Alpha", "Beta", "Gamma"],
        "recommended_startup": "Alpha", "termination_reason": "RECOMMENDED_FOUND",
        "final_ranking": [{"startup": "Beta", "total": 90.0, "qualified": False, "rank": 1},
                          {"startup": "Alpha", "total": 85.0, "qualified": True, "rank": 2},
                          {"startup": "Gamma", "total": 60.0, "qualified": False, "rank": 3}],
        "scout_result": {"counts": {"KR": {"secured": 2}, "OVERSEAS": {"secured": 1}}},
        "source_evidence": sources,
    }


def test_content_uses_agent_results_for_recommended_company(state):
    content = build_report_content(state)
    assert content["company"] == "Alpha" and content["recommended"] and content["total"] == 85
    labels = dict(content["summary"])
    assert labels["결론"].startswith("Alpha 투자 추천")
    assert "기준 통과 1개사" in labels["평가 과정"] and "핵심 정보 부족 보류 1개사" in labels["평가 과정"]
    assert "2025년 18억 달러 → 2033년 37억 달러" in labels["시장"] and "9.57%" in labels["시장"]
    assert all(len(text) <= 150 for _, text in content["summary"])
    assert content["narrative"].startswith("Alpha를 추천한 핵심 이유는 독창적 기술(15/15점")
    assert "같거나 높은 점수의 Beta는 핵심 정보가 부족해 추천에서 제외" in content["narrative"]
    assert "반면" not in content["narrative"]  # 절반 미만 항목이 없으면 감점 문장 생략
    statuses = {row["startup"]: row["status"] for row in content["ranking"]}
    assert statuses == {"Alpha": "추천", "Beta": "보류(핵심 정보 부족)", "Gamma": "보류(70점 미만)"}


def test_references_are_numbered_in_citation_order_and_exclude_uncited(state):
    content = build_report_content(state)
    assert content["market"]["market_size"][0].endswith("[1]")          # 시장 → 기술 → 경쟁 순서
    assert content["tech"]["core_technology"][0].endswith("[2]")
    assert content["competition"]["compare_performance"][0].endswith("[3]")
    texts = [text for _, text, _ in content["references"]]
    assert len(texts) == 3 and not any("Never cited" in t for t in texts)
    kinds = [kind for kind, _, _ in content["references"]]
    assert kinds == ["기관 보고서", "웹페이지", "웹페이지"]


def test_all_hold_reports_top_candidate_as_hold(state):
    held = copy.deepcopy(state)
    held["recommended_startup"] = ""
    held["termination_reason"] = "ALL_HOLD"
    content = build_report_content(held)
    assert content["company"] == "Beta" and not content["recommended"]
    assert dict(content["summary"])["결론"].startswith("전원 보류")
    assert "추천 기준 70점을 넘은 기업이 없어 전원 보류" in content["narrative"]


def test_reference_formats_follow_guide():
    paper = {"number": 1, "pages": {4}, "source": source("p", document_type="paper", publisher="arxiv.org",
                                                           title="LPU", url="https://arxiv.org/pdf/2408.07326")}
    assert reference_line(paper) == ("학술 논문", "arxiv.org(2024). LPU. arXiv preprint (p.4). https://arxiv.org/pdf/2408.07326")
    web = {"number": 2, "pages": set(), "source": source("w", published_at="2026-07-29", publisher="Eliyan",
                                                          title="Series C", url="https://eliyan.com/news")}
    assert reference_line(web) == ("웹페이지", "Eliyan(2026-07-29). Series C. eliyan.com, https://eliyan.com/news")


def test_market_brief_reads_numbers_only_from_quotes():
    analysis = {"claims": [claim("m", "market_size", "요약", ["s"], MARKET_QUOTE)]}
    assert market_brief(analysis) == "2025년 18억 달러 → 2033년 37억 달러, 연평균 성장률(CAGR) 9.57%"
    assert market_brief({"claims": []}) == "시장 수치 근거 부족"


def test_pdf_is_five_pages_and_node_updates_only_final_report(state, tmp_path):
    pytest.importorskip("reportlab")
    from pypdf import PdfReader

    output = tmp_path / "report.pdf"
    update = make_report_node(output)(state)
    assert set(update) == {"final_report"} and "Alpha 투자 추천" in update["final_report"]
    reader = PdfReader(str(output))
    text = "\n".join(page.extract_text() or "" for page in reader.pages)
    assert len(reader.pages) == 5
    for heading in ("1. Summary", "2. 선정 시장", "3. 선정 기업", "4. 성장가능성과 리스크", "5. Reference"):
        assert heading in text
    assert "Never cited" not in text
    assert "울산 캠퍼스 4반 3조(신한수, 안영준, 정하윤, 손수경, 손경락)" in text


def test_markdown_summary_contains_scores(state):
    markdown = build_markdown_report(build_report_content(state))
    assert "**총점** | **85/100**" in markdown
