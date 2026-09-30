"""출력 스키마와 검증 함수 테스트 (필수 테스트 12)."""

from __future__ import annotations

import pytest

from conftest import names_of, run_graph
from investment_scout.contracts import (
    DEFAULT_REQUIRED_ANALYSIS_DATA,
    SchemaViolationError,
    empty_analysis_result,
    find_missing_core_information,
    make_claim,
    validate_analysis_result,
    validate_node_update,
)


def valid_result(**overrides):
    base = {
        "status": "OK",
        "data": {"core_technology": "NPU"},
        "claims": [make_claim("c1", "텍스트", source_ids=["src_001"])],
        "missing_information": [],
        "errors": [],
    }
    base.update(overrides)
    return base


def test_valid_result_passes():
    validate_analysis_result(valid_result(), field_name="tech_summary")


# 12. 에이전트 출력 스키마 위반: 명확한 검증 오류
@pytest.mark.parametrize(
    "overrides, fragment",
    [
        ({"status": "MAYBE"}, "status"),
        ({"data": []}, "data 는 dict"),
        ({"claims": {}}, "claims 는 list"),
        ({"missing_information": None}, "missing_information"),
        ({"errors": "none"}, "errors"),
    ],
)
def test_schema_violations_raise(overrides, fragment):
    with pytest.raises(SchemaViolationError, match=fragment):
        validate_analysis_result(valid_result(**overrides), field_name="tech_summary")


def test_missing_required_key_raises():
    result = valid_result()
    del result["claims"]
    with pytest.raises(SchemaViolationError, match="필수 키 'claims'"):
        validate_analysis_result(result, field_name="tech_summary")


def test_claim_kind_must_be_valid():
    result = valid_result(claims=[{"claim_id": "c", "text": "t", "kind": "GUESS", "source_ids": []}])
    with pytest.raises(SchemaViolationError, match="kind"):
        validate_analysis_result(result, field_name="tech_summary")


def test_inference_claim_requires_reasoning():
    """추론은 근거와 추론 과정을 구분해 표현해야 합니다."""
    result = valid_result(
        claims=[{"claim_id": "c", "text": "t", "kind": "INFERENCE", "source_ids": ["s"]}]
    )
    with pytest.raises(SchemaViolationError, match="reasoning"):
        validate_analysis_result(result, field_name="tech_summary")

    ok = valid_result(
        claims=[
            make_claim("c", "t", kind="INFERENCE", source_ids=["s"], reasoning="A 이므로 B")
        ]
    )
    validate_analysis_result(ok, field_name="tech_summary")


def test_computation_requires_method_and_sourced_inputs():
    """수치 계산은 입력값의 출처와 계산 방법을 함께 보존해야 합니다."""
    bad = valid_result(
        claims=[
            make_claim("c", "t", source_ids=["s"], computation={"inputs": []})
        ]
    )
    with pytest.raises(SchemaViolationError, match="method"):
        validate_analysis_result(bad, field_name="market_analysis")

    bad2 = valid_result(
        claims=[
            make_claim(
                "c", "t", source_ids=["s"],
                computation={"method": "TAM x share", "inputs": [{"name": "TAM", "value": 10}]},
            )
        ]
    )
    with pytest.raises(SchemaViolationError, match="source_ids"):
        validate_analysis_result(bad2, field_name="market_analysis")

    good = valid_result(
        claims=[
            make_claim(
                "c", "t", source_ids=["s"],
                computation={
                    "method": "TAM x share",
                    "inputs": [{"name": "TAM", "value": 10, "source_ids": ["s"]}],
                },
            )
        ]
    )
    validate_analysis_result(good, field_name="market_analysis")


def test_duplicate_claim_id_rejected():
    result = valid_result(
        claims=[make_claim("c1", "a", source_ids=["s"]), make_claim("c1", "b", source_ids=["s"])]
    )
    with pytest.raises(SchemaViolationError, match="중복"):
        validate_analysis_result(result, field_name="tech_summary")


def test_non_json_serializable_rejected():
    result = valid_result(data={"when": object()})
    with pytest.raises(SchemaViolationError, match="JSON"):
        validate_analysis_result(result, field_name="tech_summary")


# 노드 반환 계약
def test_node_update_rejects_raw_analysis_result():
    """AnalysisResult 를 노드 반환값으로 그대로 쓰면 안 됩니다."""
    with pytest.raises(SchemaViolationError, match="담당 State 필드에 담아"):
        validate_node_update(
            valid_result(), allowed_fields=("tech_summary",), node_name="tech_analysis"
        )


def test_node_update_rejects_foreign_fields():
    with pytest.raises(SchemaViolationError, match="담당이 아닌 State 필드"):
        validate_node_update(
            {"tech_summary": valid_result(), "final_report": "x"},
            allowed_fields=("tech_summary",),
            node_name="tech_analysis",
        )


def test_separate_tech_and_category_node_updates_accept_correct_shapes():
    tech_update = validate_node_update(
        {"tech_summary": valid_result()},
        allowed_fields=("tech_summary",),
        node_name="tech_analysis",
    )
    category_update = validate_node_update(
        {"tech_category": "NPU"},
        allowed_fields=("tech_category",),
        node_name="tech_classification",
    )
    assert tech_update["tech_summary"]["status"] == "OK"
    assert category_update["tech_category"] == "NPU"


# 12. 그래프 실행 중 스키마 위반도 명확한 오류
def test_schema_violation_in_graph_raises():
    name = names_of(1)[0]
    with pytest.raises(SchemaViolationError, match="status"):
        run_graph(candidates=1, broken_schema_for={name})


# 평가 필수 정보 기준
def test_missing_core_information_detects_gaps():
    state = {
        "tech_summary": {
            "status": "OK",
            # 팀 결정: 기술 필수 정보는 core_technology 만 (differentiation 누락은 통과)
            "data": {"core_technology": None, "differentiation": "490개 모델 지원"},
            "claims": [], "missing_information": [], "errors": [],
        },
        "market_analysis": empty_analysis_result(missing_information=["시장 규모 미확보"]),
        "competitor_analysis": {
            "status": "OK", "data": {"main_competitors": ["A"]},
            "claims": [], "missing_information": [], "errors": [],
        },
    }
    missing = find_missing_core_information(state)

    assert "tech_summary" in missing
    assert "market_analysis" in missing
    assert "competitor_analysis" not in missing
    assert "시장 규모 미확보" in missing["market_analysis"]


def test_required_criteria_is_configurable():
    state = {
        "tech_summary": {"status": "OK", "data": {"x": 1}, "claims": [],
                         "missing_information": [], "errors": []},
    }
    assert find_missing_core_information(state, required={"tech_summary": ["x"]}) == {}
    assert "tech_summary" in find_missing_core_information(state, required={"tech_summary": ["y"]})
    assert set(DEFAULT_REQUIRED_ANALYSIS_DATA) == {
        "tech_summary", "market_analysis", "competitor_analysis"
    }
