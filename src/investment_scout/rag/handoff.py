"""역할 3 전달 파일: 분석 JSON과 인용 원본 PDF를 상대 경로로 묶음."""

import json
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

from investment_scout.rag.storage import load_corpus, read_json


def package_handoff(analysis: Path, documents: Path, manifest: Path, output: Path) -> dict:
    report = read_json(analysis)
    if not report.get("complete"):
        raise ValueError("전체 기업 분석이 완료된 JSON만 전달할 수 있습니다.")
    corpus = load_corpus(documents / "corpus.json")
    paths = {}
    for document in corpus.documents:
        source = Path(document.pdf_path)
        if not source.is_file():
            raise FileNotFoundError(f"근거 PDF가 없습니다: {source}")
        paths[str(source)] = f"documents/{document.document_id}.pdf"

    def portable(value):
        if isinstance(value, list):
            return [portable(item) for item in value]
        if isinstance(value, dict):
            return {key: paths[item] if key == "pdf_path" and item else portable(item)
                    for key, item in value.items()}
        return value

    # 경로 확인: 분석에 등장한 PDF가 빠지면 조용히 깨진 인용을 만들지 않음.
    for row in report["results"]:
        for claim in row["tech_summary"]["claims"]:
            for citation in claim["citations"]:
                if citation["pdf_path"] not in paths:
                    raise ValueError(f"등록되지 않은 인용 PDF: {citation['pdf_path']}")

    output.parent.mkdir(parents=True, exist_ok=True)
    with ZipFile(output, "w", ZIP_DEFLATED) as archive:
        for source, target in paths.items():
            archive.write(source, target)
        for name, value in (
            ("analyze.json", portable(report)),
            ("corpus.json", portable(read_json(documents / "corpus.json"))),
            ("coverage.json", read_json(documents / "coverage.json")),
            ("collection_log.json", read_json(documents / "collection_log.json")),
            ("tech_sources.json", read_json(manifest)),
        ):
            archive.writestr(name, json.dumps(value, ensure_ascii=False, indent=2))
        archive.writestr("README.txt", "역할 2 기술 RAG 인계\n"
                         "analyze.json: 20개 기업의 tech_summary, tech_category, source_evidence\n"
                         "documents/: 인용 페이지 확인용 PDF. pdf_path는 압축 해제 위치 기준 상대 경로.\n"
                         "coverage.json / collection_log.json: 수집 범위와 실패 사유.\n"
                         "특허처럼 텍스트가 없는 PDF는 페이지 한도에 포함되고 검색에는 사용되지 않습니다.\n")
    return {"companies": len(report["results"]), "documents": len(paths), "pages": corpus.total_pages}
