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
                               "penalized_risk_types": [], "risk_penalty": 0, "total": 78},
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


def test_state_sources_merge_market_chunk_shared_by_companies():
    from investment_scout.reporting.contracts import Role3HandoffError, _state_sources

    chunk = {"source_id": "m_dc:p2:s1:c1", "url": "https://example.com/market"}
    merged = _state_sources({"source_evidence": {"A": [chunk], "B": [dict(chunk)]}})
    assert merged == [chunk]
    conflict = {"source_evidence": {"A": [chunk], "B": [{**chunk, "url": "https://other.example"}]}}
    with pytest.raises(Role3HandoffError):
        _state_sources(conflict)
