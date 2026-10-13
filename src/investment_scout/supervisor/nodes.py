"""Supervisor 그래프 노드: 조정 계층(supervisor)과 하위 에이전트·마무리 노드.

하위 에이전트는 Supervisor가 State에 남긴 지시(rework_reason)만 읽고 결과를 State에 쓴 뒤 Supervisor로
돌아간다. 하위 에이전트끼리는 직접 호출·통신하지 않는다.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from investment_scout.contracts import empty_analysis_result, find_missing_core_information
from investment_scout.nodes.orchestration import rank_candidates
from investment_scout.reporting.content import build_report_content
from investment_scout.reporting.pdf_report import build_markdown_report, render_pdf_report
from investment_scout.state import DECISION_HOLD, DECISION_RECOMMENDED
from investment_scout.supervisor.observability import DecisionLog
from investment_scout.supervisor.policy import FIELD_OF, decide, sufficiency
from investment_scout.supervisor.quality import QualityJudgement, evaluate_report, openai_judge

SOURCE_KEYS = ("source_id", "url", "title", "publisher", "published_at", "accessed_at", "document_type",
               "document_id", "page", "page_basis", "company", "url_fetched", "supports")


def compact_source(source: dict) -> dict:
    """State 저장용 출처: 청크 본문·로컬 경로는 빼고 메타데이터와 짧은 근거 문장만 (지속성 비용)."""
    kept = {k: source[k] for k in SOURCE_KEYS if k in source}
    kept["evidence"] = (source.get("evidence") or "")[:300]
    return kept


def compact_analysis(result: dict) -> dict:
    return {k: v for k, v in (result or {}).items() if k != "evidence"}


def sources_from(*results_or_lists) -> dict[str, dict]:
    merged = {}
    for item in results_or_lists:
        rows = item.get("evidence", []) if isinstance(item, dict) else item
        for row in rows or []:
            if isinstance(row, dict) and row.get("source_id"):
                merged[row["source_id"]] = compact_source(row)
    return merged


class AgentSet:
    """기존 에이전트 래퍼: 재작업 지시가 있으면 검색 전략을 넓혀 다시 수행."""

    def __init__(self, *, tech_index, market_index=None, web_search=None, judge=None, min_score=0.30):
        from investment_scout.agents.competitor import CompetitorAnalyst
        from investment_scout.agents.investment_judge import InvestmentJudge
        from investment_scout.agents.market_analyst import MarketAnalyst
        from investment_scout.agents.tech_analyst import TechAnalyst
        from investment_scout.agents.tech_classifier import TechClassifier

        self.tech_index, self.min_score = tech_index, min_score
        self._tech, self._classifier = TechAnalyst, TechClassifier
        self.market_agent = MarketAnalyst(market_index, min_score=min_score) if market_index else None
        self.web_search = web_search
        self._competitor = CompetitorAnalyst
        self.judge_agent = judge or InvestmentJudge()

    def warmup(self) -> None:
        """메인 스레드에서 임베딩 모델 로드·고정 질문 벡터 캐시.

        그래프 노드는 작업 스레드에서 실행되므로, 노드 안에서 torch·sentence-transformers를 처음 import하면
        LangSmith 백그라운드 스레드와 import 잠금이 엉켜 멈출 수 있음(실측). 미리 로드해 이를 방지하고 속도도 개선.
        """
        import openai  # noqa: F401  (LLM 클라이언트도 노드 스레드에서 처음 import되지 않도록)
        from investment_scout.agents.market_analyst import QUESTION
        self.tech_index.prepare_queries([self._tech.question(""), self._classifier.question("")])
        if self.market_agent is not None:
            self.market_agent.index.prepare_queries([QUESTION])

    def tech(self, state: dict, *, rework: bool) -> dict:
        min_score = 0.22 if rework else self.min_score  # 재작업: 검색 하한을 낮춰 더 넓게
        update = self._tech(self.tech_index, min_score=min_score)(state)
        update.update(self._classifier(self.tech_index, min_score=min_score)({**state, **update}))
        # 확보 가능한 자료 수: Supervisor가 '더 찾을 자료가 있는가'를 State로 판단하는 데 사용
        name = state["current_startup"]["name"]
        update["tech_summary"]["available_documents"] = len(
            {c.document_id for c in self.tech_index.chunks if c.company == name})
        return update

    def market(self, state: dict, *, rework: bool) -> dict:
        if self.market_agent is None:
            return {"market_analysis": empty_analysis_result(missing_information=["시장 인덱스 없음"])}
        # 재작업: 기술 분류와 무관하게 전체 세부 시장에서 근거 탐색
        update = self.market_agent({**state, "tech_category": "" if rework else state.get("tech_category", "")})
        segments = set(update["market_analysis"].get("segments") or [])
        update["market_analysis"]["available_documents"] = len(
            {c.document_id for c in self.market_agent.index.chunks if c.company in segments})
        return update

    def competitor(self, state: dict, *, rework: bool) -> dict:
        if self.web_search is None:
            return {"competitor_analysis": empty_analysis_result(missing_information=["웹 검색 미설정"])}
        agent = self._competitor(self.web_search)
        if rework:  # 재작업: 한국어·반대 근거(한계·리스크·경쟁사 강점) 검색어 추가
            base = agent.queries
            agent.queries = lambda s: [*base(s), f"{s['current_startup']['name']} 경쟁사",
                                       f"{s['current_startup']['name']} challenges limitations risks",
                                       f"competitors advantages over {s['current_startup']['name']} funding customers"]
        return agent(state)

    def judge(self, state: dict) -> dict:
        return self.judge_agent(state)


def report_state(state: dict) -> dict:
    """보고서 내용 추출기가 읽는 형태로 변환 (출처는 인용된 것만 State에 있음)."""
    return {
        "target_domain": state.get("target_domain"), "evaluation_history": state.get("evaluation_history") or [],
        "final_ranking": state.get("final_ranking") or [], "recommended_startup": state.get("recommended_startup", ""),
        "candidate_startups": state.get("candidates") or [], "termination_reason": state.get("termination_reason", ""),
        "scout_result": {"counts": (state.get("scout_summary") or {}).get("counts", {})},
        "source_evidence": {"all": list((state.get("sources") or {}).values())},
    }


@dataclass
class Nodes:
    agents: Any
    scout: Any
    log: DecisionLog
    report_path: Path
    max_candidates: int
    quality_judge: Callable[[dict], QualityJudgement] = openai_judge

    # ── 조정 계층 ────────────────────────────────────────────────
    def supervisor(self, state: dict) -> dict:
        decision = decide(state)
        self.log.write("supervisor", decision.next, decision.reason,
                       company=(decision.update.get("current") or state.get("current") or {}).get("name"),
                       step=state.get("step_count", 0) + 1)
        return {**decision.update, "next_agent": decision.next, "step_count": state.get("step_count", 0) + 1,
                "last_decision": {"next": decision.next, "reason": decision.reason}}

    @staticmethod
    def route(state: dict) -> str:
        return state["next_agent"]

    # ── 하위 에이전트 ─────────────────────────────────────────────
    def _input(self, state: dict) -> dict:
        profile = state.get("current") or {}
        sources = state.get("sources") or {}
        own = [sources[sid] for sid in profile.get("source_ids", []) if sid in sources]
        return {"current_startup": profile, "tech_summary": state.get("tech_summary") or {},
                "tech_category": state.get("tech_category", ""), "market_analysis": state.get("market_analysis") or {},
                "competitor_analysis": state.get("competitor_analysis") or {},
                "source_evidence": {profile.get("name"): own}}

    def _run(self, perspective: str, state: dict, call) -> dict:
        field = FIELD_OF[perspective]
        status = dict(state.get("node_status") or {})
        try:
            update = call(self._input(state), rework=perspective in (state.get("rework_reason") or {}))
            error = None
        except Exception as exc:  # fallback: 오류를 근거 부족으로 기록 → Supervisor가 재시도 후 계속
            update = {field: {**empty_analysis_result(status="ERROR", missing_information=[f"오류: {exc}"]),
                              "evidence": []}}
            error = f"{perspective}: {type(exc).__name__}: {exc}"
        result = update.get(field) or {}
        ok, why = sufficiency(perspective, result)
        status[perspective] = "error" if error else ("ok" if ok else "insufficient")
        own = (update.get("source_evidence") or {}).get((state.get("current") or {}).get("name"), [])
        out = {field: compact_analysis(result), "sources": sources_from(result, own), "node_status": status}
        if "tech_category" in update:
            out["tech_category"] = update["tech_category"]
        if error:
            out["last_error"] = error
        self.log.write(perspective, status[perspective], why, company=(state.get("current") or {}).get("name"))
        return out

    def tech(self, state: dict) -> dict:
        return self._run("tech", state, self.agents.tech)

    def market(self, state: dict) -> dict:
        return self._run("market", state, self.agents.market)

    def competitor(self, state: dict) -> dict:
        return self._run("competitor", state, self.agents.competitor)

    def judge(self, state: dict) -> dict:
        status = dict(state.get("node_status") or {})
        try:
            update = self.agents.judge(self._input(state))
            status["judge"] = "ok"
        except Exception as exc:  # fallback: 판단 실패 기업은 보류로 기록하고 계속
            update = {"investment_decision": DECISION_HOLD, "hold_reason": f"투자 판단 오류: {exc}",
                      "evaluation_scores": {}, "evaluation_details": {}}
            status["judge"] = "error"
            update["last_error"] = f"judge: {type(exc).__name__}: {exc}"
        self.log.write("judge", update["investment_decision"], update.get("hold_reason") or "기준 통과",
                       company=(state.get("current") or {}).get("name"),
                       total=(update.get("evaluation_scores") or {}).get("total"))
        return {**update, "node_status": status}

    # ── 평가 저장·순위·보고서·품질 ───────────────────────────────────
    def record(self, state: dict) -> dict:
        profile = state["current"]
        analyses = {field: compact_analysis(state.get(field)) for field in FIELD_OF.values()}
        missing = find_missing_core_information(analyses)
        decision, reason = state.get("investment_decision") or DECISION_HOLD, state.get("hold_reason")
        if missing and decision == DECISION_RECOMMENDED:
            decision = DECISION_HOLD
            reason = f"핵심 정보 부족으로 보류: {', '.join(missing)}"
        record = {
            "startup": profile["name"], "profile": profile, "tech_category": state.get("tech_category", ""),
            **analyses, "segments": (analyses["market_analysis"] or {}).get("segments", []),
            "evaluation_scores": state.get("evaluation_scores") or {},
            "evaluation_details": state.get("evaluation_details") or {},
            "investment_decision": decision, "hold_reason": reason, "missing_core_information": missing,
            "rework": state.get("rework") or {}, "rework_reason": state.get("rework_reason") or {},
        }
        advance = 1 if state.get("phase") == "evaluate" else 0  # 품질 루프 재평가는 인덱스 유지
        self.log.write("record", decision, reason or "기준 통과", company=profile["name"])
        return {"evaluation_history": [record], "current": {},
                "candidate_index": state.get("candidate_index", 0) + advance}

    def rank(self, state: dict) -> dict:
        ranking = rank_candidates(state.get("evaluation_history") or [])
        chosen = next((row["startup"] for row in ranking if row["qualified"]), "")
        reason = state.get("termination_reason") or ("RECOMMENDED_FOUND" if chosen else "ALL_HOLD")
        self.log.write("rank", chosen or "전원 보류", f"기준 통과 {sum(r['qualified'] for r in ranking)}개사")
        return {"final_ranking": ranking, "recommended_startup": chosen, "termination_reason": reason}

    def _content(self, state: dict) -> dict:
        return build_report_content(report_state(state), strict=state.get("report_mode") == "strict")

    def report(self, state: dict) -> dict:
        content = self._content(state)
        result = render_pdf_report(content, self.report_path, process=process_summary(state))
        attempt = state.get("report_attempts", 0) + 1
        self.log.write("report", f"attempt {attempt}", f"{result['pages']}쪽, 모드 {state.get('report_mode')}")
        return {"report_attempts": attempt, "report_path": result["path"],
                "final_report": build_markdown_report(content)}

    def quality(self, state: dict) -> dict:
        content = self._content(state)
        record = next((r for r in state.get("evaluation_history") or [] if r["startup"] == content["company"]), {})
        pairs = [{"field": (c.get("data_keys") or [""])[0], "claim": c["text"], "quote": c["citations"][0]["quote"]}
                 for field in FIELD_OF.values() for c in (record.get(field) or {}).get("claims", [])
                 if c.get("citations")][:12]
        verdict = evaluate_report(content, pairs, attempt=state.get("report_attempts", 0), judge=self.quality_judge)
        # 품질 평가 결과를 보고서에 반영해 최종본 재렌더링 (내용·점수는 그대로)
        render_pdf_report(content, self.report_path, process=process_summary(state), quality=verdict)
        self.log.write("quality", "pass" if verdict["passed"] else "fail",
                       ", ".join(verdict["failed_items"]) or "3개 항목 통과", targets=verdict["rework_targets"])
        return {"quality": verdict}

    def scout_node(self, state: dict) -> dict:
        result = self.scout.run(state["target_domain"])
        names = result["candidate_startups"][: self.max_candidates]
        profiles = {name: result["candidate_profiles"][name] for name in names}
        sources = sources_from(*[result["source_evidence"].get(name, []) for name in names])
        self.log.write("scout", f"{len(names)}개사", "적격성 확인 완료")
        return {"candidates": names, "profiles": profiles, "sources": sources,
                "scout_summary": {"counts": (result.get("scout_result") or {}).get("counts", {}),
                                  "eligible": len(result["candidate_startups"])},
                "node_status": {"scout": "ok"}}


def process_summary(state: dict) -> dict:
    """보고서 '평가 과정' 페이지용: 기업별 재작업과 Supervisor 단계 수 (상세 결정 로그는 외부 파일)."""
    history = state.get("evaluation_history") or []
    return {
        "steps": state.get("step_count", 0), "trace_id": state.get("trace_id", ""),
        "report_attempts": state.get("report_attempts", 0),
        "reworks": {r["startup"]: {p: c for p, c in (r.get("rework") or {}).items() if c}
                    for r in history if any((r.get("rework") or {}).values())},
        "rework_reasons": {r["startup"]: r.get("rework_reason") or {} for r in history if r.get("rework_reason")},
    }
