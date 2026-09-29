"""실행 진입점.

    investment-scout demo      # mock 노드로 그래프 전체 실행 (API 키 불필요)
    investment-scout scout     # 실제 시드 후보에 대한 적격성 심사 결과만 출력
    investment-scout mermaid   # 한글 업무 흐름 Mermaid 다이어그램 생성
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from dotenv import load_dotenv

from investment_scout.agents.startup_scout import StartupScout
from investment_scout.graph import (
    BUSINESS_FLOW_MERMAID,
    build_graph,
    build_mock_graph,
    create_initial_state,
    recommended_recursion_limit,
)
from investment_scout.mocks.fixtures import mock_seed_profiles
from investment_scout.nodes.production import make_production_nodes
from investment_scout.retrieval import HybridEmbeddingRetriever
from investment_scout.search import get_search_provider


def cmd_demo(args: argparse.Namespace) -> int:
    """mock 노드로 그래프를 끝까지 실행합니다. 결과는 실제 평가가 아닙니다."""
    profiles = mock_seed_profiles(args.candidates)
    names = [p["name"] for p in profiles]

    decisions = {}
    if args.recommend_index is not None and 0 <= args.recommend_index < len(names):
        decisions[names[args.recommend_index]] = "RECOMMENDED"

    app = build_mock_graph(seed_profiles=profiles, decisions=decisions)
    state = create_initial_state(args.domain, max_candidates=args.max_candidates)

    final = app.invoke(
        state,
        config={"recursion_limit": recommended_recursion_limit(args.max_candidates)},
    )

    print(final["final_report"])
    print()
    print("=" * 70)
    print(f"termination_reason : {final['termination_reason']}")
    print(f"evaluated_startups : {final['evaluated_startups']}")
    print(f"candidate_index    : {final['candidate_index']}")
    print(f"evaluation_history : {len(final['evaluation_history'])} 건")
    return 0


def cmd_scout(args: argparse.Namespace) -> int:
    """실제 시드 후보 목록에 대해 탐색/적격성 심사만 실행합니다."""
    provider = get_search_provider(args.search_mode)
    scout = StartupScout(search_provider=provider)
    result = scout.run(args.domain)

    summary = result["scout_result"]
    print(json.dumps(summary["counts"], ensure_ascii=False, indent=2))
    print()
    print(f"search_mode            : {summary['search_mode']}")
    print(f"verification_performed : {summary['verification_performed']}")
    print(f"totals                 : {summary['totals']}")
    print(f"평가 목록(ELIGIBLE)    : {result['candidate_startups']}")
    print()
    print("NEEDS_VERIFICATION 사유 (상위 3건):")
    for item in summary["needs_verification"][:3]:
        print(f"  - {item['name']}: 미확인 항목 {item['unverified_fields']}")

    if args.out:
        out_path = Path(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n전체 결과 저장: {args.out}")
    return 0


def cmd_mermaid(args: argparse.Namespace) -> int:
    """발표용 한글 업무 흐름 Mermaid를 생성합니다."""
    mermaid = BUSINESS_FLOW_MERMAID
    if args.out:
        out_path = Path(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(mermaid, encoding="utf-8")
        print(f"저장: {args.out}")
    else:
        print(mermaid)
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    """검증 후보와 실제 출처 기반 노드로 그래프를 실행합니다."""
    provider = get_search_provider(args.search_mode)
    scout = StartupScout(search_provider=provider)
    retriever = None if args.no_embeddings else HybridEmbeddingRetriever()
    app = build_graph(scout=scout, **make_production_nodes(retriever=retriever))
    state = create_initial_state(args.domain, max_candidates=args.max_candidates)
    final = app.invoke(
        state,
        config={"recursion_limit": recommended_recursion_limit(args.max_candidates)},
    )
    if args.out:
        out_path = Path(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(final, ensure_ascii=False, indent=2), encoding="utf-8")
    print(final["final_report"])
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="investment-scout")
    sub = parser.add_subparsers(dest="command", required=True)

    demo = sub.add_parser("demo", help="mock 노드로 그래프 실행 (API 키 불필요)")
    demo.add_argument("--domain", default="Semiconductor")
    demo.add_argument("--candidates", type=int, default=3, help="생성할 가짜 후보 수")
    demo.add_argument("--max-candidates", type=int, default=20)
    demo.add_argument(
        "--recommend-index",
        type=int,
        default=None,
        help="이 순번의 가짜 기업만 RECOMMENDED 로 설정 (미지정 시 전원 HOLD)",
    )
    demo.set_defaults(func=cmd_demo)

    scout = sub.add_parser("scout", help="실제 시드 후보 적격성 심사")
    scout.add_argument("--domain", default="Semiconductor")
    scout.add_argument("--search-mode", default=None, choices=["mock", "live"])
    scout.add_argument("--out", default=None, help="전체 결과를 저장할 JSON 경로")
    scout.set_defaults(func=cmd_scout)

    mermaid = sub.add_parser("mermaid", help="한글 업무 흐름 Mermaid 생성")
    mermaid.add_argument("--out", default=None)
    mermaid.set_defaults(func=cmd_mermaid)

    run = sub.add_parser("run", help="검증 데이터와 실제 근거 기반 노드로 그래프 실행")
    run.add_argument("--domain", default="Semiconductor")
    run.add_argument("--max-candidates", type=int, default=20)
    run.add_argument("--search-mode", default="mock", choices=["mock", "live"])
    run.add_argument("--out", default="out/investment_report.json")
    run.add_argument(
        "--no-embeddings",
        action="store_true",
        help="임베딩 없이 출처 topic만 사용(진단/CI용; 실제 실행은 hybrid 기본)",
    )
    run.set_defaults(func=cmd_run)

    return parser


def main(argv: list[str] | None = None) -> int:
    # .env 의 SEARCH_MODE / TAVILY_API_KEY 를 읽어옵니다 (키는 저장소에 넣지 않습니다).
    load_dotenv(override=True)

    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except Exception as exc:
        print(f"\n{type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
