"""Supervisor 그래프 실행: 단계별 결정·결과 출력, 최종 State 저장, LangSmith·외부 로그와 trace_id 연결."""

from __future__ import annotations

import json
from pathlib import Path
import sqlite3

from langgraph.checkpoint.sqlite import SqliteSaver

from investment_scout.supervisor.graph import build_supervisor_graph
from investment_scout.supervisor.nodes import Nodes
from investment_scout.supervisor.observability import DecisionLog, configure_tracing, new_trace_id
from investment_scout.supervisor.policy import AGENT_LABEL
from investment_scout.supervisor.state import initial_state

STATE_PATH = Path("out/supervisor/state.json")
CHECKPOINT_PATH = Path("out/supervisor/checkpoints.sqlite")


def describe(node: str, update: dict) -> list[str]:
    if node == "scout":
        counts = update["scout_summary"].get("counts", {})
        regions = ", ".join(f"{k} {v.get('secured')}" for k, v in counts.items())
        return [f"🔍 스타트업 탐색: 평가 대상 {len(update['candidates'])}개사 ({regions})"]
    if node == "supervisor":
        decision = update["last_decision"]
        mark = "🔁" if "재작업" in decision["reason"] or "품질 미달" in decision["reason"] else "🧭"
        return [f"{mark} Supervisor → {decision['next']}: {decision['reason']}"]
    if node in ("tech", "market", "competitor"):
        field = {"tech": "tech_summary", "market": "market_analysis", "competitor": "competitor_analysis"}[node]
        result = update.get(field) or {}
        extra = ""
        if node == "tech":
            extra = f", 분류 {update.get('tech_category', '-')}"
        if node == "competitor":
            extra = f", 경쟁사 {', '.join((result.get('data') or {}).get('main_competitors', [])) or '-'}"
        return [f"   └ {AGENT_LABEL[node]}: {update['node_status'][node]}, 주장 {len(result.get('claims', []))}건{extra}"]
    if node == "judge":
        total = (update.get("evaluation_scores") or {}).get("total")
        return [f"   └ 투자 판단: {'기준 통과' if update['investment_decision'] == 'RECOMMENDED' else '기준 미달'}"
                f" (총점 {total if total is not None else '-'})"]
    if node == "record":
        record = update["evaluation_history"][-1]
        return [f"   └ 평가 저장: {record['startup']} {record['investment_decision']}"
                + (f" — {record['hold_reason']}" if record["missing_core_information"] else "")]
    if node == "rank":
        top = update["final_ranking"][:5]
        lines = ["🏆 순위: " + ", ".join(f"{r['rank']}.{r['startup']}({r['total']:.0f}{'✔' if r['qualified'] else ''})" for r in top)]
        lines.append(f"   → 최종 추천: {update['recommended_startup'] or '전원 보류'}")
        return lines
    if node == "report":
        return [f"📝 보고서 생성 {update['report_attempts']}회차: {update['report_path']}"]
    if node == "quality":
        verdict = update["quality"]
        lines = [f"✅ 품질 평가 {verdict['attempt']}회차: {'통과' if verdict['passed'] else '미달'}"]
        for item in verdict["items"].values():
            failed = [name for name, check in item["checks"].items() if not check["passed"]]
            lines.append(f"   - {item['label']}: {'통과' if item['passed'] else '미달 ' + ', '.join(failed)}")
        return lines
    return [f"   {node}"]


def run_supervisor(*, tech_index, market_index=None, web_search=None, max_candidates: int = 20,
                   report_path: Path = Path("output/pdf/investment_report.pdf"), agents=None, scout=None,
                   quality_judge=None, echo=print, state_path: Path = STATE_PATH,
                   log_dir: Path | None = None, max_steps: int | None = None,
                   checkpoint_path: Path = CHECKPOINT_PATH, resume: str | None = None) -> dict:
    """Supervisor 그래프 실행. resume에 trace_id를 주면 SQLite 체크포인트의 마지막 단계부터 이어서 실행."""
    from investment_scout.agents.startup_scout import StartupScout
    from investment_scout.search import get_search_provider
    from investment_scout.supervisor.nodes import AgentSet

    tracing = configure_tracing()
    trace_id = resume or new_trace_id()
    log = DecisionLog(trace_id, log_dir) if log_dir else DecisionLog(trace_id)
    nodes = Nodes(agents=agents or AgentSet(tech_index=tech_index, market_index=market_index, web_search=web_search),
                  scout=scout or StartupScout(search_provider=get_search_provider("mock")), log=log,
                  report_path=Path(report_path), max_candidates=max_candidates,
                  **({"quality_judge": quality_judge} if quality_judge else {}))
    if hasattr(nodes.agents, "warmup"):
        echo("임베딩 모델 준비 중 (메인 스레드 사전 로드)…", flush=True)
        nodes.agents.warmup()
    # 재개/복구: 단계마다 State를 SQLite 파일에 저장 (서버·계정 불필요). thread_id = trace_id
    checkpoint_path = Path(checkpoint_path)
    checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(str(checkpoint_path), check_same_thread=False)
    try:
        app = build_supervisor_graph(nodes, checkpointer=SqliteSaver(connection))
        config = {"configurable": {"thread_id": trace_id}, "run_name": "investment-supervisor",
                  "metadata": {"trace_id": trace_id, "resumed": bool(resume)}, "tags": ["supervisor"]}
        if resume:
            snapshot = app.get_state(config)
            if not snapshot.values:
                raise ValueError(f"재개할 체크포인트가 없습니다: trace_id={trace_id} ({checkpoint_path})")
            limit = snapshot.values.get("max_steps", 400)
            done = snapshot.values.get("evaluation_history") or []
            echo(f"trace_id={trace_id} 재개 · 완료 {len(done)}개사, 다음 노드 {list(snapshot.next) or '없음(이미 종료)'}")
            graph_input = None  # None: 마지막 체크포인트부터 이어서 실행
        else:
            graph_input = initial_state("Semiconductor", trace_id=trace_id, max_candidates=max_candidates)
            if max_steps is not None:
                graph_input["max_steps"] = max_steps
            limit = graph_input["max_steps"]
        config["recursion_limit"] = limit * 2 + 20
        echo(f"trace_id={trace_id} · LangSmith 추적 {'ON' if tracing else 'OFF (LANGSMITH_API_KEY 없음)'} · "
             f"결정 로그 {log.path} · 체크포인트 {checkpoint_path}")
        echo(f"(중단되면 `uv run python app.py --resume {trace_id}`로 이어서 실행할 수 있습니다)")
        for step in app.stream(graph_input, config=config, stream_mode="updates"):
            for node, update in step.items():
                for line in describe(node, update or {}):
                    echo(line, flush=True)
        final = app.get_state(config).values
    finally:
        connection.close()
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps(final, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    echo(f"종료: {final.get('termination_reason')} · 단계 {final.get('step_count')} · 보고서 {final.get('report_path')}")
    return final
