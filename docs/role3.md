# 역할 3 — 시장성·경쟁사·투자 판단

## 실행

~~~bash
uv sync --extra tech-rag --extra embeddings --extra live-search --group dev
uv run python -m investment_scout.rag.cli collect --manifest data/market_sources.json --directory out/market_rag/documents
uv run python -m investment_scout.rag.cli index --corpus out/market_rag/documents/corpus.json --index out/market_rag/index
uv run python -m investment_scout.rag.cli pipeline --max-candidates 3
~~~

`.env`에 `OPENAI_API_KEY`, `OPENAI_MODEL`, `TAVILY_API_KEY`가 필요합니다. `OPENAI_JUDGE_MODEL`을 지정하면 투자 판단 채점에만 그 모델을 씁니다(비우면 `OPENAI_MODEL`). 시장 인덱스가 없거나 Tavily 키가 없으면 `pipeline`이 해당 노드를 자리 표시 노드로 바꾸고 그 사실을 출력합니다.

## 에이전트

| 에이전트 | 파일 | RAG | 입력 → 출력 |
| --- | --- | --- | --- |
| 📊 시장성 평가 | `agents/market_analyst.py` | O | `tech_category` → 세부 시장 → 시장 보고서 검색 → `market_analysis` (`market_size`, `growth_rate`, `customer_demand`) |
| 🥊 경쟁사 비교 | `agents/competitor.py` | X | Tavily 웹 검색 + 자사 기술 근거 → `competitor_analysis` (`main_competitors`, `competitive_comparison`, `entry_barriers`) |
| 🧮 투자 판단 | `agents/investment_judge.py` | X | 세 분석의 검증된 주장 + 후보 탐색 출처 → `evaluation_scores`, `evaluation_details`, `investment_decision`, `hold_reason` |

시장·경쟁 노드는 기술 노드와 같은 인용 검증(원문 인용이 실제 청크·스니펫에 있어야 함)을 거치고, 인용한 출처를 `source_evidence[기업명]`에 등록합니다. 그래야 `record_evaluation`의 출처 검증에서 주장이 제거되지 않습니다.

## 시장 자료 (200페이지 공유)

`data/market_sources.json`의 `companies`는 기업이 아니라 세부 시장입니다: `AI_CHIP`, `CXL_MEMORY`, `SILICON_PHOTONICS`, `IN_MEMORY_COMPUTE`, `CHIPLET`. 시장조사 보도자료 6건을 `max_pages`로 수치가 있는 앞쪽만 보관해 18페이지입니다. `collect`는 기술·시장 자료집을 합산해 200페이지를 넘지 않게 막습니다(현재 179 + 18 = 197). 보도자료 뒤쪽의 관련 기사·광고·시세 페이지는 한도 낭비이자 검색 잡음(다른 시장의 수치)이라 제외했습니다.

기술 분류 → 세부 시장: NPU·AI_ACCELERATOR·GPU → AI_CHIP, CXL → CXL_MEMORY, PHOTONICS → SILICON_PHOTONICS, IN_MEMORY_COMPUTE → IN_MEMORY_COMPUTE(+AI_CHIP), OTHER → CHIPLET. 분류가 없으면 전체 시장에서 검색합니다. 특화 시장을 범용 AI 칩 시장보다 먼저 쓰고, 시장별로 검색 결과를 균등 배분합니다.

## 투자 판단 기준 (RAG-Design 설계서 그대로)

| 항목 | 비중 | 체크리스트 (배점) |
| --- | --- | --- |
| 시장성 | 25 | 시장 크기 (10), 고객 지불 이유 (10), 초기 고객 반응 (5) |
| 제품/기술력 | 30 | 실제 문제 해결 (5), 독창적 기술·상용화 역량 (15), 수익 모델 (10) |
| 경쟁 우위 | 20 | 차별성 (5), 진입장벽 (10), 시장 선점 우위 (5) |
| 성장가능성 | 15 | Scale-up 구조 (8), 10년 후 경쟁력 (7) |
| 투자조건 | 10 | 팀 신뢰 (5), 투자 조건·Valuation (5) |
| 리스크 | – | 기술·운영·법률 유형별로 치명 리스크가 있으면 각 −10 (최대 −30) |

체크리스트 문구와 배점은 `SCORECARD` 상수에 설계서 원문 그대로 있고, `tests/test_role3.py::test_scorecard_matches_design_document`가 설계서 표와 일치하는지 검사합니다.

역할 분담:

- **LLM(Judge)**: 13개 항목의 점수·근거 문장·근거 ID와 리스크를 제안합니다. 항목마다 관련 근거 후보 ID를 코드가 미리 제시합니다.
- **코드**: 배점 상한 적용, 존재하지 않는 근거 ID 제거, 근거 없는 항목 0점, 근거 없는 리스크는 감점하지 않음, 분야 합계·총점·감점·판정 계산.
- **필수 정보 규칙(코드)**: 인용 근거에 팀 경력 정보가 없으면 팀 0점, 밸류에이션 정보가 없으면 투자 조건 최대 2점, 고객·공급·매출 정보가 없으면 초기 고객 반응 최대 2점. 설립 연도로 팀 신뢰를, 투자 단계로 밸류에이션 적정성을 추정하지 않기 위한 규칙입니다.
- **재현성**: 같은 입력도 LLM 채점이 흔들려(측정 시 최대 12점) 3회 채점 후 항목별 중앙값을 씁니다. 반복 실행에서 판정이 같게 유지되는 것을 확인했습니다.

판정: `총점 = 분야 합계(최대 100) − 10 × 치명 리스크가 있는 유형 수`(같은 유형 여러 건은 한 번만 감점), 총점 70점 이상이면 `RECOMMENDED`, 아니면 `HOLD`. **70점은 설계서에 없어 실습 계획서 예시를 적용한 값이며 팀 확정이 필요합니다.** 추천이더라도 `record_evaluation`이 평가 필수 정보(기술 `core_technology`·`differentiation`, 시장 `market_size`·`growth_rate`, 경쟁 `main_competitors`) 부족을 발견하면 HOLD로 바꿉니다.

## 역할 4 전달 항목

`evaluation_history[]`의 각 기록에 들어 있습니다.

- `evaluation_scores`: 분야 점수, `분야.항목` 점수, `risk_penalty`, `total`
- `evaluation_details`: 항목별 질문·배점·점수·한 줄 이유·근거 ID, 리스크(치명/참고), 감점 유형, 기준선, 3회 채점 점수

`pipeline` 실행 시 기업마다 분야 소계, 체크리스트 항목별 점수와 한 줄 이유, 리스크 감점, 총점 계산식을 출력합니다.
- `market_analysis`·`competitor_analysis`: 검증된 주장과 인용(URL·페이지·원문)
- `hold_reason`: 총점 미달·치명 리스크·미흡 항목 또는 핵심 정보 부족 사유
- `source_evidence[기업명]`: 인용한 출처 전체 (`publisher`, `accessed_at`, `published_at` 포함 → REFERENCE 작성용)
