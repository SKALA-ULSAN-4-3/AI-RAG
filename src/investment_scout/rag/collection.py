"""자료 수집: 명시된 URL/PDF만 등록, 실패·한도 초과를 수집 기록에 보존."""

from contextlib import contextmanager
from pathlib import Path
import hashlib
import re
from urllib.parse import urlsplit

from investment_scout.rag.documents import DocumentCorpus
from investment_scout.rag.parsing import parse_pdf
from investment_scout.rag.storage import load_corpus, read_json, save_corpus, write_json


def seed_manifest(candidates: Path, output: Path) -> dict:
    """초기 목록: 기존 20개 기업의 공식 홈페이지, 수집 전 상태를 명시."""
    companies = read_json(candidates)["companies"]
    documents = [{
        "document_id": f"tech_{i:02d}_homepage", "company": company["name"],
        "url": company["website"], "title": f"{company['name']} 공식 홈페이지",
        "document_type": "official_website", "published_at": None,
    } for i, company in enumerate(companies, start=1)]
    manifest = {"schema_version": 1, "companies": [c["name"] for c in companies],
                "documents": documents}
    if output.exists():
        raise ValueError(f"기존 목록을 보존합니다. 수정하거나 다른 경로를 지정하세요: {output}")
    write_json(output, manifest)
    return manifest


@contextmanager
def browser_session():
    """독립 브라우저: 사용자 로그인 세션 없이 공개 문서만 수집."""
    from playwright.sync_api import sync_playwright
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 1280, "height": 900})
        try:
            yield context
        finally:
            context.close()
            browser.close()


def capture_source(context, source: dict, destination: Path) -> str:
    """PDF 원문 다운로드 또는 웹의 A4 PDF 스냅샷 생성."""
    url = source["url"]
    if urlsplit(url).scheme not in {"http", "https"}:
        raise ValueError("수집 URL은 http/https여야 합니다.")
    response = context.request.get(url, timeout=45000)
    if not response.ok:
        raise ValueError(f"HTTP {response.status}: 원문 수집 실패")
    body = response.body()
    if body.startswith(b"%PDF-"):
        destination.write_bytes(body)
        return "original_pdf"
    page = context.new_page()
    try:
        navigation = page.goto(url, wait_until="load", timeout=45000)
        if navigation is None or not navigation.ok:
            raise ValueError("웹 페이지 탐색에 실패했습니다.")
        title = page.title().lower()
        if any(marker in title for marker in ("access denied", "just a moment", "captcha", "403 forbidden")):
            raise ValueError("접근 차단 화면입니다. 공식 PDF 또는 수동 저장 자료가 필요합니다.")
        page.emulate_media(media="screen")
        # 지연 로딩: 스크롤로 드러나는 본문·이미지를 로드한 뒤 PDF 저장.
        page.evaluate("""async () => {
            const pause = ms => new Promise(resolve => setTimeout(resolve, ms));
            for (let y = 0; y < document.body.scrollHeight && y < 100000; y += 650) {
                window.scrollTo(0, y);
                await pause(120);
            }
            await pause(500);
            window.scrollTo(0, 0);
            await pause(500);
        }""")
        # 노이즈 제거: 의미가 명확한 탐색 메뉴만 제외, 표·수치·본문은 보존.
        page.locator("nav, [role=navigation]").evaluate_all("nodes => nodes.forEach(n => n.remove())")
        page.evaluate("document.fonts.ready")
        page.pdf(path=str(destination), format="A4", print_background=True,
                 margin={"top": "15mm", "bottom": "15mm", "left": "12mm", "right": "12mm"},
                 tagged=True, outline=True)
        return "web_pdf"
    finally:
        page.close()


def collect_manifest(manifest_path: Path, directory: Path, *, local_only: bool = False) -> dict:
    manifest = read_json(manifest_path)
    if manifest.get("schema_version") != 1:
        raise ValueError("지원하지 않는 자료 목록 버전입니다.")
    sources = manifest["documents"]
    names = manifest["companies"]
    ids = [source["document_id"] for source in sources]
    if len(ids) != len(set(ids)):
        raise ValueError("자료 목록에 중복 document_id가 있습니다.")
    for source in sources:
        if source["company"] not in names:
            raise ValueError(f"후보 목록에 없는 기업: {source['company']}")
        if not re.fullmatch(r"[A-Za-z0-9_-]+", source["document_id"]):
            raise ValueError("document_id에는 영문·숫자·밑줄·하이픈만 사용할 수 있습니다.")
    directory.mkdir(parents=True, exist_ok=True)
    corpus_path = directory / "corpus.json"
    corpus = load_corpus(corpus_path) if corpus_path.exists() else DocumentCorpus()
    records = []
    # 목록 동기화: 목록에서 뺀 자료(예: 본문 없는 PDF)는 자료집과 200페이지 합계에서 제외.
    for document in [d for d in corpus.documents if d.document_id not in set(ids)]:
        corpus.remove(document.document_id)
        records.append({"document_id": document.document_id, "company": document.company,
                        "url": document.url, "status": "REMOVED", "pages": len(document.pages)})
        print(f"{document.document_id}: REMOVED", flush=True)

    def collect(context=None):
        registered = {d.document_id: d for d in corpus.documents}
        for source in sources:
            identifier = source["document_id"]
            record = {"document_id": identifier, "company": source["company"], "url": source["url"]}
            try:
                if identifier in registered:
                    old = registered[identifier]
                    if any(getattr(old, key) != source.get(key) for key in
                           ("company", "url", "title", "document_type", "published_at")):
                        raise ValueError("등록된 문서 메타데이터 변경: 새 document_id를 사용하세요.")
                    if not old.pdf_path or not Path(old.pdf_path).is_file():
                        raise ValueError("보존된 PDF가 없습니다. 원문 경로를 복원하세요.")
                    if hashlib.sha256(Path(old.pdf_path).read_bytes()).hexdigest() != old.sha256:
                        raise ValueError("PDF가 등록 이후 변경되었습니다. 새 document_id로 등록하세요.")
                    has_text = any(section.text.strip() for page in old.pages for section in page.sections)
                    record.update(status="CACHED" if has_text else "NO_TEXT", pages=len(old.pages))
                else:
                    if source.get("pdf_path"):
                        path = (manifest_path.parent / source["pdf_path"]).resolve()
                        basis = source.get("page_basis", "original_pdf")
                    elif local_only:
                        raise ValueError("로컬 PDF 경로가 없습니다. pdf_path를 지정하세요.")
                    else:
                        path = directory / f"{identifier}.pdf"
                        basis = capture_source(context, source, path)
                    metadata = {key: source.get(key) for key in (
                        "document_id", "company", "url", "title", "document_type", "published_at")}
                    document, warnings = parse_pdf(path, metadata={**metadata, "page_basis": basis})
                    corpus.add(document)
                    save_corpus(corpus, corpus_path)
                    registered[identifier] = document
                    has_text = any(section.text.strip() for page in document.pages for section in page.sections)
                    record.update(status="COLLECTED" if has_text else "NO_TEXT",
                                  pages=len(document.pages), warnings=warnings)
            except Exception as exc:
                # 기업별 격리: 한 자료의 수집 실패로 이미 확인한 자료를 잃지 않음.
                record.update(status="ERROR", error_type=type(exc).__name__, message=str(exc))
            records.append(record)
            print(f"{identifier}: {record['status']}", flush=True)
            write_json(directory / "collection_log.json", records)

    if local_only or all(s.get("pdf_path") for s in sources):
        collect()
    else:
        with browser_session() as context:
            collect(context)
    # 빈 자료집도 저장: 후속 단계에서 '본문 없음'을 명확하게 진단.
    save_corpus(corpus, corpus_path)
    coverage = {name: {"documents": 0, "pages": 0, "searchable_pages": 0, "types": []} for name in names}
    for document in corpus.documents:
        item = coverage.setdefault(document.company, {"documents": 0, "pages": 0,
                                                      "searchable_pages": 0, "types": []})
        item["documents"] += 1
        item["pages"] += len(document.pages)
        item["searchable_pages"] += sum(
            any(section.text.strip() for section in page.sections) for page in document.pages
        )
        item["types"] = sorted(set(item["types"]) | {document.document_type})
    report = {"total_pages": corpus.total_pages, "coverage": coverage, "records": records}
    write_json(directory / "coverage.json", report)
    return report
