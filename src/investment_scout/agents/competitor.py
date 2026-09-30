"""경쟁사 비교 노드 (RAG 미적용, 설계서 기준): 웹 검색 결과로 경쟁 구도·차별성·진입장벽 비교."""

from dataclasses import fields as dataclass_fields
from datetime import datetime, timezone
import hashlib
from urllib.parse import urlsplit

from investment_scout.agents.market_analyst import register_sources
from investment_scout.contracts import DEFAULT_REQUIRED_ANALYSIS_DATA, empty_analysis_result
from investment_scout.rag.chunking import Chunk
from investment_scout.rag.generation import (
    COMPARISON_AXES,
    COMPETITOR_FIELDS,
    COMPETITOR_INSTRUCTIONS,
    GroundedResponse,
    OpenAIGenerator,
    grounded_result,
    loose_text,
)
from investment_scout.rag.index import SearchHit

CHUNK_FIELDS = {field.name for field in dataclass_fields(Chunk)}


def web_chunk(company: str, hit: dict, accessed_at: str) -> Chunk:
    """웹 검색 스니펫을 인용 가능한 출처로 변환: 페이지 개념이 없어 page=1, page_basis=web_search."""
    digest = hashlib.sha256(f"{hit['url']}\n{hit['snippet']}".encode()).hexdigest()[:12]
    return Chunk(
        chunk_id=f"web_{digest}", document_id=f"web_{digest}", company=company, url=hit["url"],
        title=hit["title"], document_type="web_search", published_at=hit.get("published_at"),
        page=1, heading="", text=f"{hit['title']}\n{hit['snippet']}", page_basis="web_search",
        publisher=(urlsplit(hit["url"]).hostname or "").removeprefix("www."), accessed_at=accessed_at,
    )


def own_chunks(state: dict) -> list[Chunk]:
    """비교 기준: 기술 요약이 인용한 자사 근거 (자사 성능·제품과 경쟁사 비교에 인용 가능)."""
    evidence = (state.get("tech_summary") or {}).get("evidence") or []
    return [Chunk(**{k: v for k, v in item.items() if k in CHUNK_FIELDS}) for item in evidence]


def valid_competitors(response: GroundedResponse, company: str) -> tuple[GroundedResponse, list[str]]:
    """경쟁사 이름 검증: 인용문에 이름이 없거나 평가 대상 자신이면 제외 (규격·기술명 오인 방지)."""
    kept, dropped = [], []
    for fact in response.facts:
        if fact.field == "main_competitors":
            name = loose_text(fact.text).lower()
            quoted = any(name and name in loose_text(c.quote).lower() for c in fact.citations)
            if not quoted or name == loose_text(company).lower():
                dropped.append(f"경쟁사 제외: {fact.text} (인용문에 이름 없음 또는 평가 대상 자신)")
                continue
        kept.append(fact)
    return GroundedResponse(facts=kept, missing_information=[*response.missing_information, *dropped]), dropped


class CompetitorAnalyst:
    def __init__(self, search_provider, *, generator=None, max_results=6):
        self.search = search_provider
        self.generator = generator or OpenAIGenerator(COMPETITOR_INSTRUCTIONS)
        self.max_results = max_results

    @staticmethod
    def queries(state: dict) -> list[str]:
        # 검색어 비교(Mobilint·d-Matrix·Scintil)에서 경쟁사 이름이 가장 많이 나온 두 형태.
        profile = state["current_startup"]
        product = (profile.get("main_products") or [""])[0]
        return [f"{profile['name']} competitors alternatives",
                f"companies competing with {profile['name']} {product} market"]

    def __call__(self, state: dict) -> dict:
        company = state["current_startup"]["name"]
        accessed_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        web = {}
        for query in self.queries(state):
            # 검색 장애는 예외로 전달: 경쟁사 없음으로 위장하지 않음.
            for hit in self.search.search(query, max_results=self.max_results):
                if hit.get("snippet") and hit.get("url"):
                    chunk = web_chunk(company, hit, accessed_at)
                    web.setdefault(chunk.chunk_id, chunk)
        hits = [SearchHit(chunk, 1.0, {}) for chunk in [*web.values(), *own_chunks(state)[:4]]]
        if not web:
            result = empty_analysis_result(missing_information=["근거 부족: 경쟁사 검색 결과 없음"])
            result["evidence"] = []
        else:
            # 1단계: 경쟁사 이름만 추출 (비교와 한 번에 요청하면 0~1곳만 뽑는 문제).
            names = self.generator.generate(
                company=company, hits=hits, fields=("main_competitors",),
                question=(f"원문에서 {company}와 같은 시장의 경쟁 기업으로 언급된 회사 이름을 최대 3개, "
                          f"각각 별도 fact로 찾아라. (기술 분야: {state.get('tech_category')})"),
            )
            names, _ = valid_competitors(names, company)
            rivals = ", ".join(fact.text for fact in names.facts) or "원문에서 확인되는 경쟁사"
            # 2단계: 확인된 경쟁사와 계획서 6개 항목별 비교·진입장벽.
            compared = self.generator.generate(
                company=company, hits=hits, fields=(*COMPARISON_AXES, "entry_barriers"),
                question=(f"{company}와 경쟁사({rivals})를 제품·성능·고객·특허·파트너십·양산 역량 항목별로 "
                          f"비교하고 진입장벽을 정리하라. (기술 분야: {state.get('tech_category')})"),
            )
            response = GroundedResponse(facts=[*names.facts, *compared.facts],
                                        missing_information=[*names.missing_information,
                                                             *compared.missing_information])
            response, _ = valid_competitors(response, company)
            result = grounded_result(
                response, hits, company=company, fields=COMPETITOR_FIELDS, claim_prefix="competitor",
                supports=("competitors",),
                required=tuple(DEFAULT_REQUIRED_ANALYSIS_DATA["competitor_analysis"]),
            )
        result["comparison_axes"] = COMPARISON_AXES
        return {"competitor_analysis": result,
                "source_evidence": register_sources(state, company, result["evidence"])}
