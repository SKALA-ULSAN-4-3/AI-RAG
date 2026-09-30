"""문서 처리 검증: 전체 한도, 원문 위치, 기술 표기 보존."""

import pytest

from investment_scout.rag.chunking import chunk_document, clean_text
from investment_scout.rag.documents import Document, DocumentCorpus, Page, PageLimitError, Section


def document(document_id="doc_a", company="Company A", page_count=1):
    return Document(
        document_id=document_id,
        company=company,
        url="https://example.com/product.pdf",
        title="Test product document",
        document_type="product",
        pages=tuple(Page(number, (Section("NPU 10 TOPS/W", "Specifications"),))
                    for number in range(1, page_count + 1)),
    )


def test_page_limit_is_shared_across_companies_and_rejection_is_atomic():
    corpus = DocumentCorpus()
    corpus.add(document(page_count=120))
    corpus.add(document("doc_b", "Company B", 80))
    with pytest.raises(PageLimitError):
        corpus.add(document("doc_c", "Company C"))
    assert corpus.total_pages == 200
    assert len(corpus.documents) == 2


def test_duplicate_document_does_not_change_page_count():
    corpus = DocumentCorpus()
    corpus.add(document())
    with pytest.raises(ValueError, match="이미 등록"):
        corpus.add(document())
    assert corpus.total_pages == 1


def test_missing_or_non_contiguous_pages_are_rejected():
    for pages in ((), (Page(2, ()),), (Page(1, ()), Page(3, ()))):
        with pytest.raises(ValueError, match="전체 페이지"):
            Document("bad", "Company A", "https://example.com", "Title", "paper", pages)


def test_chunks_preserve_company_source_page_and_heading():
    chunks = chunk_document(document(page_count=2), max_chars=8, overlap=2)
    assert {chunk.page for chunk in chunks} == {1, 2}
    assert len({chunk.chunk_id for chunk in chunks}) == len(chunks)
    assert all(chunk.company == "Company A" for chunk in chunks)
    assert all(chunk.url == "https://example.com/product.pdf" for chunk in chunks)
    assert all(chunk.heading == "Specifications" for chunk in chunks)
    assert all(0 < len(chunk.text) <= 8 for chunk in chunks)
    assert all(chunk.published_at is None for chunk in chunks)


def test_blank_page_counts_towards_limit_without_generating_evidence():
    blank = Document("blank", "Company A", "https://example.com", "Blank", "paper",
                     (Page(1, (Section("   "),)),))
    corpus = DocumentCorpus()
    corpus.add(blank)
    assert corpus.total_pages == 1
    assert chunk_document(blank) == []


def test_chunking_does_not_mix_headings():
    source = Document("sections", "Company A", "https://example.com", "Title", "paper",
                      (Page(1, (Section("First body", "First"), Section("Second body", "Second"))),))
    chunks = chunk_document(source)
    assert [(chunk.heading, chunk.text) for chunk in chunks] == [
        ("First", "First body"), ("Second", "Second body")]


def test_cleanup_preserves_technical_symbols_and_paragraphs():
    assert clean_text("  NPU\t10 TOPS/W\r\n\r\nHBM3 ±5%  ") == "NPU 10 TOPS/W\n\nHBM3 ±5%"


@pytest.mark.parametrize("max_chars,overlap", [(0, 0), (10, 10), (10, -1)])
def test_invalid_chunk_settings_are_rejected(max_chars, overlap):
    with pytest.raises(ValueError):
        chunk_document(document(), max_chars=max_chars, overlap=overlap)


def test_chunks_carry_publisher_and_accessed_at():
    from dataclasses import replace

    own = replace(document(), accessed_at="2026-09-30T00:00:00+00:00")
    paper = replace(document(), document_type="paper", url="https://www.arxiv.org/pdf/1.pdf")
    assert chunk_document(own)[0].publisher == "Company A"
    assert chunk_document(own)[0].accessed_at == "2026-09-30T00:00:00+00:00"
    assert chunk_document(paper)[0].publisher == "arxiv.org"
