"""Supervisor 패턴 검증 (API 호출 없음): 통신 제약, State 기반 라우팅·재작업, 품질 루프, 종료 보장, reducer, fallback."""

import json

import pytest

pytest.importorskip("reportlab")

from investment_scout.supervisor.graph import WORKERS, build_supervisor_graph
from investment_scout.supervisor.nodes import Nodes
from investment_scout.supervisor.observability import DecisionLog
from investment_scout.supervisor.quality import QualityJudgement, evaluate_report
from investment_scout.supervisor.runner import run_supervisor
from investment_scout.supervisor.state import MAX_REPORT_ATTEMPTS, merge_sources, upsert_history


def src(sid, company="Market", publisher="Example"):
    return {"source_id": sid, "url": f"https://{publisher.lower()}.com/{sid.split(':')[0]}", "title": sid.split(":")[0],
            "publisher": publisher, "published_at": "2026-01-01", "accessed_at": "2026-09-30", "page": 1,
            "document_type": "press_release", "company": company, "evidence": "근거", "url_fetched": True,
            "supports": ["product"]}


def result(claims, required):
    """claims: [(data_key, text, source)] → 분석 결과 (인용·evidence 포함)."""
    out = {"status": "OK", "data": {}, "claims": [], "missing_information": [], "errors": [], "evidence": []}
    for n, (key, text, source) in enumerate(claims, start=1):
        out["claims"].append({"claim_id": f"c{n}", "text": text, "kind": "FACT", "source_ids": [source["source_id"]],
                              "data_keys": [key], "citations": [{"source_id": source["source_id"], "quote": text,
                                                                 "url": source["url"], "page": 1}]})
        out["data"].setdefault(key, text if key not in ("main_competitors",) else [])
        if key == "main_competitors":
            out["data"]["main_competitors"].append(text)
        out["evidence"].append(source)
    out["status"] = "OK" if all(out["data"].get(k) for k in required) else "INSUFFICIENT_DATA"
    return out


class FakeAgents:
    """Thin은 첫 기술 요약이 근거 1건(부족) → 재작업에서 2건. totals로 투자 판단 총점 지정."""

    def __init__(self, totals, fail_tech=()):
        self.totals, self.fail_tech, self.calls = totals, set(fail_tech), []

    def tech(self, state, *, rework):
        name = state["current_startup"]["name"]
        self.calls.append(("tech", name, rework))
        if name in self.fail_tech:
            raise RuntimeError("API down")
        claims = [("core_technology", f"{name} NPU 기술", src(f"{name}_d1:p1", name, name))]
        if name != "Thin" or rework:
            claims.append(("advantages", f"{name} 저전력 장점", src(f"{name}_d2:p1", name, name)))
        return {"tech_summary": result(claims, ["core_technology"]), "tech_category": "NPU"}

    def market(self, state, *, rework):
        self.calls.append(("market", state["current_startup"]["name"], rework))
        return {"market_analysis": {**result([("market_size", "USD 1.80 Billion in 2025 to USD 3.72 Billion by 2033",
                                               src("m1:p1", "AI_CHIP", "Wire")),
                                              ("growth_rate", "CAGR of 9.57%", src("m2:p1", "AI_CHIP", "News"))],
                                             ["market_size", "growth_rate"]), "segments": ["AI_CHIP"]}}

    def competitor(self, state, *, rework):
        name = state["current_startup"]["name"]
        self.calls.append(("competitor", name, rework))
        return {"competitor_analysis": result([("main_competitors", "Hailo", src(f"w1_{name}", name, "Web")),
                                               ("main_competitors", "DEEPX", src(f"w2_{name}", name, "Press")),
                                               ("compare_performance", f"{name}은 Hailo보다 효율이 높다",
                                                src(f"w2_{name}", name, "Press"))], ["main_competitors"])}

    def judge(self, state):
        name = state["current_startup"]["name"]
        total = self.totals[name]
        items = [{"group": g, "group_label": g, "key": k, "question": q, "max_points": m, "score": s,
                  "rationale": f"{k} 이유", "evidence_ids": []}
                 for g, k, q, m, s in [("market", "market_size", "시장 크기", 10, 10),
                                       ("technology", "core_technology", "독창적 기술", 15, 15),
                                       ("deal", "team", "팀", 5, 0)]]
        return {"investment_decision": "RECOMMENDED" if total >= 70 else "HOLD",
                "hold_reason": None if total >= 70 else "미달", "evaluation_scores": {"total": float(total)},
                "evaluation_details": {"decision": "RECOMMENDED" if total >= 70 else "HOLD", "total": total,
                                       "threshold": 70, "items": items, "risks": [], "penalized_risk_types": [],
                                       "risk_penalty": 0, "sample_totals": [total] * 3,
                                       "groups": {"market": 10, "technology": 15, "competition": 0, "growth": 0,
                                                  "deal": total - 25}}}


class FakeScout:
    def __init__(self, names):
        self.names = names

    def run(self, domain):
        profiles = {n: {"name": n, "region": "KR", "founded_year": 2021, "funding_stage": "SERIES_A",
                        "main_products": ["NPU"], "source_ids": [f"{n}_src"]} for n in self.names}
        return {"candidate_startups": self.names, "candidate_profiles": profiles,
                "source_evidence": {n: [src(f"{n}_src", n, n)] for n in self.names},
                "scout_result": {"counts": {"KR": {"secured": len(self.names)}}}}


def judge_sequence(*neutralities):
    """품질 LLM 판정 대역: 호출마다 지정한 중립성 점수 반환 (마지막 값 반복)."""
    calls = []

    def judge(payload):
        score = neutralities[min(len(calls), len(neutralities) - 1)]
        calls.append(payload)
        return QualityJudgement(supported=[True] * len(payload["pairs"]), neutrality=score,
                                neutrality_reason="r", bias=5, bias_reason="r")
    judge.calls = calls
    return judge


def run(tmp_path, agents, names, judge, **kw):
    lines = []
    final = run_supervisor(tech_index=None, agents=agents, scout=FakeScout(names), quality_judge=judge,
                           max_candidates=len(names), report_path=tmp_path / "r.pdf", state_path=tmp_path / "s.json",
                           log_dir=tmp_path / "logs", checkpoint_path=tmp_path / "ckpt.sqlite",
                           echo=lambda line, **k: lines.append(line), **kw)
    log = [json.loads(line) for line in (tmp_path / "logs" / f"{final['trace_id']}.jsonl").read_text().splitlines()]
    return final, lines, log


def test_workers_only_return_to_supervisor():
    nodes = Nodes(agents=None, scout=None, log=None, report_path=None, max_candidates=1)
    edges = {(e.source, e.target) for e in build_supervisor_graph(nodes).get_graph().edges}
    for worker in WORKERS:
        outgoing = {target for source, target in edges if source == worker}
        assert outgoing == {"supervisor"}, worker
    assert {target for source, target in edges if source == "supervisor"} == {*WORKERS, "__end__"}


def test_supervisor_routes_by_state_and_reworks_insufficient_evidence(tmp_path):
    agents = FakeAgents({"Alpha": 80, "Thin": 90, "Gamma": 60})
    final, lines, log = run(tmp_path, agents, ["Alpha", "Thin", "Gamma"], judge_sequence(5))
    assert ("tech", "Thin", True) in agents.calls                 # 근거 부족 → 같은 에이전트 재작업
    assert ("tech", "Alpha", True) not in agents.calls            # 충분하면 재작업 없음
    reworks = [e for e in log if e["node"] == "supervisor" and "재작업" in e["reason"]]
    assert reworks and reworks[0]["company"] == "Thin"
    assert [r["startup"] for r in final["evaluation_history"]] == ["Alpha", "Thin", "Gamma"]
    assert final["recommended_startup"] == "Thin"                 # 기준 통과 중 최고점
    assert final["evaluation_history"][1]["rework"] == {"tech": 1}
    assert final["termination_reason"] == "RECOMMENDED_FOUND" and final["quality"]["passed"]
    assert final["report_attempts"] == 1
    assert any("🔁 Supervisor → tech" in line for line in lines)


def test_quality_failure_loops_then_passes(tmp_path):
    judge = judge_sequence(2, 5)                                  # 1회차 중립성 미달 → 재작성 → 통과
    final, _, log = run(tmp_path, FakeAgents({"Alpha": 80}), ["Alpha"], judge)
    assert final["report_attempts"] == 2 and final["quality"]["passed"]
    assert final["report_mode"] == "strict"
    assert [e["decision"] for e in log if e["node"] == "quality"] == ["fail", "pass"]


def test_quality_loop_stops_at_attempt_limit(tmp_path):
    final, _, _ = run(tmp_path, FakeAgents({"Alpha": 80}), ["Alpha"], judge_sequence(1))
    assert final["report_attempts"] == MAX_REPORT_ATTEMPTS
    assert final["termination_reason"] == "QUALITY_LIMIT" and not final["quality"]["passed"]
    assert (tmp_path / "r.pdf").exists()


def test_step_limit_ends_evaluation_but_still_reports(tmp_path):
    final, _, _ = run(tmp_path, FakeAgents({"Alpha": 80, "Beta": 75, "Gamma": 72}), ["Alpha", "Beta", "Gamma"],
                      judge_sequence(5), max_steps=8)
    assert final["termination_reason"] == "STEP_LIMIT"
    assert len(final["evaluation_history"]) < 3 and (tmp_path / "r.pdf").exists()


def test_agent_error_is_recorded_and_flow_continues(tmp_path):
    agents = FakeAgents({"Alpha": 80, "Beta": 75}, fail_tech={"Beta"})
    final, _, log = run(tmp_path, agents, ["Alpha", "Beta"], judge_sequence(5))
    beta = final["evaluation_history"][1]
    assert beta["investment_decision"] == "HOLD" and "tech_summary" in beta["missing_core_information"]
    assert [c for c in agents.calls if c[:2] == ("tech", "Beta")] == [("tech", "Beta", False), ("tech", "Beta", True)]
    assert any(e["node"] == "tech" and e["decision"] == "error" for e in log)
    assert final["recommended_startup"] == "Alpha"


def test_reducers_upsert_history_and_merge_sources():
    old = [{"startup": "A", "v": 1}, {"startup": "B", "v": 1}]
    assert upsert_history(old, [{"startup": "A", "v": 2}, {"startup": "C", "v": 1}]) == [
        {"startup": "A", "v": 2}, {"startup": "B", "v": 1}, {"startup": "C", "v": 1}]
    assert merge_sources({"s1": {"x": 1}}, {"s2": {"x": 2}}) == {"s1": {"x": 1}, "s2": {"x": 2}}


def test_quality_rules_point_rework_to_thin_section():
    content = {"company": "A", "decision_label": "투자 추천", "total": 80, "risk_penalty": 0, "narrative": "",
               "summary": [], "risks": [], "groups": [{"score": 80}],
               "items": [{"group": "market", "score": 10, "max_points": 10, "question": "q", "rationale": "r"},
                         {"group": "technology", "score": 5, "max_points": 15, "question": "q", "rationale": "r"}],
               # 전체 서로 다른 출처 2개(<3) → B1 미달, 출처 1개뿐인 섹션이 보강 대상
               "market": {"market_size": ["시장 규모 [1]"]},
               "tech": {"core_technology": ["기술 [2]", "세계 최초 기술 [2]"]},       # 홍보 표현 → N3 미달
               "competition": {"compare_product": ["비교 [2]"]},
               "references": [("웹페이지", f"Pub{n}(2026). t. x", n) for n in range(1, 3)]}
    verdict = evaluate_report(content, [], attempt=1, judge=judge_sequence(5))
    assert not verdict["passed"]
    assert verdict["items"]["bias"]["checks"]["B1 출처 다양성"]["passed"] is False
    assert verdict["items"]["neutrality"]["checks"]["N3 근거 없는 홍보 표현"]["passed"] is False
    assert verdict["rework_targets"][0] == "market" and "rewrite" in verdict["rework_targets"]
    # 섹션마다 1개 이상 + 전체 3개 이상이면 B1 통과 (세부 시장 자료가 1건뿐이어도)
    content["competition"] = {"compare_product": ["비교 [3]"]}
    content["references"].append(("웹페이지", "Pub3(2026). t. x", 3))
    ok = evaluate_report(content, [], attempt=1, judge=judge_sequence(5))
    assert ok["items"]["bias"]["checks"]["B1 출처 다양성"]["passed"] is True


def test_revise_skips_rework_when_no_more_documents():
    from investment_scout.supervisor.policy import revise

    exhausted = {"claims": [{"source_ids": ["m_siph:p1"]}], "available_documents": 1}
    record = {"startup": "A", "profile": {"name": "A"}, "tech_category": "PHOTONICS", "tech_summary": {},
              "market_analysis": exhausted, "competitor_analysis": {"claims": []}}
    state = {"recommended_startup": "A", "evaluation_history": [record]}
    only_market = revise(state, {"rework_targets": ["market", "rewrite"], "failed_items": ["편향 통제:B1"]})
    assert only_market.next == "report" and "추가 자료 없음" in only_market.reason
    with_competitor = revise(state, {"rework_targets": ["market", "competitor"], "failed_items": ["편향 통제:B4"]})
    assert with_competitor.next == "competitor"   # 웹 검색 기반 관점은 추가 검색 가능

class Crash(BaseException):
    """프로세스 중단 흉내 (노드의 Exception fallback에 잡히지 않음)."""


def test_resume_continues_from_sqlite_checkpoint(tmp_path):
    class CrashOnBeta(FakeAgents):
        def tech(self, state, *, rework):
            if state["current_startup"]["name"] == "Beta":
                raise Crash()
            return super().tech(state, rework=rework)

    crashed = CrashOnBeta({"Alpha": 80, "Beta": 75})
    with pytest.raises(Crash):
        run(tmp_path, crashed, ["Alpha", "Beta"], judge_sequence(5))
    trace_id = (tmp_path / "logs").glob("*.jsonl").__next__().stem
    assert (tmp_path / "ckpt.sqlite").exists()

    agents = FakeAgents({"Alpha": 80, "Beta": 75})
    final, lines, _ = run(tmp_path, agents, ["Alpha", "Beta"], judge_sequence(5), resume=trace_id)
    assert final["trace_id"] == trace_id
    assert [r["startup"] for r in final["evaluation_history"]] == ["Alpha", "Beta"]
    assert not any(name == "Alpha" for _, name, _ in agents.calls)   # 완료된 기업은 다시 평가하지 않음
    assert any("재개" in line for line in lines) and final["report_path"]


def test_resume_without_checkpoint_fails_clearly(tmp_path):
    with pytest.raises(ValueError, match="재개할 체크포인트가 없습니다"):
        run(tmp_path, FakeAgents({"Alpha": 80}), ["Alpha"], judge_sequence(5), resume="inv-missing")
