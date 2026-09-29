"""출처/근거 정책 테스트 (필수 테스트 13, 16)."""

from __future__ import annotations

import pytest

from conftest import names_of, run_graph
from investment_scout.contracts import find_dangling_source_ids, make_claim
from investment_scout.evidence import (
    EvidenceError,
    claim_is_supported,
    build_source_index,
    collect_source_ids,
    drop_unsupported_claims,
    new_source,
)


def sample_source(source_id="src_001", **kwargs):
    defaults = dict(
        source_id=source_id,
        url="https://mock.invalid/a",
        title="자료",
        publisher="PUB",
        evidence="본문 인용",
        supports=["funding_stage"],
        published_at="2026-01-01",
        url_fetched=True,
        is_mock=True,
    )
    defaults.update(kwargs)
    return new_source(**defaults)


def result_with(claims, data=None):
    return {
        "status": "OK",
        "data": data or {},
        "claims": claims,
        "missing_information": [],
        "errors": [],
    }


def test_source_requires_evidence_text():
    with pytest.raises(EvidenceError, match="evidence"):
        sample_source(evidence="   ")


def test_unknown_supports_topic_rejected():
    with pytest.raises(EvidenceError, match="supports"):
        sample_source(supports=["가짜주제"])


def test_url_existence_is_separate_from_supporting_the_claim():
    """URL 이 존재한다는 사실과 그 자료가 주장을 뒷받침한다는 사실을 구분합니다."""
    index = build_source_index({"회사": [sample_source(url_fetched=False)]})
    claim = make_claim("c1", "주장", source_ids=["src_001"])
    supported, reason = claim_is_supported(claim, index)

    assert supported is False
    assert "미확인" in reason


# 13. 출처 없는 사실/수치는 확정 결과와 보고서에서 제외
def test_claim_without_source_is_dropped_with_diagnostic():
    result = result_with(
        [make_claim("c1", "매출 100억", source_ids=[], data_keys=["revenue"])],
        data={"revenue": "100억"},
    )
    cleaned, diags = drop_unsupported_claims(
        result, source_evidence={}, field_name="market_analysis", startup="회사"
    )

    assert cleaned["claims"] == []
    assert "revenue" not in cleaned["data"]  # 뒷받침 없는 수치도 제거
    kinds = {d["kind"] for d in diags}
    assert "UNSUPPORTED_CLAIM_DROPPED" in kinds
    assert "UNSUPPORTED_DATA_DROPPED" in kinds
    assert any("출처 미확보로 제외됨" in m for m in cleaned["missing_information"])


def test_supported_claim_is_kept():
    evidence = {"회사": [sample_source()]}
    result = result_with(
        [make_claim("c1", "시리즈 A 유치", source_ids=["src_001"], data_keys=["stage"])],
        data={"stage": "SERIES_A"},
    )
    cleaned, diags = drop_unsupported_claims(
        result, source_evidence=evidence, field_name="tech_summary", startup="회사"
    )

    assert len(cleaned["claims"]) == 1
    assert cleaned["data"]["stage"] == "SERIES_A"
    assert diags == []


# 16. 존재하지 않는 source_id 탐지
def test_dangling_source_id_detected_and_dropped():
    evidence = {"회사": [sample_source()]}
    result = result_with([make_claim("c1", "주장", source_ids=["src_999"])])

    assert find_dangling_source_ids(result, collect_source_ids(evidence)) == ["src_999"]

    cleaned, diags = drop_unsupported_claims(
        result, source_evidence=evidence, field_name="tech_summary", startup="회사"
    )
    assert cleaned["claims"] == []
    dangling = [d for d in diags if d["kind"] == "DANGLING_SOURCE_REFERENCE"]
    assert dangling
    # 자료 부족이 아니라 시스템 오류로 분류됩니다.
    assert dangling[0]["category"] == "SYSTEM_ERROR"


def test_dangling_source_id_in_computation_inputs_detected():
    result = result_with(
        [
            make_claim(
                "c1", "시장 규모", source_ids=["src_001"],
                computation={
                    "method": "TAM x share",
                    "inputs": [{"name": "TAM", "value": 1, "source_ids": ["src_missing"]}],
                },
            )
        ]
    )
    assert find_dangling_source_ids(result, {"src_001"}) == ["src_missing"]


# 통합: 그래프 실행에서도 동일하게 동작
def test_graph_excludes_unsourced_claims_from_history_and_report():
    name = names_of(1)[0]
    final = run_graph(candidates=1, unsourced_for={name})

    record = final["evaluation_history"][0]
    assert record["tech_summary"]["claims"] == []
    assert "core_technology" not in record["tech_summary"]["data"]
    assert "[MOCK] AI 추론 가속 NPU" not in final["final_report"]

    kinds = {d["kind"] for d in final["diagnostics"]}
    assert "UNSUPPORTED_CLAIM_DROPPED" in kinds


def test_graph_detects_dangling_source_reference():
    name = names_of(1)[0]
    final = run_graph(candidates=1, dangling_for={name})

    dangling = [d for d in final["diagnostics"] if d["kind"] == "DANGLING_SOURCE_REFERENCE"]
    assert dangling
    assert dangling[0]["category"] == "SYSTEM_ERROR"
    assert "src_does_not_exist_999" in str(dangling[0]["details"])


def test_evidence_ref_links_history_to_source_evidence():
    """다른 담당자가 기업과 근거를 연결할 수 있는 키 규칙."""
    name = names_of(1)[0]
    final = run_graph(candidates=1)

    record = final["evaluation_history"][0]
    key = record["evidence_ref"]["source_evidence_key"]

    assert key == name == record["startup"] == record["profile"]["name"]
    assert key in final["source_evidence"]
    registered = {s["source_id"] for s in final["source_evidence"][key]}
    assert set(record["evidence_ref"]["source_ids"]) <= registered


def test_unknown_values_are_none_not_zero():
    """알 수 없는 매출/시장 규모를 0 으로 채우지 않습니다."""
    final = run_graph(candidates=1)
    record = final["evaluation_history"][0]

    assert record["market_analysis"]["data"]["company_revenue"] is None
    assert record["competitor_analysis"]["data"]["market_share"] is None


def test_scores_without_basis_are_omitted_not_invented():
    final = run_graph(candidates=1)
    record = final["evaluation_history"][0]

    assert "team" not in record["evaluation_scores"]  # 근거 없는 점수는 생략
    assert all(isinstance(v, float) for v in record["evaluation_scores"].values())
    assert any(g["metric"] == "team" for g in record["score_gaps"])
