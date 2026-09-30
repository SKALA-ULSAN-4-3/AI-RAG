"""페이지·헤딩별 청킹: 기업과 출처 위치를 모든 청크에 보존."""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urlsplit

from investment_scout.rag.documents import Document, DocumentType


@dataclass(frozen=True)
class Chunk:
    """검색 단위: URL·페이지·헤딩을 답변 인용까지 전달."""

    chunk_id: str
    document_id: str
    company: str
    url: str
    title: str
    document_type: DocumentType
    published_at: str | None
    page: int
    heading: str
    text: str
    pdf_path: str | None = None
    page_basis: str = "original_pdf"
    sha256: str | None = None
    publisher: str = ""
    accessed_at: str | None = None


def publisher_of(document: Document) -> str:
    """발행 주체: 기업 자체 자료는 기업명, 논문·보도자료는 게재 사이트 도메인."""
    if document.document_type in {"official_website", "product", "patent"}:
        return document.company
    host = urlsplit(document.url).hostname or ""
    return host.removeprefix("www.")


def clean_text(text: str) -> str:
    """공백 정리: 줄바꿈 보존, 기술 기호·숫자·단위는 원문 유지."""
    text = text.replace("\r\n", "\n").replace("\r", "\n").replace("\x00", "")
    lines = [re.sub(r"[^\S\n]+", " ", line).strip() for line in text.split("\n")]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


def chunk_document(
    document: Document, *, max_chars: int = 1200, overlap: int = 150
) -> list[Chunk]:
    """문자 기준 분할: 페이지·헤딩 경계를 넘지 않으며 긴 구역만 중첩."""
    if max_chars <= 0 or not 0 <= overlap < max_chars:
        raise ValueError("max_chars > 0, 0 <= overlap < max_chars 조건이 필요합니다.")
    chunks: list[Chunk] = []
    for page in document.pages:
        for section_index, section in enumerate(page.sections, start=1):
            text = clean_text(section.text)
            start = 0
            part = 0
            while start < len(text):
                end = min(start + max_chars, len(text))
                # 단어 경계: 지나치게 짧은 청크를 피하면서 공백 위치 우선 사용.
                if end < len(text):
                    boundary = max(text.rfind(" ", start, end), text.rfind("\n", start, end))
                    if boundary > start + max(max_chars // 2, overlap):
                        end = boundary
                part += 1
                chunks.append(Chunk(
                    chunk_id=f"{document.document_id}:p{page.number}:s{section_index}:c{part}",
                    document_id=document.document_id,
                    company=document.company,
                    url=document.url,
                    title=document.title,
                    document_type=document.document_type,
                    published_at=document.published_at,
                    page=page.number,
                    heading=section.heading,
                    text=text[start:end].strip(),
                    pdf_path=document.pdf_path,
                    page_basis=document.page_basis,
                    sha256=document.sha256,
                    publisher=publisher_of(document),
                    accessed_at=document.accessed_at,
                ))
                if end == len(text):
                    break
                start = end - overlap
    return chunks
