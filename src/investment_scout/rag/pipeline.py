"""통합 실행: 설계서 그래프를 실제 탐색·기술 RAG 노드로 돌리며 노드별 경과 출력.

시장성·경쟁사·투자 판단·보고서는 역할 3·4 구현 전까지 기존 근거 기반 노드
(nodes/production.py)를 자리 표시로 사용: 근거가 없으면 INSUFFICIENT_DATA, 판단은 HOLD.
"""

from investment_scout.agents.startup_scout import StartupScout, make_scout_node
from investment_scout.agents.tech_analyst import TechAnalyst
from investment_scout.agents.tech_classifier import TechClassifier
from investment_scout.graph import build_graph, recommended_recursion_limit
from investment_scout.nodes.production import make_production_nodes
from investment_scout.rag.integration import with_technical_sources
from investment_scout.search import get_search_provider
from investment_scout.state import create_initial_state

PLACEHOLDER = "(역할 3·4 구현 전 자리 표시 노드)"


def _claims(summary: dict, keys: tuple[str, ...], limit: int = 2) -> list[str]:
    lines = []
    for claim in summary.get("claims", []):
        if set(claim.get("data_keys", [])) & set(keys) and len(lines) < limit:
            citation = claim["citations"][0]
            lines.append(f"      · [{claim['data_keys'][0]}] {claim['text'][:70]}"
                         f"  ← {citation['url'][:45]} p{citation['page']}")
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
    if node in ("market_analysis", "competitor_analysis"):
        label = "📊 시장성 평가" if node == "market_analysis" else "🥊 경쟁사 비교"
        return [f"  {label}: {update[node]['status']} {PLACEHOLDER}"]
    if node == "investment_decision":
        return [f"  🧮 투자 판단: {update['investment_decision']} {PLACEHOLDER}"]
    if node == "record_evaluation":
        return [f"  💾 평가 저장: 누적 {len(update.get('evaluated_startups', []))}개"]
    if node == "generate_report":
        return ["", f"📝 보고서 생성: 종료 사유 {state.get('termination_reason')} {PLACEHOLDER}"]
    return [f"  {node}"]


def run_pipeline(index, *, max_candidates: int, min_score: float, echo=print) -> dict:
    scout = StartupScout(search_provider=get_search_provider("mock"))
    rest = make_production_nodes(retriever=None)
    app = build_graph(
        scout_node=with_technical_sources(make_scout_node(scout), index),
        tech_node=TechAnalyst(index, min_score=min_score),
        category_node=TechClassifier(index, min_score=min_score),
        market_node=rest["market_node"], competitor_node=rest["competitor_node"],
        decision_node=rest["decision_node"], report_node=rest["report_node"],
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
