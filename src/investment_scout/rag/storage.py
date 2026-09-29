"""JSON 저장·복원: 원문 페이지와 전체 페이지 제한을 다시 검증."""

import json
from dataclasses import asdict
from pathlib import Path

from investment_scout.rag.documents import Document, DocumentCorpus, Page, Section


def write_json(path: Path, value: object) -> None:
    """원자적 저장: 중단 시 기존 JSON이 반만 덮어써지는 문제 방지."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def save_corpus(corpus: DocumentCorpus, path: Path) -> None:
    write_json(path, {"schema_version": 1, "documents": [asdict(d) for d in corpus.documents]})


def load_corpus(path: Path) -> DocumentCorpus:
    payload = read_json(path)
    if payload.get("schema_version") != 1:
        raise ValueError("지원하지 않는 자료집 버전입니다.")
    corpus = DocumentCorpus()
    for raw in payload["documents"]:
        item = dict(raw)
        item["pages"] = tuple(
            Page(p["number"], tuple(Section(**s) for s in p["sections"]))
            for p in item["pages"]
        )
        corpus.add(Document(**item))
    return corpus
