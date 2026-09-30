"""기술 분류 노드: 기술 요약에서 인용 검증을 통과한 분류만 전달."""

import re

from investment_scout.contracts import DEFAULT_REQUIRED_ANALYSIS_DATA
from investment_scout.rag.generation import OpenAIGenerator, analysis_status, answer_question

# 라벨-근거 일치 검사: 라벨을 뒷받침하는 표현이 근거 문장·인용문에 있어야 함.
# (LLM이 본문에 'PIM'이라 쓰고 라벨은 PHOTONICS로 붙이는 오류 방지)
LABEL_TERMS = {
    "NPU": r"\bNPU|neural processing",
    "HBM": r"\bHBM",
    "DRAM": r"DRAM",
    "GPU": r"\bGPU",
    "EDA_PROCESS_AI": r"\bEDA\b|design automation|설계 ?자동화|공정",
    "IN_MEMORY_COMPUTE": r"in-memory|processing-in-memory|compute-in-memory|\bPIM\b|\bCIM\b|인메모리|"
                         r"compute (?:directly )?into memory|compute inside SRAM|memory and compute are physically",
    "CXL": r"\bCXL",
    "PHOTONICS": r"photon|optical|광(?:학|반도체|연결|연산|통신|인터커넥트|소자)|laser|레이저|microLED|light",
    "AI_ACCELERATOR": r"accelerat|가속|\bLPU|inference|추론|AI ?(?:chip|칩|processor|프로세서|SoC)|processor|프로세서",
}
SPECIFIC_LABELS = [label for label in LABEL_TERMS if label != "AI_ACCELERATOR"]


def checked_label(label: str, text: str) -> str | None:
    """근거와 맞는 라벨: 맞으면 그대로, 아니면 근거에 나타난 특화 라벨 하나로 교정, 없으면 AI 가속기 또는 제외."""
    if label == "OTHER" or re.search(LABEL_TERMS.get(label, "$^"), text, re.I):
        return label
    found = [name for name in SPECIFIC_LABELS if re.search(LABEL_TERMS[name], text, re.I)]
    if len(found) == 1:
        return found[0]
    if not found and re.search(LABEL_TERMS["AI_ACCELERATOR"], text, re.I):
        return "AI_ACCELERATOR"
    return None


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
        category_claims, labels, corrections = [], [], []
        for claim in category["claims"]:
            evidence = claim["text"] + " " + " ".join(c["quote"] for c in claim.get("citations", []))
            label = checked_label(claim.get("category"), evidence)
            if label is None:
                corrections.append(f"분류 제외: {claim.get('category')} (근거 문장에 해당 표현 없음)")
                continue
            if label != claim.get("category"):
                corrections.append(f"분류 교정: {claim.get('category')} → {label} (근거 문장 기준)")
            category_claims.append({**claim, "category": label,
                                    "claim_id": f"tech_category_{len(category_claims) + 1:03d}"})
            labels.append(label)
        category["data"] = {"categories": list(dict.fromkeys(labels))} if labels else {}
        category["missing_information"] = [*category["missing_information"], *corrections]
        summary["data"] = {**summary["data"], **category["data"]}
        summary["claims"] = [*summary["claims"], *category_claims]
        known = {item["source_id"]: item for item in summary["evidence"]}
        known.update({item["source_id"]: item for item in category["evidence"]})
        summary["evidence"] = list(known.values())
        # 분류 LLM이 해당하지 않는 라벨마다 적는 '근거 부족: NPU' 같은 항목은 누락 정보가 아니므로 제외.
        category_gaps = [note for note in category["missing_information"]
                         if "categories" in note or "검색 결과 없음" in note or note.startswith("분류 ")]
        summary["missing_information"] = list(dict.fromkeys([*summary["missing_information"], *category_gaps]))
        summary["status"] = analysis_status(summary, DEFAULT_REQUIRED_ANALYSIS_DATA["tech_summary"])
        return {"tech_summary": summary, **tech_classifier({"tech_summary": summary})}
