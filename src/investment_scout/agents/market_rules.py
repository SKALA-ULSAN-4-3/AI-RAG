"""시장 근거의 최소 형식 검사. 의미적 정확성이나 조사 품질을 보증하지 않는다."""

import re

YEAR = re.compile(r"\b20\d{2}\b")
AMOUNT = re.compile(r"\d[\d,.]*\s*(?:billion|million|trillion|억|조|만)|(?:USD|US\$|\$|달러|원|EUR)", re.I)
PERCENT = re.compile(r"\d+(?:\.\d+)?\s*%")
GROWTH = re.compile(r"CAGR|compound annual|연평균|성장률", re.I)
DEMAND = re.compile(r"demand|customer|end.user|adoption|need|cost|power|latency|수요|고객|도입|비용|전력|지연", re.I)
TRACTION = re.compile(r"customer|고객|deploy|도입|공급|supply|ship|출하|매출|revenue|contract|계약|수주|sample|샘플", re.I)


def market_fact_supported(field: str, text: str) -> bool:
    """인용문 자체에 수치·연도·수요 근거가 없으면 채택하지 않는다."""
    if field in {"market_size", "tam", "sam"}:
        return bool(YEAR.search(text) and AMOUNT.search(text) and re.search(r"\d", text))
    if field == "growth_rate":
        return bool(YEAR.search(text) and PERCENT.search(text) and GROWTH.search(text))
    if field == "customer_demand":
        return bool(DEMAND.search(text))
    return False
