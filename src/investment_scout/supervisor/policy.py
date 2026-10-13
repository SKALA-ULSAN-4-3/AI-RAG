"""Supervisor 판단 규칙: 현재 State(수집된 관점·근거 충분도·품질 verdict)만 보고 다음 노드를 결정.

순서를 하드코딩하지 않는다. 매 방문마다
  1) 결과가 없는 관점 → 해당 에이전트 (의존성: 시장은 기술 분류, 경쟁사는 기술 근거, 판단은 세 관점이 필요)
  2) 근거가 부족한 관점 → 같은 에이전트에 재작업 지시 (관점별 MAX_REWORK 상한)
  3) 판단까지 끝난 기업 → 평가 저장, 다음 기업
  4) 전체 평가 완료 → 순위 → 보고서 → 품질 평가
  5) 품질 미달 → 실패 항목에 맞는 에이전트 재작업 또는 보고서 개선 (MAX_REPORT_ATTEMPTS 상한)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from investment_scout.supervisor.state import MAX_REPORT_ATTEMPTS, MAX_REWORK, PER_CANDIDATE_RESET

FIELD_OF = {"tech": "tech_summary", "market": "market_analysis", "competitor": "competitor_analysis"}
AGENT_LABEL = {"tech": "기술 요약·분류", "market": "시장성 평가", "competitor": "경쟁사 비교", "judge": "투자 판단"}


@dataclass
class Decision:
    next: str
    reason: str
    update: dict[str, Any] = field(default_factory=dict)


def documents(result: dict) -> set[str]:
    """주장들이 인용한 서로 다른 문서 (청크 ID의 문서 부분)."""
    return {sid.split(":")[0] for claim in (result or {}).get("claims", []) for sid in claim.get("source_ids", [])}


def sufficiency(perspective: str, result: dict) -> tuple[bool, str]:
    """근거 충분성 (코드 규칙): 필수 항목 + 근거 수 + 서로 다른 출처 수."""
    data = (result or {}).get("data") or {}
    claims = (result or {}).get("claims") or []
    docs = documents(result)
    if perspective == "tech":
        if not data.get("core_technology"):
            return False, "핵심 기술 근거 없음"
        if len(claims) < 2:
            return False, f"기술 근거 주장 {len(claims)}건(2건 미만)"
        return True, f"기술 주장 {len(claims)}건·출처 {len(docs)}개"
    if perspective == "market":
        missing = [k for k in ("market_size", "growth_rate") if not data.get(k)]
        if missing:
            return False, f"시장 필수 항목 없음: {', '.join(missing)}"
        return True, f"시장 규모·성장률 확보, 출처 {len(docs)}개"
    rivals = data.get("main_competitors") or []
    compared = [c for c in claims if any(k.startswith("compare_") for k in c.get("data_keys", []))]
    if len(rivals) < 2:
        return False, f"경쟁사 {len(rivals)}곳(2곳 미만)"
    if not compared:
        return False, "경쟁사 항목별 비교 근거 없음"
    if len(docs) < 2:
        return False, f"경쟁 근거 출처 {len(docs)}개(단일 출처)"
    return True, f"경쟁사 {len(rivals)}곳·비교 {len(compared)}건·출처 {len(docs)}개"


def next_for_company(state: dict) -> Decision:
    """현재 기업: 누락 관점 → 근거 부족 재작업 → 판단 → 저장."""
    rework = dict(state.get("rework") or {})
    reasons = dict(state.get("rework_reason") or {})
    for perspective in ("tech", "market", "competitor"):
        result = state.get(FIELD_OF[perspective]) or {}
        if not result:
            return Decision(perspective, f"{AGENT_LABEL[perspective]} 결과 없음 → 실행")
        ok, why = sufficiency(perspective, result)
        # 품질 루프 재평가(revise)는 지정 관점만 다시 수행하므로 관점별 재작업 검사를 건너뜀
        if not ok and state.get("phase") != "revise" and rework.get(perspective, 0) < MAX_REWORK:
            rework[perspective] = rework.get(perspective, 0) + 1
            reasons[perspective] = why
            return Decision(perspective, f"근거 부족({why}) → {AGENT_LABEL[perspective]} 재작업 "
                                         f"{rework[perspective]}/{MAX_REWORK}",
                            {"rework": rework, "rework_reason": reasons})
    if not state.get("investment_decision"):
        gaps = [AGENT_LABEL[p] for p in FIELD_OF if not sufficiency(p, state.get(FIELD_OF[p]) or {})[0]]
        if gaps:
            return Decision("judge", f"{', '.join(gaps)} 근거 보강 상한 도달 → 확보된 근거로 투자 판단")
        return Decision("judge", "세 관점 근거 충분 → 투자 판단")
    return Decision("record", f"투자 판단 완료({state['investment_decision']}) → 평가 저장")


def decide(state: dict) -> Decision:
    """Supervisor 결정: 단계(phase)·현재 기업·품질 verdict·상한을 보고 다음 노드 선택."""
    phase = state.get("phase", "evaluate")
    if state.get("step_count", 0) >= state.get("max_steps", 10**6) and phase in ("evaluate", "revise"):
        return Decision("rank", "단계 상한 도달 → 평가 중단하고 순위 선정",
                        {"phase": "report", "termination_reason": "STEP_LIMIT", "current": {}})
    if phase in ("evaluate", "revise"):
        if state.get("current"):
            return next_for_company(state)
        index, candidates = state.get("candidate_index", 0), state.get("candidates") or []
        if phase == "evaluate" and index < len(candidates):
            name = candidates[index]
            profile = (state.get("profiles") or {}).get(name, {"name": name})
            decision = next_for_company({**state, **PER_CANDIDATE_RESET, "current": profile})
            decision.reason = f"[{index + 1}/{len(candidates)}] {name} 평가 시작 · {decision.reason}"
            decision.update = {**PER_CANDIDATE_RESET, "current": profile, **decision.update}
            return decision
        label = "재평가 반영" if phase == "revise" else f"{len(candidates)}개사 평가 완료"
        return Decision("rank", f"{label} → 순위 선정", {"phase": "report"})
    if phase == "report":
        return Decision("report", "순위 확정 → 보고서 작성", {"phase": "quality"})
    if phase == "quality":
        verdict = state.get("quality") or {}
        if verdict.get("attempt") != state.get("report_attempts"):
            return Decision("quality", "보고서 생성 완료 → 품질 평가")
        if verdict.get("passed"):
            return Decision("end", "품질 평가 통과 → 종료", {"phase": "done", "termination_reason":
                                                       state.get("termination_reason") or "QUALITY_PASSED"})
        if state.get("report_attempts", 0) >= MAX_REPORT_ATTEMPTS:
            return Decision("end", f"품질 미달이지만 개선 상한({MAX_REPORT_ATTEMPTS}회) 도달 → 한계 명시 후 종료",
                            {"phase": "done", "termination_reason": "QUALITY_LIMIT"})
        return revise(state, verdict)
    return Decision("end", "종료")


def can_gather_more(result: dict) -> bool:
    """더 확보할 자료가 있는가 (State 기반): 확보 가능 문서 수가 기록돼 있고 이미 모두 인용했으면 False.

    기록이 없는 관점(웹 검색 기반 경쟁사 비교)은 추가 검색이 가능하다고 본다.
    """
    available = (result or {}).get("available_documents")
    return available is None or len(documents(result)) < available


def revise(state: dict, verdict: dict) -> Decision:
    """품질 미달 대응: 근거 보강이 필요하면 선정 기업의 해당 관점 재작업, 아니면 보고서 개선(엄격 모드).

    더 확보할 자료가 없는 관점은 재조사하지 않는다(관련 없는 자료가 섞여 판단이 흔들리는 것을 방지).
    """
    failed = ", ".join(verdict.get("failed_items") or []) or "기준 미달"
    chosen = state.get("recommended_startup") or (state.get("final_ranking") or [{}])[0].get("startup")
    record = next((r for r in state.get("evaluation_history") or [] if r["startup"] == chosen), None)
    viable = [t for t in verdict.get("rework_targets") or []
              if t in FIELD_OF and record and can_gather_more(record.get(FIELD_OF[t]))]
    target = viable[0] if viable else "rewrite"
    if target in FIELD_OF:
        if record:
            loaded = {
                "current": record["profile"], "tech_summary": record["tech_summary"],
                "tech_category": record["tech_category"], "market_analysis": record["market_analysis"],
                "competitor_analysis": record["competitor_analysis"], FIELD_OF[target]: {},
                "evaluation_scores": {}, "evaluation_details": {}, "investment_decision": "", "hold_reason": None,
                "rework": record.get("rework") or {},
                "rework_reason": {**(record.get("rework_reason") or {}),
                                  target: f"품질 평가 미달({failed}): 추가·반대 근거 보강"},
            }
            return Decision(target, f"품질 미달({failed}) → {chosen} {AGENT_LABEL[target]} 근거 보강 재작업",
                            {"phase": "revise", "report_mode": "strict", **loaded})
    skipped = [t for t in verdict.get("rework_targets") or [] if t in FIELD_OF]
    note = f", {'·'.join(AGENT_LABEL[t] for t in skipped)} 추가 자료 없음" if skipped else ""
    return Decision("report", f"품질 미달({failed}{note}) → 근거 없는 문장 제외·표현 완화한 보고서 재작성",
                    {"phase": "quality", "report_mode": "strict"})
