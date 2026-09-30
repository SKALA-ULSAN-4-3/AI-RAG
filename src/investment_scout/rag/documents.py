"""문서 계약: 기업·출처·원문 페이지 관리, 전체 자료 200페이지 제한."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Literal
from urllib.parse import urlsplit

DocumentType = Literal["official_website", "product", "paper", "patent", "press_release"]
DOCUMENT_TYPES = {"official_website", "product", "paper", "patent", "press_release"}
MAX_CORPUS_PAGES = 200


@dataclass(frozen=True)
class Section:
    """본문 구역: 원문에 있는 헤딩만 기록, 헤딩이 없으면 빈 문자열."""

    text: str
    heading: str = ""


@dataclass(frozen=True)
class Page:
    """원문 페이지: 1부터 시작, 빈 페이지도 전체 페이지 수에 포함."""

    number: int
    sections: tuple[Section, ...]


@dataclass(frozen=True)
class Document:
    """문서 메타데이터: 발행일 미확인은 None, 페이지 번호는 원문 순서."""

    document_id: str
    company: str
    url: str
    title: str
    document_type: DocumentType
    pages: tuple[Page, ...]
    published_at: str | None = None
    pdf_path: str | None = None
    sha256: str | None = None
    accessed_at: str | None = None
    page_basis: str = "original_pdf"

    def __post_init__(self) -> None:
        # 필수값: 식별자와 기업명 누락 시 다른 기업 자료와 혼합되는 문제 방지.
        for field in ("document_id", "company", "title"):
            if not getattr(self, field).strip():
                raise ValueError(f"{field}: 빈 문자열은 사용할 수 없습니다.")
        parsed = urlsplit(self.url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("출처 URL은 http 또는 https 주소여야 합니다.")
        if self.document_type not in DOCUMENT_TYPES:
            raise ValueError(f"지원하지 않는 문서 유형: {self.document_type}")
        if self.page_basis not in {"original_pdf", "web_pdf"}:
            raise ValueError("페이지 기준은 original_pdf 또는 web_pdf여야 합니다.")
        if self.published_at is not None:
            date.fromisoformat(self.published_at)
        # 페이지 보존: 선택한 일부 페이지만 등록하여 제한을 우회하지 않도록 검증.
        numbers = [page.number for page in self.pages]
        if not numbers or numbers != list(range(1, len(self.pages) + 1)):
            raise ValueError("문서에는 1부터 연속된 전체 페이지가 필요합니다.")


class PageLimitError(ValueError):
    """전체 한도 초과: 초과 문서를 자르거나 조용히 제외하지 않고 알림."""


class DocumentCorpus:
    """공통 자료집: 기업별이 아닌 모든 기업 합계로 페이지 제한 적용."""

    def __init__(self) -> None:
        self._documents: dict[str, Document] = {}

    @property
    def documents(self) -> tuple[Document, ...]:
        return tuple(self._documents.values())

    @property
    def total_pages(self) -> int:
        return sum(len(document.pages) for document in self._documents.values())

    def add(self, document: Document) -> None:
        # 중복 방지: 같은 식별자 재등록은 원문 덮어쓰기 대신 명시적 오류.
        if document.document_id in self._documents:
            raise ValueError(f"이미 등록된 문서: {document.document_id}")
        proposed_total = self.total_pages + len(document.pages)
        if proposed_total > MAX_CORPUS_PAGES:
            raise PageLimitError(
                f"전체 자료 {proposed_total}페이지: {MAX_CORPUS_PAGES}페이지 한도 초과"
            )
        self._documents[document.document_id] = document

    def remove(self, document_id: str) -> Document:
        """목록에서 제외된 자료 삭제: 페이지 한도 계산에서도 빠짐."""
        return self._documents.pop(document_id)
