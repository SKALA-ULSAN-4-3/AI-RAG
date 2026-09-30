"""통합 실행: 설계서 그래프를 실제 에이전트로 돌리며 노드별 경과 출력.

탐색·기술 요약·기술 분류(역할 1·2), 시장성·경쟁사·투자 판단(역할 3)은 실제 에이전트.
보고서(역할 4)는 구현 전까지 기존 근거 기반 노드(nodes/production.py)를 자리 표시로 사용.
시장 인덱스가 없으면 시장성, Tavily 키가 없으면 경쟁사 노드도 자리 표시로 대체하고 그 사실을 출력.
"""

from investment_scout.agents.competitor import CompetitorAnalyst
from investment_scout.agents.investment_judge import SCORECARD, InvestmentJudge
from investment_scout.agents.market_analyst import MarketAnalyst
from investment_scout.agents.startup_scout import StartupScout, make_scout_node
from investment_scout.agents.tech_analyst import TechAnalyst
from investment_scout.agents.tech_classifier import TechClassifier
from investment_scout.graph import build_graph, recommended_recursion_limit
from investment_scout.nodes.production import make_production_nodes
from investment_scout.rag.integration import with_technical_sources
from investment_scout.search import get_search_provider
from investment_scout.state import create_initial_state

PLACEHOLDER = "(자리 표시 노드)"


def _claims(summary: dict, keys: tuple[str, ...], limit: int = 2) -> list[str]:
    lines = []
    for claim in summary.get("claims", []):
        if set(claim.get("data_keys", [])) & set(keys) and len(lines) < limit:
            citation = claim["citations"][0]
            lines.append(f"      · [{claim['data_keys'][0]}] {claim['text'][:70]}"
                         f"  ← {citation['url'][:45]} p{citation['page']}")
    return lines


def _reason(text: str, limit: int = 70) -> str:
    text = text.replace("[코드 규칙: 인용 근거에 필요한 정보가 없어 ", "(필요 정보 없음 → ").replace(" 상한 적용]", " 상한)")
    return text if len(text) <= limit else text[: limit - 1] + "…"


def score_lines(details: dict) -> list[str]:
    """점수표: 설계서 항목별 소계, 체크리스트 점수와 한 줄 이유, 리스크 감점."""
    lines = []
    for key, label, weight, _ in SCORECARD:
        lines.append(f"      ■ {label} {details['groups'][key]}/{weight}")
        for item in (i for i in details["items"] if i["group"] == key):
            lines.append(f"        - {item['question']} {item['score']}/{item['max_points']}: {_reason(item['rationale'])}")
    penalized = details.get("penalized_risk_types", [])
    lines.append(f"      ■ 리스크 감점 {details['risk_penalty']}점 (치명 리스크 유형: {', '.join(penalized) or '없음'})")
    for risk in details["risks"]:
        mark = "치명" if risk["fatal"] else "참고"
        lines.append(f"        - [{risk['type']}·{mark}] {_reason(risk['description'])}")
    subtotal = sum(details["groups"].values())
    lines.append(f"      = 항목 합계 {subtotal} + 리스크 {details['risk_penalty']} = 총점 {details['total']}점")
    return lines


def describe(node: str, update: dict, state: dict) -> list[str]:
    """노드 출력 요약: 각 에이전트가 무엇을 채웠는지 한눈에 확인."""
    if node == "scout_candidates":
        counts = (update.get("scout_result") or {}).get("counts", {})
        regions = ", ".join(f"{k} {v['secured']}/{v['target']}" for k, v in counts.items())
        sources = sum(len(v) for v in update.get("source_evidence", {}).values())
        return [f"🔍 스타트업 탐색: 적격 후보 {len(update.get('candidate_startups', []))}개 ({regions}), "
                f"출처 {sources}건 (기술 청크 포함)"]
    if node == "select_candidate":
        name = update["current_startup"]["name"]
        position = state.get("candidate_index", 0) + 1
        return ["", f"▶ [{position}/{min(state['max_candidates'], len(state['candidate_startups']))}] {name}"]
    if node == "tech_analysis":
        summary = update["tech_summary"]
        found = [k for k in summary.get("data", {}) if k != "categories"]
        return [f"  🗜️ 기술 요약: 주장 {len(summary['claims'])}개, 항목 {found or '없음'}",
                *_claims(summary, ("core_technology", "advantages", "commercialization"))]
    if node == "tech_classification":
        return [f"  🔬 기술 분류: {update['tech_category']}",
                *_claims(update["tech_summary"], ("categories",), limit=1)]
    if node == "market_analysis":
        result = update[node]
        segments = ", ".join(result.get("segments", [])) or PLACEHOLDER
        return [f"  📊 시장성 평가: {result['status']}, 주장 {len(result['claims'])}개 (세부 시장: {segments})",
                *_claims(result, ("market_size", "growth_rate"))]
    if node == "competitor_analysis":
        result = update[node]
        rivals = ", ".join(result["data"].get("main_competitors", [])) or "없음"
        return [f"  🥊 경쟁사 비교: {result['status']}, 경쟁사 [{rivals}]",
                *_claims(result, ("competitive_comparison", "entry_barriers"), limit=1)]
    if node == "investment_decision":
        details = update.get("evaluation_details") or {}
        if not details:
            return [f"  🧮 투자 판단: {update['investment_decision']} {PLACEHOLDER}"]
        return [f"  🧮 투자 판단: {details['decision']} — 총점 {details['total']}/100 (추천 기준 {details['threshold']}점)",
                *score_lines(details)]
    if node == "record_evaluation":
        record = update["evaluation_history"][-1]
        lines = [f"  💾 평가 저장: 최종 {record['investment_decision']}, 누적 {len(update['evaluated_startups'])}개"]
        if record["missing_core_information"]:
            fields = ", ".join(record["missing_core_information"])
            lines.append(f"      ⚠️ 핵심 정보 부족({fields})으로 HOLD 강제 — record_evaluation 규칙")
        return lines
    if node == "generate_report":
        return ["", f"📝 보고서 생성: 종료 사유 {state.get('termination_reason')} {PLACEHOLDER}"]
    return [f"  {node}"]


def run_pipeline(index, *, max_candidates: int, min_score: float, market_index=None,
                 web_search=None, judge=None, echo=print) -> dict:
    scout = StartupScout(search_provider=get_search_provider("mock"))
    rest = make_production_nodes(retriever=None)
    if market_index is None:
        echo(f"⚠️ 시장 인덱스 없음: 시장성 평가는 {PLACEHOLDER}")
    if web_search is None:
        echo(f"⚠️ 웹 검색(Tavily) 미설정: 경쟁사 비교는 {PLACEHOLDER}")
    app = build_graph(
        scout_node=with_technical_sources(make_scout_node(scout), index),
        tech_node=TechAnalyst(index, min_score=min_score),
        category_node=TechClassifier(index, min_score=min_score),
        market_node=MarketAnalyst(market_index, min_score=min_score) if market_index else rest["market_node"],
        competitor_node=CompetitorAnalyst(web_search) if web_search else rest["competitor_node"],
        decision_node=judge or InvestmentJudge(), report_node=rest["report_node"],
    )
    state = dict(create_initial_state("Semiconductor", max_candidates=max_candidates))
    config = {"recursion_limit": recommended_recursion_limit(max_candidates)}
    for step in app.stream(state, config=config, stream_mode="updates"):
        for node, update in step.items():
            # State 규칙: 모든 필드는 덮어쓰기이므로 병합 결과가 그래프 State와 같음.
            state.update(update or {})
            for line in describe(node, update or {}, state):
                echo(line, flush=True)
    return state
