"""Supervisor 그래프 State.

설계 원칙 (가이드 State 정의표 7개 항목):
- 제어 vs 페이로드 분리: 라우팅·종료·재개에 필요한 최소 제어 메타(ControlFields)와
  에이전트 작업 결과(PayloadFields)를 분리. Supervisor는 제어 필드와 결과의 '충분성 요약'만 보고 분기.
- 관측성 위치: 라우팅 사유·재작업 판단은 State가 아닌 외부 결정 로그(out/logs/{trace_id}.jsonl)와
  LangSmith 트레이스에 기록. State에는 마지막 결정 1건(last_decision)만 둠.
- 지속성 비용: 청크 본문·전체 출처(수백 건)를 State에 넣지 않음. 인용된 출처의 메타데이터만
  sources(source_id → 메타, 근거 문장 300자 제한)에 보관하고, 평가 이력은 근거 본문을 뺀 요약 레코드.
- 상관: trace_id(=체크포인터 thread_id, LangSmith metadata, 결정 로그 파일명)로 State·로그·트레이스 연결.
- 재개/복구: node_status(노드별 마지막 상태)·last_error·candidate_index·rework와 체크포인터로 중단 지점 재개.
- 동시 처리: Supervisor는 한 번에 하위 에이전트 하나만 실행(동시 쓰기 없음). 누적 필드는 reducer로
  병합 규칙을 명시: evaluation_history는 기업명 기준 upsert, sources는 source_id 기준 병합.
- 종료 보장: step_count ≤ max_steps, 관점별 재작업 ≤ MAX_REWORK, 보고서 수정 ≤ MAX_REPORT_ATTEMPTS,
  LangGraph recursion_limit을 안전망으로 사용.
"""

from __future__ import annotations

from typing import Annotated, Any, Dict, List, Optional, TypedDict

PERSPECTIVES = ("tech", "market", "competitor")
MAX_REWORK = 1             # 관점별 근거 부족 재작업 상한 (기업당)
MAX_REPORT_ATTEMPTS = 2    # 품질 평가 미달 시 보고서 개선 루프 상한
STEPS_PER_CANDIDATE = 14   # 기업당 최대 노드 방문 수 추정 (supervisor 왕복 + 재작업 포함)


def upsert_history(old: List[Dict[str, Any]], new: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """평가 이력 reducer: 같은 기업은 교체(품질 루프 재평가), 새 기업은 추가. 평가 순서 유지."""
    merged = {record["startup"]: record for record in old or []}
    order = [record["startup"] for record in old or []]
    for record in new or []:
        if record["startup"] not in merged:
            order.append(record["startup"])
        merged[record["startup"]] = record
    return [merged[name] for name in order]


def merge_sources(old: Dict[str, Dict], new: Dict[str, Dict]) -> Dict[str, Dict]:
    """출처 reducer: source_id 기준 병합 (같은 출처를 여러 기업이 인용해도 하나로)."""
    return {**(old or {}), **(new or {})}


class SupervisorState(TypedDict, total=False):
    # ── 작업 페이로드 ─────────────────────────────────────────────
    target_domain: str
    candidates: List[str]                          # 적격 후보 기업명 (스타트업 탐색 결과)
    profiles: Dict[str, Dict[str, Any]]            # 기업명 → 프로필 (설립·투자 단계·제품·출처 ID)
    scout_summary: Dict[str, Any]                  # 국내·해외 확보 수 등 탐색 요약
    current: Dict[str, Any]                        # 현재 평가 중 기업 프로필
    tech_summary: Dict[str, Any]                   # 현재 기업 관점별 결과 (기술 요약 + 분류 근거)
    tech_category: str
    market_analysis: Dict[str, Any]
    competitor_analysis: Dict[str, Any]
    evaluation_scores: Dict[str, float]            # 현재 기업 투자 판단 결과
    evaluation_details: Dict[str, Any]
    investment_decision: str
    hold_reason: Optional[str]
    evaluation_history: Annotated[List[Dict[str, Any]], upsert_history]   # 기업별 확정 결과 (reducer)
    sources: Annotated[Dict[str, Dict[str, Any]], merge_sources]          # 인용 출처 메타 (reducer)
    final_ranking: List[Dict[str, Any]]
    recommended_startup: str
    final_report: str                              # Markdown 요약 (PDF 경로는 report_path)
    report_path: str
    quality: Dict[str, Any]                        # 최근 품질 평가 verdict (항목별 통과·점수·재작업 대상)

    # ── 제어 메타데이터 (조정·종료·재개에 필요한 최소치) ─────────────
    trace_id: str                                  # ★ 외부 로그·LangSmith·체크포인터 thread_id 연결 키
    phase: str                                     # evaluate → report → done (revise: 품질 루프 재작업)
    candidate_index: int                           # 다음에 평가할 후보 위치
    next_agent: str                                # Supervisor가 고른 다음 노드
    rework: Dict[str, int]                         # 현재 기업의 관점별 재작업 횟수
    rework_reason: Dict[str, str]                  # 재작업 지시 사유 (하위 에이전트가 검색 전략 변경에 사용)
    report_attempts: int                           # 보고서 생성 횟수 (품질 루프 상한 관리)
    report_mode: str                               # normal / strict(품질 미달 후: 근거 없는 문장 제외·표현 완화)
    step_count: int                                # 종료 가드
    max_steps: int
    node_status: Dict[str, str]                    # 노드별 마지막 상태 (ok / insufficient / error)
    last_error: Optional[str]
    last_decision: Dict[str, str]                  # 마지막 라우팅 결정 1건 (전체 이력은 외부 로그)
    termination_reason: str


PER_CANDIDATE_RESET = {
    "tech_summary": {}, "tech_category": "", "market_analysis": {}, "competitor_analysis": {},
    "evaluation_scores": {}, "evaluation_details": {}, "investment_decision": "", "hold_reason": None,
    "rework": {}, "rework_reason": {},
}


def initial_state(target_domain: str, *, trace_id: str, max_candidates: int) -> SupervisorState:
    return SupervisorState(
        target_domain=target_domain, candidates=[], profiles={}, scout_summary={}, current={},
        evaluation_history=[], sources={}, final_ranking=[], recommended_startup="", final_report="",
        report_path="", quality={}, trace_id=trace_id, phase="evaluate", candidate_index=0, next_agent="",
        report_attempts=0, report_mode="normal", step_count=0, max_steps=STEPS_PER_CANDIDATE * max_candidates + 20,
        node_status={}, last_error=None, last_decision={}, termination_reason="", **PER_CANDIDATE_RESET,
    )
