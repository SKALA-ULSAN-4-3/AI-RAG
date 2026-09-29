"""PDF 추출: 실제 페이지·목차 헤딩 보존, 스캔 페이지는 별도 진단."""

from collections import Counter
from datetime import datetime, timezone
import hashlib
from pathlib import Path
import re

from pypdf import PdfReader

from investment_scout.rag.chunking import clean_text
from investment_scout.rag.documents import Document, Page, Section


def _outline_headings(reader: PdfReader) -> dict[int, list[str]]:
    headings: dict[int, list[str]] = {}

    def visit(items):
        for item in items:
            if isinstance(item, list):
                visit(item)
            else:
                number = reader.get_destination_page_number(item)
                if number is not None and item.title:
                    headings.setdefault(number + 1, []).append(clean_text(item.title))

    visit(reader.outline)
    return headings


def _sections(lines: list[str], headings: list[str]) -> tuple[Section, ...]:
    sections: list[Section] = []
    heading = ""
    body: list[str] = []
    for line in lines:
        # 확정 헤딩: PDF 목차와 본문 줄이 일치할 때만 구역을 나눔.
        if line in headings:
            if body:
                sections.append(Section("\n".join(body), heading))
            heading, body = line, []
        else:
            body.append(line)
    if body:
        sections.append(Section("\n".join(body), heading))
    return tuple(sections)


def parse_pdf(path: Path, *, metadata: dict) -> tuple[Document, list[dict]]:
    reader = PdfReader(path)
    if reader.is_encrypted:
        raise ValueError("암호화된 PDF는 먼저 잠금을 해제해야 합니다.")
    # 논문 2단 편집: layout은 양쪽 열을 한 줄로 합칠 수 있어 원문 읽기 순서를 사용.
    mode = "plain" if metadata["document_type"] == "paper" else "layout"
    texts = [clean_text(page.extract_text(extraction_mode=mode) or "")
             if page.get_contents() is not None else "" for page in reader.pages]
    headings = _outline_headings(reader)
    # 반복 여백: 3페이지 이상 문서의 모든 페이지에서 반복되는 첫/끝 줄만 제거.
    edges = Counter()
    for text in texts:
        lines = text.splitlines()
        edges.update(set(lines[:1] + lines[-1:]))
    repeated = {line for line, count in edges.items() if count == len(texts) and count >= 3}
    pages, diagnostics = [], []
    for number, text in enumerate(texts, start=1):
        lines = text.splitlines()
        cleaned = [line for i, line in enumerate(lines)
                   if not (i in {0, len(lines) - 1} and
                           (line in repeated or re.fullmatch(r"(?:Page\s+)?\d+", line, re.I)))]
        if not text.strip():
            diagnostics.append({"page": number, "kind": "NO_EXTRACTABLE_TEXT",
                                "message": "빈 페이지 또는 스캔 이미지: OCR/원문 확인 필요"})
        pages.append(Page(number, _sections(cleaned, headings.get(number, []))))
    document = Document(
        **metadata, pages=tuple(pages), pdf_path=str(path.resolve()),
        sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
        accessed_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
    )
    return document, diagnostics
