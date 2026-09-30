# 역할 2 — 기술 RAG 실행·검증·인계

## Git 공유와 재실행 결과

Git에는 코드, `data/tech_sources.json`, 의존성 잠금 파일, `.env.example`, 테스트와 이 가이드가 올라갑니다. 실제 API 키가 들어 있는 `.env`, 가상환경 `.venv/`, 수집 PDF·FAISS 인덱스·분석 JSON·전달 ZIP이 들어 있는 `out/`은 Git에서 제외됩니다.

새 컴퓨터에서 실행하면 같은 **절차와 검증 규칙**이 적용됩니다. 다만 `out/`의 원본 PDF를 공유하지 않으므로 웹 문서는 실행 시점에 다시 수집됩니다. 홈페이지 변경·접속 차단·인쇄 페이지 수 변경으로 수집 범위와 200페이지 한도 결과가 달라질 수 있고, 임베딩 모델 파일과 OpenAI 응답도 시점·실행 환경에 따라 달라질 수 있습니다. 따라서 결과 JSON의 문장·점수·분류가 매번 완전히 같다고 보장하지 않습니다. 이번 인용 페이지까지 동일하게 확인하려면 별도로 생성한 `tech_handoff.zip`의 PDF를 함께 전달하세요.

## 어디에서 실행하나요?

VS Code의 **터미널 → 새 터미널**을 열고 pyproject.toml이 있는 프로젝트 최상위 폴더에서 실행합니다.
documents.py를 직접 실행하지 않습니다. 실행 진입점은 아래 명령입니다.

~~~bash
python -m investment_scout.rag.cli --help
~~~

## 설치

Python 3.11 이상이 필요합니다. 기존 .venv가 있으면 생성 단계를 생략합니다.

Windows PowerShell:

~~~powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[tech-rag,embeddings]" pytest
python -m playwright install chromium --only-shell
~~~

활성화가 차단되면 아래 명령의 python을 .\.venv\Scripts\python.exe로 바꾸면 됩니다.
시스템 실행 정책을 바꿀 필요는 없습니다.

macOS:

잠금 파일의 FAISS 패키지에는 macOS 14 이상용 바이너리가 제공됩니다. 더 오래된 macOS에서는 호환 버전 확인이 필요합니다.

~~~bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[tech-rag,embeddings]" pytest
python -m playwright install chromium --only-shell
~~~

uv를 사용한다면 다음 명령으로 잠금 파일에 맞춰 설치합니다.
이후 python 대신 uv run python을 사용합니다.

~~~bash
uv sync --extra tech-rag --extra embeddings --group dev
~~~

Colab에서도 같은 Python 모듈을 실행할 수 있습니다.
Linux 브라우저 의존성이 부족하면 playwright install --with-deps chromium을 사용합니다.

## 1. 환경 설정

.env는 만들어져 있으며 Git에서 제외됩니다. 실제 키는 .env에만 입력합니다.
.env.example은 빈 설정 양식으로 유지합니다.

~~~dotenv
OPENAI_API_KEY=본인_API_키
OPENAI_MODEL=gpt-4o-mini
EMBEDDING_DEVICE=cpu
EMBEDDING_BATCH_SIZE=4
RAG_MIN_SCORE=0.30
~~~

요청한 모델 ID는 gpt-4o-mini이며 Responses API의 구조화 응답을 지원합니다.
OPENAI_API_KEY의 자리표시자는 실제 값으로 바꿔야 합니다. OpenAI는 기술 요약·분류에만 사용합니다.

~~~bash
python -m investment_scout.rag.cli doctor
~~~

NOT_SET은 설정 누락, NOT_INSTALLED는 패키지 미설치입니다. 키 값은 출력하지 않습니다.
수집·인덱싱·검색에는 OpenAI 키가 필요하지 않습니다. 실제 호출 권한은 API 실행 시 확인됩니다.

## 2. 자료 수집

data/tech_sources.json에 후보 20개 기업의 공식 홈페이지를 등록했습니다.
등록 자체는 원문 확인을 의미하지 않습니다. 수집 명령으로 확인합니다.

~~~bash
python -m investment_scout.rag.cli collect
~~~

웹은 A4 PDF로 저장한 실제 페이지, PDF 원문은 파일의 물리적 페이지를 사용합니다.
페이지는 1부터 시작하며 인쇄된 로마 숫자 등의 페이지 라벨과 다를 수 있습니다.
빈 페이지도 포함해 **모든 기업 합계 200페이지**를 제한합니다.
초과 자료는 자료집에 등록하지 않고 오류를 남깁니다.

| 출력 | 용도 |
| --- | --- |
| out/tech_rag/documents/*.pdf | 원문 또는 웹 스냅샷 |
| out/tech_rag/documents/corpus.json | 메타데이터, 페이지별 본문 |
| out/tech_rag/documents/collection_log.json | 성공·실패·빈 페이지 진단 |
| out/tech_rag/documents/coverage.json | 기업별 자료 수, 유형, 페이지 합계 |

실패가 있으면 종료 코드 1을 반환하지만 성공 자료는 보존합니다.
재실행 시 등록된 자료를 재사용하며 PDF의 SHA-256을 검사합니다.
새 버전은 새 document_id를 사용합니다. 이미 존재하는 자료를 갱신하려면 새 자료집 경로로 재수집할 수도 있습니다.
자료가 없는 기업도 20개 대상에서 제외하지 않고 이후 분석에서 “근거 부족”을 반환합니다.

초기 자료는 홈페이지 중심입니다. 공식 제품 문서·논문·특허·보도자료를 추가하려면
목록의 documents에 다음 형식의 항목을 넣습니다. 확인되지 않은 발행일은 null입니다.

~~~json
{
  "document_id": "mobilint_product_001",
  "company": "Mobilint",
  "url": "확인한 실제 출처 URL",
  "title": "확인한 문서 제목",
  "document_type": "product",
  "published_at": null,
  "pdf_path": "../out/manual/product.pdf",
  "page_basis": "original_pdf"
}
~~~

문서 유형은 official_website, product, paper, patent, press_release 중 하나입니다.
pdf_path는 선택 사항으로 목록 파일 기준의 상대 경로를 사용할 수 있습니다.
없으면 URL을 수집합니다. 직접 저장한 웹 PDF는 page_basis를 web_pdf로 지정합니다.
네트워크 없이 로컬 PDF만 등록하려면 collect --local-only를 실행합니다.
접근 차단·인증서 오류를 확인된 자료로 처리하지 않습니다.

새 초기 목록이 필요할 때만 다음 명령을 사용합니다. 기존 목록을 덮어쓰지 않습니다.

~~~bash
python -m investment_scout.rag.cli seed --manifest out/new_sources.json
~~~

## 3. KURE/Jina 인덱스 생성

현재 수집 결과(2026-09-30): 20개 기업에 대해 총 179페이지를 등록했고, 20개 기업 모두 검색 가능한 본문을 확보했습니다. HyperAccel·Panmnesia의 논문, Articron에 관한 연세대 보도자료, iHW에 관한 Microchip 보도자료, Mobilint ARIES 제품 문서, Semunite 기사(한국신용신문), Pebble Square 일본 법인 설립 소개(JETRO), XCENA·BOS 보도자료가 포함됩니다. 본문 텍스트가 없던 Articron 홈페이지 스냅샷과 Mobilint 특허 PDF(합계 18페이지)는 목록에서 뺐습니다. 목록에서 뺀 자료는 다음 `collect` 때 자료집과 페이지 합계에서 제거되고 `REMOVED`로 기록됩니다. HTTP 403·인증서 오류로 실패하는 URL 6건 때문에 `collect`가 종료 코드 1을 반환하며, 이는 실패를 숨기지 않기 위한 동작입니다. 실제 상태는 `out/tech_rag/documents/coverage.json`과 `collection_log.json`에서 확인하세요.
현재 분석(gpt-4o-mini, temperature 0)은 20개 기업을 모두 처리했고, 인용 검증을 통과한 주장 78건(인용 79건)을 생성했습니다. 기술 분야는 18개 기업에서 확인됐고 2개 기업(iHW, Oxmiq Labs)은 `근거 부족`입니다. temperature 0에서도 OpenAI 응답이 완전히 같지는 않아, 두 번 실행 시 20개 중 18개 기업의 분류가 일치했습니다. 일부 항목이 빠지면 결과 상태가 `INSUFFICIENT_DATA`로 표시됩니다. 이 수치는 문서 추가와 재실행에 따라 달라집니다.

~~~bash
python -m investment_scout.rag.cli index
~~~

첫 실행은 Hugging Face 모델 다운로드가 필요합니다. CPU에서도 실행하지만 시간이 걸립니다.
메모리가 부족하면 EMBEDDING_BATCH_SIZE를 1로 줄입니다.
Colab GPU 사용 시 EMBEDDING_DEVICE=cuda로 바꿀 수 있습니다.

두 모델 각각으로 전체 청크를 인덱싱하여 질문과 문서의 벡터 공간을 일치시킵니다.
Jina는 retrieval 작업을 지정하고 query/document 프롬프트를 구분합니다.

- 짧은 한국어 일반 질문: KURE 0.7 + Jina 0.3.
- 영문·전문용어·장문 질문: KURE 0.3 + Jina 0.7.
- 검색 대상: 요청한 기업의 청크만 사용.
- 점수: 정규화 벡터의 cosine 점수. 검색 결과별 min-max 보정 없음.
- 하한: RAG_MIN_SCORE=0.30. 20개 기업의 기술 질문 점수 분포(관련 청크가 0.30~0.35에 다수)를 보고 정한 값이며, 정답셋으로 보정한 값은 아닙니다.
- 질문: 기업명을 넣지 않습니다(검색이 이미 기업별로 필터링됨). 영문 기술 용어를 병기해 영문 스펙 문서에 Jina 가중치 0.7이 적용됩니다.

청킹 기본값은 1,200자/중첩 150자입니다. 페이지·헤딩 경계를 넘지 않습니다.
PDF 목차와 본문에서 확인한 헤딩만 사용하고, 목차가 없으면 페이지 중심으로 나눕니다.
2단 논문 PDF는 좌우 열의 문장이 섞이지 않도록 읽기 순서 추출을 사용합니다. 인용 검증에서는 줄 끝 하이픈으로 나뉜 단어만 이어 붙여 비교합니다.
스캔 OCR, 도표의 시각 해석, 복잡한 표의 의미 복원은 포함하지 않습니다.
빈/스캔 페이지는 진단으로 남기며 임의의 텍스트를 생성하지 않습니다.

out/tech_rag/index에 인덱스를 저장합니다. 자료 변경 후 index를 다시 실행합니다.
한 번에 모델 하나만 메모리에 유지하고, 여러 질문은 일괄 임베딩하여 재로딩 비용을 줄입니다.

## 4. 검색·질문 6개 검증 — OpenAI 호출 없음

~~~bash
python -m investment_scout.rag.cli search --company Mobilint --question "NPU 성능과 전력 효율은?"
python -m investment_scout.rag.cli verify --company Mobilint
~~~

search.json에 본문·기업·URL·페이지·점수·PDF 경로가 저장됩니다.
verify.json에는 핵심 기술, 성능, 장점, 한계, 상용화, 분류에 관한 질문 6개의 검색 결과가 저장됩니다.
다른 기업은 --company를 후보 목록의 정확한 이름으로 바꿉니다.

1. out/tech_rag/verify.json에서 URL과 page를 확인합니다.
2. pdf_path의 PDF를 열고 해당 물리적 페이지를 확인합니다.
3. 원문이 질문의 답을 뒷받침하는지, 단위·조건·시점이 맞는지 대조합니다.
4. 관련 결과가 없는 질문이 INSUFFICIENT_DATA인지 확인합니다.

검색 성공 자체가 의미적 정답은 아닙니다.
semantic_review: PENDING과 REVIEW_REQUIRED는 사람이 확인해야 한다는 표시입니다.

## 4-1. 검색 품질 평가 (Hit Rate@K, MRR) — OpenAI 호출 없음

~~~bash
python -m investment_scout.rag.cli evaluate
~~~

`data/retrieval_eval.json`의 46문항(20개 기업, 한국어·영어 혼합)으로 측정합니다. 각 문항의 정답은 `answer_contains` 구절이 들어 있는 페이지입니다. 페이지 번호를 직접 적지 않아 자료를 다시 수집해도 정답이 유지되며, 구절을 자료에서 찾지 못하면 평가를 중단합니다. 순위는 임계값 없이 해당 기업 청크 전체에서 매기고, 상위 10개 안에서 정답 페이지의 첫 순위로 계산합니다.

| 방식 | Hit@1 | Hit@3 | Hit@5 | MRR@10 |
| --- | --- | --- | --- | --- |
| 하이브리드 (질문 언어별 0.7/0.3) | 0.870 | 0.978 | 0.978 | 0.923 |
| KURE 단독 | 0.783 | 0.935 | 0.978 | 0.862 |
| Jina 단독 | 0.804 | 0.978 | 0.978 | 0.888 |

무작위 순위의 MRR@10은 약 0.39입니다. 청크가 20개 이상인 4개 기업(Axelera AI, HyperAccel, Mobilint, Panmnesia)만 보면 하이브리드 0.955, KURE 0.848, Jina 0.864, 무작위 0.236입니다. 정답셋은 사실 확인형 질문이므로, 요약형 질문의 검색 품질은 `verify` 결과를 사람이 확인해 보완합니다. 결과는 `out/tech_rag/evaluate.json`에 문항별 순위와 함께 저장됩니다.

## 5. 기술 답변·전체 기업 분석 — OpenAI 호출 발생

~~~bash
python -m investment_scout.rag.cli ask --company Mobilint --question "상용화 상태는?"
python -m investment_scout.rag.cli analyze --company Mobilint
python -m investment_scout.rag.cli analyze
python -m investment_scout.rag.cli package
~~~

마지막 명령은 전체 20개 기업을 분석합니다.
out/tech_rag/analyze.json에 역할 3 인계 결과가 저장됩니다.
`package`는 분석 JSON, 수집 기록, 원본 PDF를 `out/tech_rag/tech_handoff.zip`에 넣고 PDF 경로를 압축 파일 안의 상대 경로로 바꿉니다. Mac/Windows에서 압축을 풀어 함께 전달하세요. `.env`와 API 키는 포함되지 않습니다.
API 장애 시 앞서 완료한 결과는 complete: false로 보존하고 종료 코드 1을 반환합니다.
전체 분석 완료 시 complete: true입니다. 기술 분석 완료와 투자 추천은 별개입니다.

~~~text
results[]
  current_startup.name
  tech_summary
    status: OK | INSUFFICIENT_DATA
    data: core_technology, differentiation, advantages, limitations, commercialization, categories
    claims[]: text, source_ids, data_keys, citations[{url, page, quote, pdf_path, page_basis}]
    evidence[]: source_id, company, url, page, text, ...
    missing_information[]
    errors[]
  tech_category
  source_evidence: {기업명: 근거 목록}
~~~

근거 없는 항목은 data에서 제외하고 missing_information에 “근거 부족”을 남깁니다.
검색 결과가 없으면 OpenAI를 호출하지 않습니다. 청크 ID, 기업, 인용문 원문 일치를 검증합니다.
인용 대조 시 PDF 추출 흔적(단어 사이 공백, 줄 끝 하이픈, 합자 ﬁ)과 인용 끝의 `...`·마침표는 무시하며, 인용 중간의 `...`는 생략으로 보고 각 구간이 원문에 순서대로 있어야 통과합니다.
원문 인용 검증은 주장의 의미적 정확성을 완전히 보증하지 않으므로 위 페이지 대조가 필요합니다.
API·설정 오류는 자료 부족으로 위장하지 않고 오류로 종료합니다.

분류는 NPU, AI_ACCELERATOR, HBM, DRAM, GPU, EDA_PROCESS_AI, IN_MEMORY_COMPUTE,
CXL, PHOTONICS, OTHER를 지원합니다. 근거가 없으면 tech_category도 “근거 부족”입니다.
out은 Git에서 제외됩니다. 인계 시 JSON과 검증에 사용한 PDF를 별도로 전달합니다.
PDF 경로는 실행 컴퓨터 기준이므로 다른 컴퓨터에서는 경로를 맞춰야 합니다.

## 기존 그래프 연결

기존 run 명령은 종전 노드를 사용합니다. 새 기술 노드는 다음처럼 주입합니다.
출처 등록 래퍼를 함께 써야 평가 저장 단계에서 새로운 source_id가 제거되지 않습니다.

~~~python
from pathlib import Path
from investment_scout.agents.startup_scout import make_scout_node
from investment_scout.agents.tech_analyst import TechAnalyst
from investment_scout.agents.tech_classifier import TechClassifier
from investment_scout.graph import build_graph
from investment_scout.rag.index import DualFaissIndex
from investment_scout.rag.integration import with_technical_sources

# scout와 나머지 노드는 각 담당자가 만든 인스턴스/함수입니다.
index = DualFaissIndex.load(Path("out/tech_rag/index"))
app = build_graph(
    scout_node=with_technical_sources(make_scout_node(scout), index),
    tech_node=TechAnalyst(index),
    category_node=TechClassifier(index),
    market_node=market_node,
    competitor_node=competitor_node,
    decision_node=decision_node,
    report_node=report_node,
)
~~~

## 코드 테스트 — 모델 다운로드·API 호출 없음

~~~bash
python -m pytest -q -p no:cacheprovider
python -m pytest tests/test_rag_documents.py tests/test_tech_rag.py -q -p no:cacheprovider
~~~

실제 FAISS와 PDF 파서를 사용하고 임베딩·생성 응답만 테스트 대역으로 대체합니다.
가상 기술 질문 5개로 기업·URL·페이지 연결을 검증합니다.
이는 실제 모델의 검색 정확도 평가와 구분해야 합니다.

## 공식 사용법 근거

- [KURE-v1](https://huggingface.co/nlpai-lab/KURE-v1)
- [Jina v5 small](https://huggingface.co/jinaai/jina-embeddings-v5-text-small)
- [OpenAI 구조화 응답](https://developers.openai.com/api/docs/guides/structured-outputs)
- [pypdf 텍스트 추출](https://pypdf.readthedocs.io/en/stable/user/extract-text.html)
