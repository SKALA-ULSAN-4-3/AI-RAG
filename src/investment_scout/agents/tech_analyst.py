"""기술 요약 노드: 검색 근거와 인용을 포함한 AnalysisResult 반환."""

from investment_scout.rag.generation import OpenAIGenerator, TECH_FIELDS, answer_question


class TechAnalyst:
    def __init__(self, index, *, generator=None, min_score=0.35):
        self.index = index
        self.generator = generator or OpenAIGenerator()
        self.min_score = min_score

    @staticmethod
    def question(company: str) -> str:
        return f"{company} 핵심 기술, 차별성, 장점, 한계와 상용화 상태는?"

    def __call__(self, state: dict) -> dict:
        company = state["current_startup"]["name"]
        result = answer_question(
            self.index, self.generator, company=company,
            question=self.question(company),
            fields=TECH_FIELDS, min_score=self.min_score, top_k=12,
        )
        return {"tech_summary": result}
