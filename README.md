# AI Startup Investment Evaluation Agent

본 프로젝트는 Semiconductor 스타트업의 투자 가능성을 평가하는 에이전트를 설계하고 구현한 실습 프로젝트입니다. 이 문서는 현재 구현한 **2번 담당 영역: 기술 RAG·기술 분류**를 중심으로 작성했습니다.

## Overview

- Objective: 20개 후보 기업의 핵심 기술, 장점·한계, 상용화 상태와 기술 분야를 근거와 함께 정리해 투자 평가에 전달
- Method: LangGraph 에이전트와 PDF·웹 문서 기반 RAG
- 범위: 기술 요약과 기술 분류. 시장성·경쟁·최종 투자 판단 및 보고서는 다른 담당 영역

## Progress

- 20개 기업의 공식 자료 URL 28개를 등록하고, 웹 문서를 PDF로 저장해 전체 **181/200페이지**를 수집했습니다.
- 19개 기업의 텍스트를 검색할 수 있으며, 1개 기업은 사이트 인증서 문제로 수집 자료가 부족합니다. 이미지로만 구성된 특허 PDF 1건은 텍스트 검색에서 제외됩니다.
- 329개 청크로 FAISS 인덱스를 만들었습니다. 기술 분석 결과(gpt-4o-mini)는 근거가 확인된 주장 64개와 인용 67개를 포함하며, 기술 분야는 18개 기업에서 분류되고 2개 기업은 `근거 부족`으로 처리됩니다. LLM 응답에 따라 재실행 시 수치가 달라질 수 있습니다.
- 기술 질문 6개로 검색 결과의 URL·실제 PDF 페이지를 확인했습니다. 검색 품질의 Hit Rate@K와 MRR, 검색 결과의 의미적 적합성은 아직 측정·검토되지 않았습니다.
- 관련 테스트 140개가 통과했습니다. 수집 자료와 분석 결과는 로컬 `out/`에 있으며 GitHub에는 포함되지 않습니다.

## Features

- 기업 홈페이지·제품 문서·논문·특허·보도자료 수집 및 전체 200페이지 제한
- 웹 문서 PDF 저장, 페이지별 메타데이터 정리, PDF 텍스트 추출과 노이즈 제거
- 헤딩·페이지 기준 청킹 및 KURE/Jina 이중 임베딩을 사용하는 FAISS 검색
- 출처 URL과 PDF 페이지를 포함한 기술 장점·한계·상용화 상태 요약 및 기술 분야 분류
- 확인 가능한 근거가 없을 때 `근거 부족` 반환, 담당 역할 3에 전달할 JSON·자료 ZIP 생성

## Tech Stack

- Framework: LangGraph
- LLM/Generator: OpenAI `gpt-4o-mini` (기술 요약·분류)
- LLM/Judge: 담당 범위 밖, 모델 미확정
- Retrieval: FAISS (Hit Rate@K, MRR 미측정)
- Embedding: `nlpai-lab/KURE-v1`, `jinaai/jina-embeddings-v5-text-small`
- PDF/Web: pypdf, Playwright

## Agents

- 기술 요약 에이전트: 검색 근거에 따라 기술 장점·한계·상용화 상태를 `tech_summary`로 반환
- 기술 분류 에이전트: NPU, AI Accelerator, HBM, EDA/공정 AI 등 기술 분야를 `tech_category`로 반환

## Architecture

```mermaid
flowchart LR
    A[공식 자료 목록] --> B[웹 PDF 저장·PDF 파싱]
    B --> C[페이지·헤딩 청킹]
    C --> D[KURE·Jina 임베딩]
    D --> E[FAISS 검색]
    E --> F[기술 요약]
    F --> G[기술 분류]
    G --> H[tech_summary·tech_category·인용 근거]
```

기술 노드의 기존 LangGraph 연결 방법은 [기술 RAG 실행 안내](docs/tech_rag.md)를 참고하세요.

## Directory Structure

```text
├── data/tech_sources.json                # 20개 기업의 수집 대상 자료
├── src/investment_scout/agents/
│   ├── tech_analyst.py                  # 기술 요약 에이전트
│   └── tech_classifier.py               # 기술 분류 에이전트
├── src/investment_scout/rag/             # 수집·파싱·청킹·임베딩·FAISS·CLI
├── docs/tech_rag.md                      # 설치, 실행 및 역할 3 전달 안내
├── tests/                                # 기술 RAG 검증 테스트
├── out/tech_rag/                         # 수집 PDF·인덱스·결과물 (Git 제외)
├── .env.example                          # 환경변수 예시 (.env는 Git 제외)
└── README.md
```

## Usage

프로젝트 루트(`pyproject.toml`이 있는 폴더)의 터미널에서 실행합니다. 1번은 기존 README의 후보 검증 명령을 사용하고, 이어서 2번의 문서 수집과 기술 분석을 실행합니다. `uv`가 없다면 [기술 RAG 실행 안내](docs/tech_rag.md)의 Windows/macOS `pip` 설치 절차를 따르세요.

### 1번: 후보 탐색·적격성 확인

```bash
uv sync --group dev
uv run investment-scout scout --out out/scout_result.json
```

> `uv sync`는 명령에 지정하지 않은 extra 패키지를 삭제합니다. 2번까지 설치한 뒤 1번의 `uv sync --group dev`를 다시 실행하면 FAISS·임베딩 패키지가 지워지므로, 이후에는 2번의 `uv sync` 명령을 사용하세요.

후보 목록은 `data/candidates_verified.json`에 있습니다. 기존 그래프를 별도로 확인하려면 다음 명령을 사용할 수 있습니다. 현재 `investment-scout run`은 2번의 새 기술 RAG 결과를 자동으로 읽지 않으며, 기술 노드 연결 방법은 [기술 RAG 실행 안내](docs/tech_rag.md)에 설명했습니다.

```bash
uv run investment-scout run --no-embeddings --out out/investment_report.json
```

### 2번: 기술 RAG·기술 분류

`.env.example`을 참고해 `.env`에 OpenAI API 키를 설정합니다. `collect`와 `index`까지는 OpenAI 키가 필요하지 않습니다. `collect`는 일부 URL 수집에 실패해도 성공한 문서를 저장하며 종료 코드가 1일 수 있으므로, 출력과 `out/tech_rag/documents/`의 수집 기록을 확인하세요.

```bash
uv sync --extra tech-rag --extra embeddings --group dev
uv run python -m playwright install chromium --only-shell
uv run python -m investment_scout.rag.cli doctor
uv run python -m investment_scout.rag.cli collect
uv run python -m investment_scout.rag.cli index
uv run python -m investment_scout.rag.cli verify --company HyperAccel
uv run python -m investment_scout.rag.cli analyze
uv run python -m investment_scout.rag.cli package
uv run pytest -q
```

`verify`는 6개 질문의 검색 결과를 만들지만, 본문이 질문의 답을 실제로 뒷받침하는지는 사람이 확인해야 합니다. `analyze`는 20개 기업의 `tech_summary`·`tech_category`를 `out/tech_rag/analyze.json`에 저장합니다. 역할 3에 넘길 인용 근거와 원본 PDF는 `out/tech_rag/tech_handoff.zip`에 묶입니다. 상세 검증 방법은 [기술 RAG 실행 안내](docs/tech_rag.md)를 참고하세요.

`out/`과 `.env`는 Git에서 제외됩니다. 새 환경에서 다시 수집하면 웹 자료나 OpenAI 응답이 달라질 수 있으므로 결과 파일이 완전히 같다고 보장할 수 없습니다. 같은 PDF 페이지를 기준으로 검토하려면 생성한 자료 ZIP을 별도로 전달해야 합니다.
