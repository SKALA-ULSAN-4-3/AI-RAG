"""기술 분류 노드: 기술 요약에서 인용 검증을 통과한 분류만 전달."""

from investment_scout.contracts import DEFAULT_REQUIRED_ANALYSIS_DATA
from investment_scout.rag.generation import OpenAIGenerator, analysis_status, answer_question


def tech_classifier(state: dict) -> dict:
    summary = state.get("tech_summary") or {}
    categories = (summary.get("data") or {}).get("categories", [])
    claims = summary.get("claims") or []
    supported = {c.get("category") for c in claims
                 if "categories" in c.get("data_keys", []) and c.get("citations")}
    confirmed = [category for category in categories if category in supported]
    return {"tech_category": " / ".join(confirmed) if confirmed else "근거 부족"}


class TechClassifier:
    """제품 분야 검색: 요약과 분리된 질문으로 분류 근거를 확보."""

    def __init__(self, index, *, generator=None, min_score=0.30):
        self.index = index
        self.generator = generator or OpenAIGenerator()
        self.min_score = min_score

    @staticmethod
    def question(company: str) -> str:
        # 기업명 제외: 기술 요약 질문과 같은 이유.
        return ("실제 반도체 제품의 기술 분야는 NPU, AI Accelerator, HBM, DRAM, GPU, EDA/공정 AI, "
                "PIM/인메모리 연산, CXL, 광반도체, 칩렛 인터커넥트 중 무엇인가? "
                "(product chip type, processor, memory, processing-in-memory, interconnect, photonics)")

    def __call__(self, state: dict) -> dict:
        company = state["current_startup"]["name"]
        category = answer_question(
            self.index, self.generator, company=company,
            question=self.question(company), fields=("categories",),
            min_score=self.min_score, top_k=8,
        )
        summary = {**state["tech_summary"]}
        summary["data"] = {**summary["data"], **category["data"]}
        category_claims = [
            {**claim, "claim_id": f"tech_category_{number:03d}"}
            for number, claim in enumerate(category["claims"], start=1)
        ]
        summary["claims"] = [*summary["claims"], *category_claims]
        known = {item["source_id"]: item for item in summary["evidence"]}
        known.update({item["source_id"]: item for item in category["evidence"]})
        summary["evidence"] = list(known.values())
        # 분류 LLM이 해당하지 않는 라벨마다 적는 '근거 부족: NPU' 같은 항목은 누락 정보가 아니므로 제외.
        category_gaps = [note for note in category["missing_information"]
                         if "categories" in note or "검색 결과 없음" in note]
        summary["missing_information"] = list(dict.fromkeys([*summary["missing_information"], *category_gaps]))
        summary["status"] = analysis_status(summary, DEFAULT_REQUIRED_ANALYSIS_DATA["tech_summary"])
        return {"tech_summary": summary, **tech_classifier({"tech_summary": summary})}
