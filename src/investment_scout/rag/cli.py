"""기술 담당 실행 도구: python -m investment_scout.rag.cli --help."""

import argparse
from dataclasses import asdict
import importlib.util
import os
from pathlib import Path

from dotenv import load_dotenv

from investment_scout.rag.storage import load_corpus, read_json, write_json

DEFAULT_QUESTIONS = (
    "핵심 반도체 제품과 기술 구조는 무엇인가?",
    "공식 문서에서 확인되는 성능과 전력 효율은 무엇인가?",
    "기술의 차별점과 장점은 무엇인가?",
    "기술의 제약 조건과 한계는 무엇인가?",
    "샘플 공급, 양산 또는 고객 도입 등 상용화 상태는 무엇인가?",
    "NPU, AI Accelerator, HBM, DRAM, GPU, EDA/공정 AI 중 어떤 제품 기술인가?",
)


def doctor() -> int:
    """환경 점검: 키 내용은 출력하지 않고 설정 유무만 확인."""
    dependencies = ("pypdf", "playwright", "faiss", "numpy", "openai", "sentence_transformers")
    missing = []
    for package in dependencies:
        ready = importlib.util.find_spec(package) is not None
        print(f"{package}: {'OK' if ready else 'NOT_INSTALLED'}")
        if not ready:
            missing.append(package)
    for name in ("OPENAI_API_KEY", "OPENAI_MODEL"):
        configured = bool(os.getenv(name, "").strip())
        print(f"{name}: {'SET' if configured else 'NOT_SET'}")
        if not configured:
            missing.append(name)
    print("PDF/검색만 실행할 때는 OpenAI 설정이 필요하지 않습니다.")
    return 1 if missing else 0


def _load_index(path):
    from investment_scout.rag.index import DualFaissIndex
    return DualFaissIndex.load(path)


def _run(args) -> int:
    if args.command == "doctor":
        return doctor()
    if args.command == "seed":
        from investment_scout.rag.collection import seed_manifest
        result = seed_manifest(args.candidates, args.manifest)
        print(f"후보 {len(result['companies'])}개: {args.manifest}")
        return 0
    if args.command == "collect":
        from investment_scout.rag.collection import collect_manifest
        result = collect_manifest(args.manifest, args.directory, local_only=args.local_only)
        print(f"전체 자료: {result['total_pages']}/200페이지")
        return int(any(row["status"] in {"ERROR", "NO_TEXT"} for row in result["records"]))
    if args.command == "package":
        from investment_scout.rag.handoff import package_handoff

        result = package_handoff(args.analysis, args.directory, args.manifest, args.out)
        print(f"인계 파일: {result['companies']}개 기업, {result['documents']}개 PDF, "
              f"{result['pages']}페이지 → {args.out}")
        return 0
    if args.command == "index":
        from investment_scout.rag.index import DualFaissIndex
        corpus = load_corpus(args.corpus)
        print(f"{corpus.total_pages}페이지 임베딩 시작: 첫 실행은 모델 다운로드가 필요합니다.", flush=True)
        index = DualFaissIndex.build(corpus)
        index.save(args.index)
        print(f"{len(index.chunks)}개 청크, KURE/Jina 인덱스 저장: {args.index}")
        return 0
    index = _load_index(args.index)
    if args.command == "search":
        hits = index.search(args.question, company=args.company, min_score=args.min_score)
        write_json(args.out, {"company": args.company, "question": args.question,
                             "status": "OK" if hits else "INSUFFICIENT_DATA",
                             "message": "검색 결과는 답변의 사실성을 보증하지 않습니다." if hits else "근거 부족",
                             "hits": [asdict(hit) for hit in hits]})
    elif args.command == "verify":
        results = []
        if any(chunk.company == args.company for chunk in index.chunks):
            index.prepare_queries(list(DEFAULT_QUESTIONS))
        for question in DEFAULT_QUESTIONS:
            hits = index.search(question, company=args.company, min_score=args.min_score)
            results.append({"question": question, "status": "REVIEW_REQUIRED" if hits else "INSUFFICIENT_DATA",
                            "hits": [asdict(hit) for hit in hits]})
        write_json(args.out, {"company": args.company, "semantic_review": "PENDING",
                             "questions": results})
        print("검증 질문 6개: 각 URL·페이지·본문이 질문의 답을 뒷받침하는지 확인하세요.")
    elif args.command == "ask":
        from investment_scout.rag.generation import OpenAIGenerator, answer_question
        result = answer_question(index, OpenAIGenerator(), company=args.company,
                                 question=args.question, min_score=args.min_score)
        write_json(args.out, result)
        print(result["data"].get("answer", "근거 부족"))
    elif args.command == "analyze":
        from investment_scout.agents.tech_analyst import TechAnalyst
        from investment_scout.agents.tech_classifier import TechClassifier
        names = read_json(args.manifest)["companies"]
        if args.company:
            if args.company not in names:
                raise ValueError("후보 목록에 없는 기업명입니다.")
            names = [args.company]
        analyst = TechAnalyst(index, min_score=args.min_score)
        classifier = TechClassifier(index, min_score=args.min_score)
        covered = {chunk.company for chunk in index.chunks}
        index.prepare_queries([
            question for name in names if name in covered
            for question in (analyst.question(name), classifier.question(name))
        ])
        rows = []
        for name in names:
            state = {"current_startup": {"name": name}}
            state.update(analyst(state))
            state.update(classifier(state))
            rows.append({**state, "source_evidence": {name: state["tech_summary"]["evidence"]}})
            # 중간 저장: API 장애 전까지 완료한 기업의 결과 보존.
            write_json(args.out, {"role": 2, "results": rows, "complete": len(rows) == len(names)})
            print(f"{name}: {state['tech_summary']['status']}", flush=True)
    print(f"저장: {args.out}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("doctor", help="의존성·OpenAI 설정 점검 (키 값 출력 안 함)")
    seed = commands.add_parser("seed", help="기존 후보 20개의 수집 목록 생성")
    seed.add_argument("--candidates", type=Path, default=Path("data/candidates_verified.json"))
    seed.add_argument("--manifest", type=Path, default=Path("data/tech_sources.json"))
    collect = commands.add_parser("collect", help="URL 또는 로컬 PDF 수집·파싱")
    collect.add_argument("--manifest", type=Path, default=Path("data/tech_sources.json"))
    collect.add_argument("--directory", type=Path, default=Path("out/tech_rag/documents"))
    collect.add_argument("--local-only", action="store_true")
    package = commands.add_parser("package", help="기술 결과·원본 PDF를 역할 3 전달용 ZIP으로 묶기")
    package.add_argument("--analysis", type=Path, default=Path("out/tech_rag/analyze.json"))
    package.add_argument("--directory", type=Path, default=Path("out/tech_rag/documents"))
    package.add_argument("--manifest", type=Path, default=Path("data/tech_sources.json"))
    package.add_argument("--out", type=Path, default=Path("out/tech_rag/tech_handoff.zip"))
    for name in ("index", "search", "ask", "analyze", "verify"):
        command = commands.add_parser(name)
        command.add_argument("--index", type=Path, default=Path("out/tech_rag/index"))
        if name == "index":
            command.add_argument("--corpus", type=Path, default=Path("out/tech_rag/documents/corpus.json"))
            continue
        command.add_argument("--company", required=name != "analyze")
        command.add_argument("--min-score", type=float, default=float(os.getenv("RAG_MIN_SCORE", "0.35")))
        command.add_argument("--out", type=Path, default=Path(f"out/tech_rag/{name}.json"))
        if name in {"search", "ask"}:
            command.add_argument("--question", required=True)
        if name == "analyze":
            command.add_argument("--manifest", type=Path, default=Path("data/tech_sources.json"))
    return parser


def main(argv=None) -> int:
    # 실행 위치: 프로젝트 루트의 .env, 운영 환경변수가 있으면 기존 값 우선.
    load_dotenv(Path.cwd() / ".env")
    args = build_parser().parse_args(argv)
    try:
        return _run(args)
    except Exception as exc:
        # 실패 분리: 시스템 오류를 '근거 부족' 결과로 바꾸지 않음.
        print(f"ERROR [{type(exc).__name__}]: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
