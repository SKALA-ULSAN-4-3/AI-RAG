"""기술 요약 노드: 검색 근거와 인용을 포함한 AnalysisResult 반환."""

from investment_scout.rag.generation import OpenAIGenerator, TECH_FIELDS, answer_question


class TechAnalyst:
    def __init__(self, index, *, generator=None, min_score=0.30):
        self.index = index
        self.generator = generator or OpenAIGenerator()
        self.min_score = min_score

    @staticmethod
    def question(company: str) -> str:
        # 기업명 제외: 검색은 이미 기업별로 필터링되며, 이름이 들어가면 저자 소개·참고문헌이 상위로 올라옴.
        # 영문 병기: 영문 스펙 문서가 대부분이므로 Jina 우선 가중치 적용.
        return ("핵심 반도체 기술과 제품 구조, 성능과 전력 효율, 차별성, 장점, 한계, 상용화 상태 "
                "(core technology, chip architecture, performance, efficiency, advantages, "
                "limitations, commercialization)")

    def __call__(self, state: dict) -> dict:
        company = state["current_startup"]["name"]
        result = answer_question(
            self.index, self.generator, company=company,
            question=self.question(company),
            fields=TECH_FIELDS, min_score=self.min_score, top_k=12,
        )
        return {"tech_summary": result}
