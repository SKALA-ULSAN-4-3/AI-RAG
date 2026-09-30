"""기술 RAG 검증: 실제 FAISS/PDF 처리, 외부 모델 대신 결정적 테스트 대역."""

from dataclasses import replace
import json
from pathlib import Path
from zipfile import ZipFile

import pytest

pytest.importorskip("faiss")
pytest.importorskip("pypdf")

from pypdf import PdfReader, PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

from investment_scout.agents.tech_analyst import TechAnalyst
from investment_scout.agents.tech_classifier import TechClassifier, tech_classifier
from investment_scout.contracts import validate_analysis_result
from investment_scout.evidence import drop_unsupported_claims
from investment_scout.rag.collection import collect_manifest, seed_manifest
from investment_scout.rag.documents import Document, DocumentCorpus, Page, Section
from investment_scout.rag.generation import Citation, Fact, GroundedResponse, answer_question, grounded_result
from investment_scout.rag.index import DualFaissIndex, SearchHit
from investment_scout.rag.handoff import package_handoff
from investment_scout.rag.integration import with_technical_sources
from investment_scout.rag.parsing import parse_pdf
from investment_scout.rag.storage import load_corpus, read_json, save_corpus, write_json


TECH_TEXTS = [
    "NPU architecture supports INT8 inference.",
    "Energy efficiency is 10 TOPS/W at INT8.",
    "The compiler supports operator fusion.",
    "The device supports a maximum of 8 GB memory.",
    "Engineering samples are available; mass production is planned.",
]
QUESTIONS = [f"technical question {i}" for i in range(5)]


class FixedEmbeddings:
    """가상 벡터: 의미 품질이 아닌 기업 필터·점수·인용 연결만 검증."""

    def encode(self, texts, *, model, query=False):
        vectors = []
        for text in texts:
            position = TECH_TEXTS.index(text) if text in TECH_TEXTS else QUESTIONS.index(text)
            vector = [0.0] * (5 if model == "kure" else 7)
            vector[position] = 1.0
            vectors.append(vector)
        return vectors


@pytest.fixture
def corpus():
    corpus = DocumentCorpus()
    for name in ("Company A", "Company B"):
        corpus.add(Document(
            document_id=name[-1], company=name, url=f"https://example.com/{name[-1]}.pdf",
            title="Synthetic test evidence", document_type="product",
            pages=tuple(Page(i, (Section(text, f"Section {i}"),))
                        for i, text in enumerate(TECH_TEXTS, start=1)),
        ))
    return corpus


@pytest.fixture
def index(corpus):
    return DualFaissIndex.build(corpus, embedder=FixedEmbeddings())


@pytest.mark.parametrize("position", range(5))
def test_five_questions_find_correct_company_url_and_page(index, position):
    hits = index.search(QUESTIONS[position], company="Company A", min_score=0.5)
    assert len(hits) == 1
    assert hits[0].chunk.text == TECH_TEXTS[position]
    assert hits[0].chunk.company == "Company A"
    assert hits[0].chunk.url == "https://example.com/A.pdf"
    assert hits[0].chunk.page == position + 1


def test_index_roundtrip_and_integrity(index, tmp_path):
    directory = tmp_path / "인덱스"
    index.save(directory)
    loaded = DualFaissIndex.load(directory, embedder=FixedEmbeddings())
    assert loaded.search(QUESTIONS[0], company="Company B")[0].chunk.page == 1
    metadata = read_json(directory / "metadata.json")
    file = directory / metadata["files"]["kure"]["name"]
    file.write_bytes(b"corrupted")
    with pytest.raises(ValueError, match="무결성"):
        DualFaissIndex.load(directory)


def test_handoff_zip_uses_portable_pdf_paths(tmp_path):
    documents = tmp_path / "documents"
    documents.mkdir()
    pdf = documents / "source.pdf"
    writer = PdfWriter()
    writer.add_blank_page(width=200, height=200)
    with pdf.open("wb") as stream:
        writer.write(stream)
    corpus = DocumentCorpus()
    corpus.add(Document("source", "Company A", "https://example.com/source.pdf", "Source",
                        "product", (Page(1, (Section("NPU technical evidence"),)),),
                        pdf_path=str(pdf.resolve())))
    save_corpus(corpus, documents / "corpus.json")
    write_json(documents / "coverage.json", {})
    write_json(documents / "collection_log.json", [])
    manifest = tmp_path / "manifest.json"
    write_json(manifest, {"companies": ["Company A"]})
    analysis = tmp_path / "analysis.json"
    write_json(analysis, {"complete": True, "results": [{"tech_summary": {"claims": [
        {"citations": [{"pdf_path": str(pdf.resolve()), "url": "https://example.com/source.pdf",
                        "page": 1}]}
    ]}}]})
    output = tmp_path / "handoff.zip"
    package_handoff(analysis, documents, manifest, output)
    with ZipFile(output) as archive:
        assert "documents/source.pdf" in archive.namelist()
        assert ".env" not in archive.namelist()
        result = json.loads(archive.read("analyze.json"))
        citation = result["results"][0]["tech_summary"]["claims"][0]["citations"][0]
        assert citation["pdf_path"] == "documents/source.pdf"


def test_unknown_company_does_not_call_embedding_or_llm(index):
    class MustNotRun:
        def encode(self, *args, **kwargs):
            pytest.fail("문서가 없는 기업에 임베딩을 호출하면 안 됩니다.")

        def generate(self, **kwargs):
            pytest.fail("검색 결과가 없을 때 LLM을 호출하면 안 됩니다.")

    index.embedder = MustNotRun()
    result = answer_question(index, MustNotRun(), company="Unknown", question="NPU")
    assert result["status"] == "INSUFFICIENT_DATA"
    assert result["claims"] == []
    assert "근거 부족" in result["missing_information"][0]


def test_low_similarity_does_not_become_one_after_normalization(index):
    class Orthogonal:
        def encode(self, texts, *, model, query=False):
            return [[-1.0, 0, 0, 0, 0] + ([0, 0] if model == "jina" else [])]
    index.embedder = Orthogonal()
    assert index.search("unrelated", company="Company A", min_score=0.35) == []


def response(chunk, *, quote=None, chunk_id=None, field="answer", category=None):
    return GroundedResponse(facts=[Fact(
        field=field, text="검증용 기술 설명", category=category,
        citations=[Citation(chunk_id=chunk_id or chunk.chunk_id, quote=quote or chunk.text)],
    )], missing_information=[])


@pytest.mark.parametrize("invalid", ["missing_chunk", "fabricated_quote", "other_company"])
def test_invalid_citations_are_removed(index, invalid):
    hits = index.search(QUESTIONS[0], company="Company A")
    chunk = hits[0].chunk
    generated = response(chunk,
                         quote="Invented performance 9999 TOPS" if invalid == "fabricated_quote" else None,
                         chunk_id="missing" if invalid == "missing_chunk" else None)
    company = "Company B" if invalid == "other_company" else "Company A"
    result = grounded_result(generated, hits, company=company, fields=("answer",))
    assert result["status"] == "INSUFFICIENT_DATA"
    assert result["claims"] == []
    assert result["data"] == {}


def test_registered_citations_survive_existing_evidence_contract(index):
    hits = index.search(QUESTIONS[0], company="Company A")
    result = grounded_result(response(hits[0].chunk, field="core_technology"), hits,
                             company="Company A", fields=("core_technology",))
    update = with_technical_sources(lambda state: {"source_evidence": {}}, index)({})
    known = [s["source_id"] for rows in update["source_evidence"].values() for s in rows]
    validate_analysis_result(result, field_name="tech_summary", known_source_ids=known)
    cleaned, diagnostics = drop_unsupported_claims(result, source_evidence=update["source_evidence"],
                                                   field_name="tech_summary", startup="Company A")
    assert cleaned["claims"] == result["claims"]
    assert diagnostics == []
    assert cleaned["claims"][0]["citations"][0]["page"] == 1


def test_pdf_line_break_hyphen_is_accepted_but_other_words_are_not(index):
    original = index.search(QUESTIONS[0], company="Company A")[0].chunk
    chunk = replace(original, text="HyperAccel introduces latency pro-\ncessing unit (LPU).")
    hit = SearchHit(chunk, 0.9, {})
    quote = "HyperAccel introduces latency processing unit (LPU)."
    accepted = grounded_result(response(chunk, quote=quote), [hit],
                               company="Company A", fields=("answer",))
    rejected = grounded_result(response(chunk, quote=quote.replace("latency", "quantum")),
                               [hit], company="Company A", fields=("answer",))
    assert accepted["status"] == "OK"
    assert accepted["claims"][0]["citations"][0]["page"] == chunk.page
    assert rejected["status"] == "INSUFFICIENT_DATA"


def test_classifier_only_uses_cited_category(index):
    hits = index.search(QUESTIONS[0], company="Company A")
    result = grounded_result(response(hits[0].chunk, field="categories", category="NPU"), hits,
                             company="Company A", fields=("categories",))
    assert tech_classifier({"tech_summary": result}) == {"tech_category": "NPU"}
    result["claims"] = []
    assert tech_classifier({"tech_summary": result}) == {"tech_category": "근거 부족"}


def test_classifier_adds_separately_retrieved_citation(index):
    hits = index.search(QUESTIONS[0], company="Company A")

    class Retrieved:
        def search(self, question, **kwargs):
            return hits

    class Generated:
        def generate(self, **kwargs):
            return response(hits[0].chunk, field="categories", category="NPU")

    state = {"current_startup": {"name": "Company A"},
             "tech_summary": grounded_result(response(hits[0].chunk, field="core_technology"),
                                             hits, company="Company A", fields=("core_technology",))}
    result = TechClassifier(Retrieved(), generator=Generated())(state)
    assert result["tech_category"] == "NPU"
    assert {c["data_keys"][0] for c in result["tech_summary"]["claims"]} == {
        "core_technology", "categories"}
    assert len({c["claim_id"] for c in result["tech_summary"]["claims"]}) == 2
    assert result["tech_summary"]["claims"][-1]["citations"][0]["page"] == 1
    validate_analysis_result(
        result["tech_summary"], field_name="tech_summary",
        known_source_ids=[item["source_id"] for item in result["tech_summary"]["evidence"]],
    )


def test_api_failure_is_not_disguised_as_insufficient_data(index):
    class BrokenGenerator:
        def generate(self, **kwargs):
            raise RuntimeError("API unavailable")
    with pytest.raises(RuntimeError, match="API unavailable"):
        answer_question(index, BrokenGenerator(), company="Company A", question=QUESTIONS[0])


def test_storage_checks_page_budget_when_loading(corpus, tmp_path):
    file = tmp_path / "corpus.json"
    save_corpus(corpus, file)
    assert load_corpus(file).documents == corpus.documents
    data = read_json(file)
    page = data["documents"][0]["pages"][0]
    data["documents"][0]["pages"] = [{**page, "number": i} for i in range(1, 202)]
    write_json(file, data)
    with pytest.raises(ValueError, match="한도 초과"):
        load_corpus(file)


def make_pdf(path: Path):
    """테스트 원문: 1페이지 본문 + 2페이지 공백, 실제 기업 자료가 아님."""
    writer = PdfWriter()
    page = writer.add_blank_page(width=595, height=842)
    font = DictionaryObject({NameObject("/Type"): NameObject("/Font"),
                             NameObject("/Subtype"): NameObject("/Type1"),
                             NameObject("/BaseFont"): NameObject("/Helvetica")})
    page[NameObject("/Resources")] = DictionaryObject({NameObject("/Font"): DictionaryObject({NameObject("/F1"): font})})
    stream = DecodedStreamObject()
    stream.set_data(b"BT /F1 12 Tf 50 750 Td (Specifications) Tj 0 -20 Td (NPU supports INT8 inference.) Tj ET")
    page[NameObject("/Contents")] = stream
    writer.add_outline_item("Specifications", 0)
    writer.add_blank_page(width=595, height=842)
    writer.write(path)


def test_pdf_parser_preserves_pages_outline_and_empty_page_diagnostic(tmp_path):
    pdf = tmp_path / "sample.pdf"
    make_pdf(pdf)
    document, warnings = parse_pdf(pdf, metadata={"document_id": "test", "company": "Company A",
                                                   "url": "https://example.com/test.pdf", "title": "Test",
                                                   "document_type": "product"})
    assert len(document.pages) == 2
    assert document.pages[0].sections[0].heading == "Specifications"
    assert "INT8" in document.pages[0].sections[0].text
    assert warnings[0]["page"] == 2
    assert len(document.sha256) == 64


def test_collection_reuses_documents_without_double_counting(tmp_path):
    pdf = tmp_path / "sample.pdf"
    make_pdf(pdf)
    manifest = tmp_path / "sources.json"
    write_json(manifest, {"schema_version": 1, "companies": ["Company A"], "documents": [{
        "document_id": "test", "company": "Company A", "url": "https://example.com/test.pdf",
        "title": "Test", "document_type": "product", "pdf_path": "sample.pdf", "published_at": None,
    }]})
    directory = tmp_path / "collected"
    first = collect_manifest(manifest, directory, local_only=True)
    second = collect_manifest(manifest, directory, local_only=True)
    assert first["total_pages"] == second["total_pages"] == 2
    assert second["records"][0]["status"] == "CACHED"


def test_seed_covers_all_twenty_candidates(tmp_path):
    result = seed_manifest(Path("data/candidates_verified.json"), tmp_path / "sources.json")
    assert len(result["companies"]) == 20
    assert {d["company"] for d in result["documents"]} == set(result["companies"])


def test_query_batch_only_encodes_each_model_once(index):
    calls = []
    original = index.embedder.encode

    def counted(texts, **kwargs):
        calls.append(kwargs["model"])
        return original(texts, **kwargs)

    index.embedder.encode = counted
    index.prepare_queries(QUESTIONS)
    for question in QUESTIONS:
        index.search(question, company="Company A")
    assert calls == ["kure", "jina"]


def test_analyst_keeps_node_contract_and_missing_fields(index):
    hits = index.search(QUESTIONS[0], company="Company A")

    class Retrieved:
        def search(self, question, **kwargs):
            return hits

    class Generated:
        def generate(self, **kwargs):
            return response(hits[0].chunk, field="core_technology")

    state = {"current_startup": {"name": "Company A"}, "market_analysis": {"untouched": True}}
    result = TechAnalyst(Retrieved(), generator=Generated())(state)
    assert set(result) == {"tech_summary"}
    assert result["tech_summary"]["status"] == "INSUFFICIENT_DATA"
    assert "limitations" not in result["tech_summary"]["data"]
    assert state["market_analysis"] == {"untouched": True}


def test_classifier_does_not_inherit_an_uncited_second_category(index):
    hits = index.search(QUESTIONS[0], company="Company A")
    result = grounded_result(response(hits[0].chunk, field="categories", category="NPU"), hits,
                             company="Company A", fields=("categories",))
    result["data"]["categories"].append("HBM")
    assert tech_classifier({"tech_summary": result}) == {"tech_category": "NPU"}


def test_technical_sources_satisfy_team_source_contract(index):
    from investment_scout.evidence import validate_source

    hits = index.search(QUESTIONS[0], company="Company A")
    result = grounded_result(response(hits[0].chunk, field="core_technology"), hits,
                             company="Company A", fields=("core_technology",))
    update = with_technical_sources(lambda state: {"source_evidence": {}}, index)({})
    sources = [*result["evidence"], *(s for rows in update["source_evidence"].values() for s in rows)]
    for source in sources:
        validate_source(source)
    assert sources[0]["publisher"] == "Company A"


@pytest.mark.parametrize("agent", [TechAnalyst, TechClassifier])
def test_search_question_excludes_company_name(agent):
    # 기업명이 들어가면 저자 소개·참고문헌처럼 이름이 반복되는 청크가 상위로 올라옴.
    assert "Mobilint" not in agent.question("Mobilint")


@pytest.mark.parametrize("quote,ok", [
    # 실제 분석에서 탈락했던 PDF 추출 흔적: 공백 삽입, 줄 끝 하이픈, 합자, 끝 생략·마침표
    ("rules of memory to redefine AI inference", True),
    ("information from an end-user perspective remains scarce.", True),
    ("Design of CXL-integrated GPU: We propose", True),
    ("rules of memory to redefine... end-user perspective", True),
    ("information from an end-user perspective...", True),
    # 내용 변경·순서 뒤바뀜·지나치게 짧은 조각은 거부
    ("rules of memory to redefine GPU inference", False),
    ("end-user perspective... rules of memory", False),
    ("rules of memory... AI", False),
])
def test_quote_matching_tolerates_pdf_artifacts_only(index, quote, ok):
    original = index.search(QUESTIONS[0], company="Company A")[0].chunk
    chunk = replace(original, text=("Breaking the rules of memory t o redeﬁn e AI inference. "
                                    "Design of CXL-integrated GPU : We propose a design. "
                                    "Latency information from an end-\nuser perspective remains scarce."))
    result = grounded_result(response(chunk, quote=quote), [SearchHit(chunk, 0.9, {})],
                             company="Company A", fields=("answer",))
    assert (result["status"] == "OK") is ok


def test_graph_nodes_reuse_query_vectors_across_companies(index):
    # 기업명이 없는 고정 질문: 첫 기업 이후 모델 재로딩·질문 재임베딩 없음 (그래프 경로 속도).
    calls = []
    original = index.embedder.encode

    def counted(texts, *, model, query=False):
        if query:
            calls.append(model)
            return [[1.0] + [0.0] * (4 if model == "kure" else 6) for _ in texts]
        return original(texts, model=model, query=query)

    index.embedder.encode = counted

    class NoFacts:
        def generate(self, **kwargs):
            return GroundedResponse(facts=[], missing_information=[])

    analyst = TechAnalyst(index, generator=NoFacts())
    for company in ("Company A", "Company B"):
        analyst({"current_startup": {"name": company}})
    assert calls == ["kure", "jina"]


def test_retrieval_evaluation_scores_rank_of_answer_page(index):
    from investment_scout.rag.evaluation import evaluate

    items = [
        {"company": "Company A", "question": QUESTIONS[0], "answer_contains": ["INT8 inference"]},
        {"company": "Company B", "question": QUESTIONS[1], "answer_contains": ["10 TOPS/W"]},
    ]
    report = evaluate(index, items, ks=(1, 3), modes={"hybrid": None})
    assert report["metrics"]["hybrid"] == {"hit@1": 1.0, "hit@3": 1.0, "mrr@3": 1.0}
    assert report["items"][0]["relevant_pages"] == ["A:p1"]


def test_retrieval_evaluation_rejects_phrase_missing_from_corpus(index):
    from investment_scout.rag.evaluation import evaluate

    with pytest.raises(ValueError, match="정답 구절"):
        evaluate(index, [{"company": "Company A", "question": QUESTIONS[0],
                          "answer_contains": ["not in any document"]}])


def test_collection_removes_documents_dropped_from_manifest(tmp_path):
    pdf = tmp_path / "sample.pdf"
    make_pdf(pdf)
    manifest = tmp_path / "sources.json"
    source = {"company": "Company A", "url": "https://example.com/test.pdf", "title": "Test",
              "document_type": "product", "pdf_path": "sample.pdf", "published_at": None}
    write_json(manifest, {"schema_version": 1, "companies": ["Company A"], "documents": [
        {**source, "document_id": "keep"}, {**source, "document_id": "drop"}]})
    directory = tmp_path / "collected"
    assert collect_manifest(manifest, directory, local_only=True)["total_pages"] == 4
    write_json(manifest, {"schema_version": 1, "companies": ["Company A"],
                          "documents": [{**source, "document_id": "keep"}]})
    result = collect_manifest(manifest, directory, local_only=True)
    assert result["total_pages"] == 2
    assert {r["document_id"]: r["status"] for r in result["records"]} == {"drop": "REMOVED", "keep": "CACHED"}
    assert [d.document_id for d in load_corpus(directory / "corpus.json").documents] == ["keep"]


def test_pipeline_runs_graph_and_reports_each_agent(index):
    from investment_scout.rag.pipeline import run_pipeline

    lines = []
    # 후보 기업 자료가 없는 테스트 인덱스: 검색 결과가 없으므로 OpenAI 호출 없이 '근거 부족'.
    from investment_scout.agents.investment_judge import InvestmentJudge, Judgement

    judge = InvestmentJudge(parse=lambda **kwargs: Judgement(items=[], risks=[]))
    final = run_pipeline(index, max_candidates=2, min_score=0.3, judge=judge,
                         echo=lambda line, **kwargs: lines.append(line))
    text = "\n".join(lines)
    assert "🔍 스타트업 탐색: 적격 후보 20개" in text
    assert "▶ [1/2] Mobilint" in text and "▶ [2/2] HyperAccel" in text
    assert text.count("🔬 기술 분류: 근거 부족") == 2
    assert final["evaluated_startups"] == ["Mobilint", "HyperAccel"]
    assert final["termination_reason"] == "LIMIT_REACHED"
    assert "🧮 투자 판단: HOLD — 총점 0/100" in text


def test_collection_keeps_first_pages_and_respects_shared_limit(tmp_path):
    pdf = tmp_path / "sample.pdf"
    make_pdf(pdf)
    manifest = tmp_path / "sources.json"
    source = {"company": "Company A", "url": "https://example.com/test.pdf", "title": "Test",
              "document_type": "press_release", "pdf_path": "sample.pdf", "published_at": None}
    write_json(manifest, {"schema_version": 1, "companies": ["Company A"],
                          "documents": [{**source, "document_id": "trimmed", "max_pages": 1}]})
    trimmed = collect_manifest(manifest, tmp_path / "a", local_only=True)
    assert trimmed["total_pages"] == 1
    assert len(PdfReader(str(tmp_path / "a" / "trimmed.pdf")).pages) == 1
    # 다른 자료집이 199페이지를 쓰면 2페이지 문서는 등록되지 않음.
    write_json(manifest, {"schema_version": 1, "companies": ["Company A"],
                          "documents": [{**source, "document_id": "full"}]})
    limited = collect_manifest(manifest, tmp_path / "b", local_only=True, page_limit=1)
    assert limited["total_pages"] == 0
    assert limited["records"][0]["error_type"] == "PageLimitError"
