# 역할 4 - 보고서 생성·통합 계약

역할 4는 역할 3이 확정한 점수와 결정을 **표시하고 검증**하며, 점수나 투자 판단을 새로 만들지 않습니다. 출력은 Summary → 시장 → 기업/점수표 → 성장·리스크 → Reference 순서의 5페이지 PDF입니다.

## 리서치형 페이지 구성

페이지별로 표·벡터 차트·근거 인용을 배치하며, 단순 요약문 반복 대신 투자 논점과 확인 과제를 제공합니다.

| 페이지 | 구성 |
| --- | --- |
| 1 | 반 페이지 이내 Summary, 투자 의견, 분야별 득점률 그래프, 핵심 논점, 평가 후보 |
| 2 | 시장 규모·성장률·TAM/SAM·수요 표, 근거 인용, 시장 범위·추가 실사 |
| 3 | 기업 프로필, 핵심 기술·장점·한계·상용화, 점수표, 주요 미흡 체크리스트 |
| 4 | 성장·경쟁·리스크 매트릭스, 투자 전 실행 조건, 리스크 반영 점수 그래프 |
| 5 | 실제 사용 출처의 서지정보, URL, 페이지·접근일 및 본문 인용 번호 |

내용을 먼저 측정하고 정확히 5페이지에 배치합니다. 가독성을 해치는 과도한 축소나 출처 URL 잘라내기는 하지 않습니다. 공간이 부족하면 오류를 반환하며 기존 PDF는 보존합니다. Reference가 많으면 2단으로 배치합니다.

내용이 적을 때는 **근거 부족 및 실사 과제**를 표시합니다. 매출·목표주가·ROI나 시계열 수치를 만들어 여백을 채우지 않습니다. 전달된 의견과 점수는 변경하지 않으며 보고서 레이아웃 개선이 분석 품질을 보증하지 않습니다.

Summary는 상단 여백을 포함해 페이지의 중간선 아래로 내려가지 않도록 실제 위치를 검사합니다. Reference는 분량에 따른 밀도 목표 없이 출처 정보만 수록합니다. 출처가 적으면 여백을 유지하며, 여백을 채우기 위한 부가 표·설명은 넣지 않습니다. 기존 전체 5페이지 제한을 넘는 경우 출처를 생략하지 않고 오류를 반환합니다.

시장 추이 그래프가 필요하면 역할 3이 출처가 명시된 `market.series`를 전달할 수 있습니다. 연도는 중복 없이 오름차순, 값은 유한한 0 이상 숫자, 포인트는 2~12개입니다. 출처 원문의 수치만 전달해야 하며 CAGR로 임의 보간하지 않습니다.

```json
{
  "series": {
    "title": "출처 기반 시장 전망",
    "unit": "USD billion",
    "points": [{"year": 2025, "value": 11.8}, {"year": 2030, "value": 56.8}],
    "source_ids": ["market_001"]
  }
}
```

전체 State는 `evaluation_details.items` 및 분석 `claims/citations`를 자동 전달합니다. 독립 handoff는 선택적으로 `score_items`와 각 분석 섹션의 `claims`를 제공할 수 있습니다. 출처 ID는 Reference와 일치해야 합니다.

## 설치와 실행 방법

```bash
uv sync --extra report --group dev
uv run investment-report \
  --input out/role3/handoff.json \
  --out output/pdf/investment_report.pdf \
  --markdown-out output/pdf/investment_report.md
```

레이아웃만 확인하는 합성 데이터는 다음 명령으로 실행합니다. 데모의 수치와 기업은 실제 투자 자료가 아닙니다.

```bash
uv run investment-report \
  --input data/report_demo.json \
  --out output/pdf/scenario_recommended.pdf

uv run investment-report \
  --input data/report_all_hold.json \
  --out output/pdf/scenario_all_hold.pdf
```

한글 글꼴을 자동으로 찾지 못하면 `REPORT_FONT_PATH`에 TTF 또는 TTC 파일 경로를 지정합니다.

## 역할 3 인계 계약

역할 3은 아래 구조를 독립 JSON으로 넘기거나 전체 State의 `role3_handoff`에 넣을 수 있습니다. 현재 역할 3 구현처럼 `evaluation_history[].evaluation_scores`, `evaluation_details`, `final_ranking`, `recommended_startup`, `source_evidence`를 담은 전체 State를 그대로 넘겨도 호환 어댑터가 처리합니다. 역할 3 Judge의 `market_analysis:claim_id` 같은 근거 ID는 실제 Reference의 `source_id`로 자동 변환됩니다.

```json
{
  "schema_version": 1,
  "selected_company": {
    "name": "기업명",
    "founded_year": 2021,
    "main_products": ["제품"],
    "funding_stage": "Series B",
    "tech_category": "AI_ACCELERATOR"
  },
  "evaluated_companies": [],
  "market": {
    "tam": "...",
    "sam": "...",
    "growth_rate": "...",
    "customer_demand": "...",
    "source_ids": ["market_001"]
  },
  "technology": {"summary": "...", "source_ids": ["tech_001"]},
  "competition": {"summary": "...", "source_ids": ["comp_001"]},
  "scorecard": {
    "market": {"score": 20, "reason": "...", "source_ids": ["market_001"]},
    "technology": {"score": 24, "reason": "...", "source_ids": ["tech_001"]},
    "competitive_advantage": {"score": 15, "reason": "...", "source_ids": ["comp_001"]},
    "growth": {"score": 11, "reason": "...", "source_ids": ["market_001"]},
    "deal_terms": {"score": 8, "reason": "...", "source_ids": ["company_001"]}
  },
  "risks": [
    {
      "description": "치명 리스크 설명",
      "fatal": true,
      "penalty": -10,
      "mitigation": "대응 방안",
      "source_ids": ["risk_001"]
    }
  ],
  "growth_outlook": [{"description": "성장 근거", "source_ids": ["market_001"]}],
  "decision": "RECOMMENDED",
  "decision_reason": "판단 근거",
  "total_score": 68,
  "summary": "반 페이지 이내 요약 원문",
  "references": [
    {
      "source_id": "market_001",
      "publisher": "발행 주체",
      "title": "문서 제목",
      "published_at": "2026-01-01",
      "accessed_at": "2026-09-30",
      "url": "https://example.com/source",
      "page": 3
    }
  ]
}
```

점수 키와 최대점은 고정됩니다.

| 키 | 표시명 | 최대점 |
| --- | --- | ---: |
| `market` | 시장성 | 25 |
| `technology` | 제품/기술력 | 30 |
| `competitive_advantage` | 경쟁 우위 | 20 |
| `growth` | 성장가능성 | 15 |
| `deal_terms` | 투자조건 | 10 |

역할 4는 항목 점수 합계와 역할 3이 확정한 리스크 감점을 다시 계산해 `total_score`와 대조합니다. 현재 역할 3 State의 치명 리스크는 기술·운영·법률 유형별 최초 1건에 -10점을 적용한 결과를 그대로 변환합니다. 본문, 점수, 리스크, 성장 전망에서 참조한 `source_ids`가 `references`에 없으면 보고서 생성을 중단합니다. 반대로 참조되지 않은 출처는 Reference에 넣지 않습니다.

## LangGraph 연결

기존 `build_graph`의 `report_node`에 역할 4 노드를 주입합니다.

```python
from investment_scout.reporting import make_report_node

app = build_graph(
    # scout/tech/category/market/competitor/decision 노드 생략
    report_node=make_report_node("output/pdf/investment_report.pdf"),
)
```

노드는 기존 계약에 맞춰 `{"final_report": "..."}`만 반환하고 PDF는 지정 경로에 저장합니다. 따라서 역할 3과 병합할 때 그래프 구조를 변경할 필요가 없습니다.

통합 `pipeline` 명령은 기본적으로 `output/pdf/investment_report.pdf`를 생성합니다. 다른 경로는 `--report-pdf`로 지정합니다.

```bash
uv run python -m investment_scout.rag.cli pipeline \
  --report-pdf output/pdf/investment_report.pdf
```

## 출력 검증

- PDF는 정확히 5페이지가 아니면 실패합니다.
- Summary는 700자로 제한합니다.
- 점수표는 역할 3 점수를 그대로 표시하고 최종합만 재검산합니다.
- Reference는 실제 사용된 `source_ids`만 포함합니다.
- `pypdf`로 생성 후 페이지 수를 다시 확인합니다.
- 제출 전에는 렌더링 PNG에서 글자 잘림, 표 겹침, 한글 누락을 확인합니다.
- 추천·전원 보류 시나리오 PDF를 각각 생성해 판정과 점수 표시를 확인합니다.
- 최종 제출 파일명은 `semiconductor_investment_report.pdf`로 고정합니다.
