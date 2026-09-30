"""역할 4 입력 계약, 출처 필터링, 그래프 노드와 PDF 제한 검증."""

import json
from pathlib import Path

import pytest

from investment_scout.reporting.contracts import Role3HandoffError, normalize_report_input
from investment_scout.reporting.node import make_report_node
from investment_scout.reporting.pdf_report import MAX_SUMMARY_CHARS, build_markdown_report


@pytest.fixture
def demo_payload():
    return json.loads(Path("data/report_demo.json").read_text(encoding="utf-8"))


@pytest.fixture
def all_hold_payload():
    return json.loads(Path("data/report_all_hold.json").read_text(encoding="utf-8"))


def test_role3_contract_recalculates_total_and_keeps_only_used_references(demo_payload):
    data = normalize_report_input(demo_payload)

    assert data["total_score"] == 78
    assert data["risk_penalty"] == 0
    assert len(data["scorecard"]) == 5
    assert {item["source_id"] for item in data["references"]} == {
        "demo_market", "demo_tech", "demo_competition", "demo_company",
    }
    assert "unused_reference" not in build_markdown_report(data)


def test_role3_contract_rejects_wrong_total_and_missing_reference(demo_payload):
    wrong_total = {**demo_payload, "total_score": 99}
    with pytest.raises(Role3HandoffError, match="재계산값"):
        normalize_report_input(wrong_total)

    missing = {**demo_payload, "references": demo_payload["references"][1:]}
    with pytest.raises(Role3HandoffError, match="reference"):
        normalize_report_input(missing)


def test_fatal_risk_is_minus_ten_and_changes_total(demo_payload):
    payload = {
        **demo_payload,
        "risks": [{
            "description": "법률 리스크",
            "fatal": True,
            "penalty": -10,
            "source_ids": ["demo_company"],
        }],
        "total_score": 68,
        "decision": "HOLD",
    }
    data = normalize_report_input(payload)
    assert data["risk_penalty"] == -10
    assert data["total_score"] == 68


def test_current_state_is_supported_until_role3_handoff_is_ready():
    state = {
        "evaluation_history": [{
            "startup": "A",
            "profile": {"name": "A"},
            "evaluation_scores": {"시장성": 10, "기술력": 20},
            "investment_decision": "HOLD",
            "hold_reason": "자료 부족",
            "tech_summary": {"claims": []},
            "market_analysis": {"claims": []},
            "competitor_analysis": {"claims": []},
        }],
        "source_evidence": {},
        "termination_reason": "ALL_HOLD",
    }
    data = normalize_report_input(state)
    assert data["company"]["name"] == "A"
    assert data["decision"] == "HOLD"
    assert data["total_score"] == 30
    assert [item["score"] for item in data["scorecard"]] == [10, 20, None, None, None]


def test_current_role3_details_are_mapped_to_report_sources():
    source = lambda source_id: {
        "source_id": source_id, "publisher": "Publisher", "title": source_id,
        "url": f"https://example.com/{source_id}", "published_at": None,
        "accessed_at": "2026-09-30",
    }
    groups = {"market": 20, "technology": 24, "competition": 15, "growth": 11, "deal": 8}
    analyses = {
        "market_analysis": {"data": {"market_size": "TAM"}, "claims": [
            {"claim_id": "m1", "text": "시장 근거", "source_ids": ["s_market"]}
        ]},
        "tech_summary": {"data": {"core_technology": "NPU"}, "claims": [
            {"claim_id": "t1", "text": "기술 근거", "source_ids": ["s_tech"]}
        ]},
        "competitor_analysis": {"data": {"main_competitors": ["B"]}, "claims": [
            {"claim_id": "c1", "text": "경쟁 근거", "source_ids": ["s_comp"]}
        ]},
    }
    items = [
        {"group": "market", "rationale": "시장 이유", "evidence_ids": ["market_analysis:m1"]},
        {"group": "technology", "rationale": "기술 이유", "evidence_ids": ["tech_summary:t1"]},
        {"group": "competition", "rationale": "경쟁 이유", "evidence_ids": ["competitor_analysis:c1"]},
        {"group": "growth", "rationale": "성장 이유", "evidence_ids": ["market_analysis:m1"]},
        {"group": "deal", "rationale": "조건 이유", "evidence_ids": ["profile:s_profile"]},
    ]
    record = {
        "startup": "A", "profile": {"name": "A", "source_ids": ["s_profile"]},
        "tech_category": "NPU", "investment_decision": "RECOMMENDED", "hold_reason": None,
        "evaluation_scores": {**groups, "risk_penalty": 0.0, "total": 78.0},
        "evaluation_details": {"groups": groups, "items": items, "risks": [],
                               "penalized_risk_types": [], "risk_penalty": 0,
                               "total": 78, "threshold": 70},
        **analyses,
    }
    state = {
        "target_domain": "Semiconductor", "evaluation_history": [record],
        "recommended_startup": "A",
        "final_ranking": [{"startup": "A", "total": 78, "qualified": True, "rank": 1}],
        "source_evidence": {"A": [source("s_market"), source("s_tech"),
                                    source("s_comp"), source("s_profile")]},
    }

    data = normalize_report_input(state)

    assert data["total_score"] == 78
    assert data["company"]["name"] == "A"
    assert data["decision_reason"] == (
        "총점 78점으로 추천 기준 70점 이상을 충족했습니다. 치명 리스크 감점 유형: 없음."
    )
    assert [item["score"] for item in data["scorecard"]] == [20, 24, 15, 11, 8]
    assert {item["source_id"] for item in data["references"]} == {
        "s_market", "s_tech", "s_comp", "s_profile",
    }


def test_report_node_keeps_existing_state_update_contract(monkeypatch, demo_payload, tmp_path):
    called = {}

    def fake_render(data, output, **kwargs):
        called["data"] = data
        called["output"] = output
        return {"path": str(output), "pages": 5, "references": 4}

    monkeypatch.setattr("investment_scout.reporting.node.render_pdf_report", fake_render)
    node = make_report_node(tmp_path / "report.pdf")
    update = node({"role3_handoff": demo_payload})

    assert set(update) == {"final_report"}
    assert "RECOMMENDED" in update["final_report"]
    assert called["data"]["total_score"] == 78


def test_summary_is_limited_in_markdown(demo_payload):
    demo_payload["summary"] = "가" * (MAX_SUMMARY_CHARS + 100)
    report = build_markdown_report(normalize_report_input(demo_payload))
    summary = report.split("## Summary\n", 1)[1].split("\n\n", 1)[0]
    assert len(summary) <= MAX_SUMMARY_CHARS


def test_all_hold_scenario_is_reproducible(all_hold_payload):
    data = normalize_report_input(all_hold_payload)

    assert data["decision"] == "HOLD"
    assert data["total_score"] == 48
    assert data["risk_penalty"] == -10
    assert data["termination_reason"] == "ALL_HOLD"
    assert len(data["evaluated_companies"]) == 2


def test_pdf_is_exactly_five_pages_and_contains_only_used_references(demo_payload, tmp_path):
    pytest.importorskip("reportlab")
    from pypdf import PdfReader
    from investment_scout.reporting.pdf_report import render_pdf_report

    output = tmp_path / "report.pdf"
    result = render_pdf_report(normalize_report_input(demo_payload), output)
    reader = PdfReader(output)
    text = "\n".join(page.extract_text() or "" for page in reader.pages)

    assert result["pages"] == 5
    assert len(reader.pages) == 5
    assert "Demo Product Brief" in text
    assert "This source must not appear" not in text


def test_pdf_marks_missing_tam_and_sam_as_insufficient_evidence(all_hold_payload, tmp_path):
    pytest.importorskip("reportlab")
    from pypdf import PdfReader
    from investment_scout.reporting.pdf_report import render_pdf_report

    output = tmp_path / "all-hold.pdf"
    render_pdf_report(normalize_report_input(all_hold_payload), output)
    text = "\n".join(page.extract_text() or "" for page in PdfReader(output).pages)

    assert "전체 잠재시장 TAM" in text and "출처에서 확인된 데모 TAM" in text
    assert "접근 가능시장 SAM" in text and "근거 부족" in text
    assert "HOLD" in text


def test_research_layout_has_charts_citations_and_physical_summary_limit(demo_payload, tmp_path):
    pytest.importorskip("reportlab")
    from pypdf import PdfReader
    from reportlab.lib.pagesizes import A4
    from investment_scout.reporting.pdf_report import render_pdf_report
    output = tmp_path / "research.pdf"
    result = render_pdf_report(normalize_report_input(demo_payload), output)
    reader = PdfReader(output)
    text = "\n".join(page.extract_text() or "" for page in reader.pages)
    assert result["pages"] == 5
    assert result["layout"][0]["summary_height"] <= result["summary_max_height"]
    assert result["layout"][0]["summary_bottom"] <= A4[1] / 2
    assert all(p["scale"] >= .9 for p in result["layout"])
    assert "득점률" in text and "투자 전 확인" in text and "Reference" in text
    assert "연도별 구조화 수치 미제공" in text
    assert "[1]" in text and "추가 실사 과제는 감점하지 않습니다" in text
    assert b" re" in reader.pages[0].get_contents().get_data()


def test_market_series_requires_sources_and_finite_ordered_values(demo_payload):
    demo_payload["market"]["series"] = {"unit": "USD billion",
        "points": [{"year": 2025, "value": 10}, {"year": 2030, "value": 20}],
        "source_ids": ["demo_market"]}
    assert normalize_report_input(demo_payload)["market"]["series"]["points"][1]["value"] == 20
    demo_payload["market"]["series"]["points"][1]["value"] = float("nan")
    with pytest.raises(Role3HandoffError, match="유한"):
        normalize_report_input(demo_payload)
    demo_payload["market"]["series"]["points"][1]["value"] = 20
    demo_payload["market"]["series"]["source_ids"] = []
    with pytest.raises(Role3HandoffError, match="source_ids"):
        normalize_report_input(demo_payload)


def test_series_chart_uses_input_years_only(demo_payload, tmp_path):
    pytest.importorskip("reportlab")
    from pypdf import PdfReader
    from investment_scout.reporting.pdf_report import render_pdf_report
    demo_payload["market"]["series"] = {"title": "검증 시계열", "unit": "USD billion",
        "points": [{"year": 2025, "value": 11.8}, {"year": 2030, "value": 56.8}],
        "source_ids": ["demo_market"]}
    output = tmp_path / "series.pdf"
    render_pdf_report(normalize_report_input(demo_payload), output)
    text = PdfReader(output).pages[1].extract_text()
    assert "검증 시계열" in text and "USD billion" in text
    assert "11.8" in text and "56.8" in text
    assert "2027" not in text


def test_reference_urls_are_not_truncated(demo_payload, tmp_path):
    pytest.importorskip("reportlab")
    from pypdf import PdfReader
    from investment_scout.reporting.pdf_report import render_pdf_report
    url = "https://example.com/" + "long-reference-path-" * 12 + "END_OF_SOURCE"
    demo_payload["references"][0]["url"] = url
    output = tmp_path / "references.pdf"
    render_pdf_report(normalize_report_input(demo_payload), output)
    assert "END_OF_SOURCE" in PdfReader(output).pages[4].extract_text()


def test_overflow_does_not_destroy_existing_pdf(demo_payload, tmp_path):
    pytest.importorskip("reportlab")
    from investment_scout.reporting.pdf_report import render_pdf_report
    output = tmp_path / "keep.pdf"
    output.write_bytes(b"existing-report")
    for i in range(100):
        source_id = f"additional_{i}"
        demo_payload["references"].append({"source_id": source_id,
            "title": "긴 참고문헌 " * 30, "url": f"https://example.com/{i}"})
        demo_payload["market"]["source_ids"].append(source_id)
    with pytest.raises(ValueError, match="내용이 너무 많"):
        render_pdf_report(normalize_report_input(demo_payload), output)
    assert output.read_bytes() == b"existing-report"


def test_reference_contains_sources_only_without_filler(demo_payload, tmp_path):
    from pypdf import PdfReader
    from investment_scout.reporting.pdf_report import render_pdf_report
    output = tmp_path / "references-only.pdf"
    result = render_pdf_report(normalize_report_input(demo_payload), output)
    text = PdfReader(output).pages[4].extract_text()
    assert "Demo Product Brief" in text
    assert "분석 항목별 근거 연결" not in text
    assert "작성 방법과 데이터 한계" not in text
    assert result["layout"][4]["content_height"] < result["layout"][4]["available_height"] / 2


def test_long_summary_still_ends_above_page_midpoint(demo_payload, tmp_path):
    from reportlab.lib.pagesizes import A4
    from investment_scout.reporting.pdf_report import render_pdf_report
    demo_payload["summary"] = "가" * MAX_SUMMARY_CHARS
    result = render_pdf_report(normalize_report_input(demo_payload), tmp_path / "long-summary.pdf")
    assert result["layout"][0]["summary_bottom"] <= A4[1] / 2
