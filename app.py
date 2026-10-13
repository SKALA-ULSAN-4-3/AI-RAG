"""AI 스타트업 투자 평가 — Supervisor 패턴 멀티 에이전트 실행 스크립트 (명령어 하나로 보고서까지).

    uv run python app.py                     # 20개 기업 전체 평가 → 품질 평가 → PDF 보고서
    uv run python app.py --max-candidates 3  # 앞의 3개 기업만 (빠른 확인)
    uv run python app.py --report-only       # 저장된 결과로 PDF만 다시 생성 (API 호출 없음)
    uv run python app.py --resume <trace_id> # 중단된 실행을 마지막 단계부터 이어서 실행

처음 실행이면 기술·시장 자료 수집과 인덱스 생성을 먼저 수행합니다.
LANGSMITH_API_KEY가 .env에 있으면 LangSmith 추적이 켜집니다.
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import sys

from dotenv import load_dotenv

TECH_INDEX = Path("out/tech_rag/index")
MARKET_INDEX = Path("out/market_rag/index")
STATE = Path("out/supervisor/state.json")
REPORT = Path("output/pdf/investment_report.pdf")


def step(title: str) -> None:
    print(f"\n{'=' * 70}\n▶ {title}\n{'=' * 70}", flush=True)


def ensure_indexes(rag) -> None:
    """인덱스가 없으면 자료 수집(합산 200페이지 검사)과 인덱싱을 수행."""
    jobs = [
        (TECH_INDEX, ["collect"], ["index"]),
        (MARKET_INDEX,
         ["collect", "--manifest", "data/market_sources.json", "--directory", "out/market_rag/documents"],
         ["index", "--corpus", "out/market_rag/documents/corpus.json", "--index", str(MARKET_INDEX)]),
    ]
    for index, collect, build in jobs:
        if (index / "metadata.json").exists():
            print(f"✔ 인덱스 재사용: {index}")
            continue
        step(f"자료 수집·인덱스 생성: {index}")
        # collect는 접근이 막힌 일부 URL 때문에 1을 반환할 수 있음: 성공한 자료로 계속 진행.
        rag.main(collect)
        if rag.main(build) != 0:
            raise SystemExit(f"인덱스 생성 실패: {index}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--max-candidates", type=int, default=20, help="평가할 후보 수 (기본 20)")
    parser.add_argument("--report-only", action="store_true", help="저장된 결과로 PDF만 다시 생성")
    parser.add_argument("--report", type=Path, default=REPORT, help="보고서 PDF 경로")
    parser.add_argument("--resume", metavar="TRACE_ID", default=None,
                        help="중단된 실행의 trace_id: SQLite 체크포인트에서 이어서 실행")
    args = parser.parse_args(argv)
    load_dotenv(Path.cwd() / ".env")

    try:
        from investment_scout.rag import cli as rag
    except ImportError as exc:
        print(f"의존성이 없습니다 ({exc}). 먼저 설치하세요:\n"
              "  uv sync --extra tech-rag --extra embeddings --extra live-search --extra report --group dev")
        return 1

    if args.report_only:
        step("보고서 재생성 (API 호출 없음)")
        if not STATE.exists():
            print(f"저장된 결과가 없습니다: {STATE}. 먼저 `uv run python app.py`로 평가를 실행하세요.")
            return 1
        import json
        from investment_scout.reporting.content import build_report_content
        from investment_scout.reporting.pdf_report import render_pdf_report
        from investment_scout.supervisor.nodes import process_summary, report_state
        saved = json.loads(STATE.read_text(encoding="utf-8"))
        content = build_report_content(report_state(saved), strict=saved.get("report_mode") == "strict")
        result = render_pdf_report(content, args.report, process=process_summary(saved),
                                   quality=saved.get("quality") or None)
        print(f"보고서 저장: {result['path']} ({result['pages']}페이지)")
        return 0

    if not os.getenv("OPENAI_API_KEY", "").strip():
        print(".env에 OPENAI_API_KEY를 입력하세요. (.env.example 참고)")
        return 1
    if not os.getenv("TAVILY_API_KEY", "").strip():
        print("⚠️ TAVILY_API_KEY가 없어 경쟁사 비교는 자리 표시 노드로 실행됩니다.")

    step("준비: 기술·시장 인덱스 확인")
    ensure_indexes(rag)
    step(f"Supervisor 실행: 후보 {args.max_candidates}개 평가 → 순위 → 보고서 → 품질 평가")
    from investment_scout.search import SearchConfigurationError, get_search_provider
    from investment_scout.supervisor import run_supervisor
    try:
        web = get_search_provider("live")
    except SearchConfigurationError as exc:
        print(f"⚠️ 경쟁사 웹 검색 비활성: {exc}")
        web = None
    final = run_supervisor(tech_index=rag._load_index(TECH_INDEX), market_index=rag._load_index(MARKET_INDEX),
                           web_search=web, max_candidates=args.max_candidates, report_path=args.report,
                           state_path=STATE, resume=args.resume)
    step("완료")
    print(f"보고서: {args.report.resolve()}\n최종 State: {STATE.resolve()}")
    return 0 if final.get("report_path") else 1


if __name__ == "__main__":
    sys.exit(main())
