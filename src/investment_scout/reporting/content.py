"""보고서 내용 추출: 최종 State(에이전트 실행 결과)에서 5페이지 보고서의 문장·표·그래프 데이터를 만듦.

LLM을 다시 호출하지 않고, 에이전트들이 남긴 검증된 주장·점수·이유·리스크·순위·출처만 사용한다.
본문 문장 끝의 [n]은 5페이지 Reference 번호와 연결된다.
"""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlsplit

from investment_scout.state import DECISION_RECOMMENDED

MARKET_SEGMENT_LABELS = {
    "AI_CHIP": "엣지 AI 칩", "DATACENTER_AI": "데이터센터 AI 가속기", "CXL_MEMORY": "CXL 메모리",
    "SILICON_PHOTONICS": "실리콘 포토닉스", "IN_MEMORY_COMPUTE": "인메모리 컴퓨팅", "CHIPLET": "칩렛·다이 간 인터커넥트",
}
CATEGORY_LABELS = {
    "NPU": "NPU", "AI_ACCELERATOR": "AI 가속기", "GPU": "GPU", "HBM": "HBM", "DRAM": "DRAM",
    "EDA_PROCESS_AI": "EDA·공정 AI", "IN_MEMORY_COMPUTE": "인메모리 연산", "CXL": "CXL",
    "PHOTONICS": "광반도체", "OTHER": "칩렛·인터커넥트 등",
}
FIELD_LABELS = {
    "core_technology": "핵심 기술", "differentiation": "차별성", "advantages": "장점", "limitations": "한계",
    "commercialization": "상용화", "market_size": "시장 규모", "growth_rate": "성장률",
    "customer_demand": "수요·고객", "serviceable_market": "SAM", "entry_barriers": "진입장벽",
    "compare_product": "제품", "compare_performance": "성능", "compare_customers": "고객",
    "compare_patents": "특허", "compare_partnerships": "파트너십", "compare_production": "양산 역량",
}
DOCUMENT_TYPE_LABELS = {"official_website": "공식 홈페이지", "paper": "논문", "press_release": "보도자료·기사",
                        "product": "제품 문서", "patent": "특허", "web_search": "웹 검색 결과"}
STAGE_LABELS = {"SEED": "Seed", "PRE_SERIES_A": "Pre-A", "SERIES_A": "Series A", "SERIES_B": "Series B",
                "SERIES_C": "Series C"}
TERMINATION_LABELS = {
    "RECOMMENDED_FOUND": "전체 평가 후 기준 통과 기업 중 1순위 추천",
    "ALL_HOLD": "전체 평가 결과 기준 통과 기업 없음(전원 보류)",
    "LIMIT_REACHED": "평가 한도 도달(일부 후보만 평가)",
    "NO_ELIGIBLE_CANDIDATES": "적격 후보 없음",
    "ZERO_LIMIT": "평가 한도 0",
}


def shorten(value: Any, limit: int) -> str:
    text = " ".join(str(value or "").split())
    return text if len(text) <= limit else text[: max(0, limit - 1)].rstrip() + "…"


class References:
    """출처 번호: 같은 문서(URL·제목)의 여러 청크는 하나의 번호로 묶고 인용 페이지를 모음."""

    def __init__(self, sources: dict[str, dict]):
        self.sources = sources
        self.entries: list[dict] = []
        self._by_doc: dict[tuple, dict] = {}

    def number(self, source_id: str) -> int | None:
        source = self.sources.get(source_id)
        if not source or not source.get("url"):
            return None
        key = (source.get("url"), source.get("title"))
        entry = self._by_doc.get(key)
        if entry is None:
            entry = {"number": len(self.entries) + 1, "source": source, "pages": set()}
            self.entries.append(entry)
            self._by_doc[key] = entry
        if source.get("page") and source.get("page_basis") != "web_search":
            entry["pages"].add(int(source["page"]))
        return entry["number"]

    def cite(self, source_ids: list[str]) -> str:
        numbers = sorted({n for n in (self.number(s) for s in source_ids) if n})
        return "".join(f"[{n}]" for n in numbers)


def collect_sources(state: dict) -> dict[str, dict]:
    sources = {}
    for rows in (state.get("source_evidence") or {}).values():
        for row in rows or []:
            if isinstance(row, dict) and row.get("source_id"):
                sources.setdefault(row["source_id"], row)
    return sources


def claims_by_key(analysis: dict, refs: References) -> dict[str, list[str]]:
    """분석 결과의 검증된 주장을 항목별로 모으고 출처 번호를 붙임."""
    grouped: dict[str, list[str]] = {}
    for claim in (analysis or {}).get("claims", []):
        key = (claim.get("data_keys") or ["기타"])[0]
        grouped.setdefault(key, []).append(f"{claim['text'].strip()} {refs.cite(claim.get('source_ids', []))}".strip())
    return grouped


def market_points(analysis: dict) -> list[tuple[int, float]]:
    """그래프용 시장 규모 (연도, 십억 달러): 인용 원문의 'USD x billion in/by 20xx' 표현에서만 추출."""
    pattern = re.compile(r"(?:USD|US\$|\$)\s?([\d.,]+)\s*(billion|million|bn)\b[^.;]{0,60}?"
                         r"\b(?:in|by|of)\s*(?:the end of\s*)?(20\d\d)", re.I)
    points = {}
    for claim in (analysis or {}).get("claims", []):
        if "market_size" not in claim.get("data_keys", []):
            continue
        for citation in claim.get("citations", []):
            for amount, unit, year in pattern.findall(citation.get("quote", "")):
                value = float(amount.replace(",", ""))
                points.setdefault(int(year), value / 1000 if unit.lower() == "million" else value)
    return sorted(points.items())


def cagr_of(analysis: dict) -> str | None:
    for claim in (analysis or {}).get("claims", []):
        for text in [claim.get("text", ""), *(c.get("quote", "") for c in claim.get("citations", []))]:
            match = re.search(r"(?:CAGR|compound annual growth rate|연평균)[^%]{0,40}?(\d+(?:\.\d+)?)\s?%", text, re.I)
            if match:
                return f"{match.group(1)}%"
    return None


def money(value: float) -> str:
    """십억 달러 → 한국어 금액 (억 달러 / 조 달러)."""
    eok = value * 10
    return f"{eok / 10000:.1f}조 달러" if eok >= 10000 else f"{eok:,.0f}억 달러"


def market_brief(analysis: dict) -> str:
    """인용 원문 수치로 만든 한국어 시장 요약: '2025년 18억 달러 → 2033년 37억 달러, CAGR 9.57%'."""
    points = market_points(analysis)
    parts = []
    if points:
        ends = [points[0], points[-1]] if len(points) > 1 else points[:1]
        parts.append(" → ".join(f"{year}년 {money(value)}" for year, value in ends))
    growth = cagr_of(analysis)
    if growth:
        parts.append(f"연평균 성장률(CAGR) {growth}")
    return ", ".join(parts) or "시장 수치 근거 부족"


def reference_line(entry: dict) -> tuple[str, str]:
    """가이드 REFERENCE 형식: (구분, 문자열)."""
    source = entry["source"]
    publisher = source.get("publisher") or source.get("company") or "작성자 미상"
    title = source.get("title") or "제목 미상"
    url = source.get("url", "")
    host = (urlsplit(url).hostname or "").removeprefix("www.")
    published = source.get("published_at")
    accessed = (source.get("accessed_at") or "")[:10]
    pages = f" (p.{', '.join(str(p) for p in sorted(entry['pages']))})" if entry["pages"] else ""
    if source.get("document_type") == "paper":
        arxiv = re.search(r"arxiv\.org/(?:pdf|abs)/(\d{2})(\d{2})\.", url)
        year = published[:4] if published else (f"20{arxiv.group(1)}" if arxiv else "연도 미상")
        venue = "arXiv preprint" if arxiv else host
        return "학술 논문", f"{publisher}({year}). {title}. {venue}{pages}. {url}"
    if source.get("company") in MARKET_SEGMENT_LABELS:
        year = published[:4] if published else "연도 미상"
        return "기관 보고서", f"{publisher}({year}). {title}{pages}. {url}"
    date = published or (f"발행일 미상, {accessed} 접속" if accessed else "발행일 미상")
    return "웹페이지", f"{publisher}({date}). {title}. {host}{pages}, {url}"


def build_report_content(state: dict) -> dict:
    """최종 State → 보고서 내용. 추천 기업이 없으면 최고점 후보를 '보류' 판단으로 기술."""
    history = state.get("evaluation_history") or []
    if not history:
        raise ValueError("평가 이력이 없어 보고서를 만들 수 없습니다. pipeline을 먼저 실행하세요.")
    ranking = state.get("final_ranking") or []
    chosen = state.get("recommended_startup") or (ranking[0]["startup"] if ranking else history[0]["startup"])
    record = next(r for r in history if r["startup"] == chosen)
    recommended = bool(state.get("recommended_startup"))
    details = record.get("evaluation_details") or {}
    profile = record.get("profile") or {}
    refs = References(collect_sources(state))

    # 본문 순서대로 출처 번호 부여: 시장 → 기술 → 경쟁 → 채점 근거
    market = claims_by_key(record.get("market_analysis"), refs)
    tech = claims_by_key(record.get("tech_summary"), refs)
    competition = claims_by_key(record.get("competitor_analysis"), refs)
    claim_sources = {}
    for field in ("tech_summary", "market_analysis", "competitor_analysis"):
        for claim in (record.get(field) or {}).get("claims", []):
            claim_sources[f"{field}:{claim['claim_id']}"] = claim.get("source_ids", [])
    items = []
    for item in details.get("items", []):
        ids = [sid for e in item.get("evidence_ids", [])
               for sid in (claim_sources.get(e) or ([e.split(":", 1)[1]] if e.startswith("profile:") else []))]
        items.append({**item, "cite": refs.cite(ids)})
    risks = details.get("risks", [])

    # 평가 과정 통계 (모든 에이전트 결과)
    rows = {r["startup"]: r for r in history}
    table = []
    for row in ranking or [{"startup": r["startup"], "total": (r.get("evaluation_scores") or {}).get("total", 0),
                            "qualified": r["investment_decision"] == DECISION_RECOMMENDED, "rank": i + 1}
                           for i, r in enumerate(history)]:
        r = rows.get(row["startup"], {})
        passed = (r.get("evaluation_details") or {}).get("decision") == DECISION_RECOMMENDED
        status = ("추천" if row["startup"] == state.get("recommended_startup") else
                  "기준 통과" if row["qualified"] else
                  "보류(핵심 정보 부족)" if passed and r.get("missing_core_information") else "보류(70점 미만)")
        table.append({"rank": row["rank"], "startup": row["startup"], "total": float(row["total"]),
                      "category": category_label(r.get("tech_category")), "status": status,
                      "region": "국내" if (r.get("profile") or {}).get("region") == "KR" else "해외"})
    qualified = sum(1 for t in table if t["status"] in ("추천", "기준 통과"))
    forced = sum(1 for t in table if t["status"] == "보류(핵심 정보 부족)")
    counts = (state.get("scout_result") or {}).get("counts", {})
    segments = [s for s in (record.get("market_analysis") or {}).get("segments", [])]
    segment_mix: dict[str, int] = {}
    for r in history:
        segs = (r.get("market_analysis") or {}).get("segments") or []
        label = MARKET_SEGMENT_LABELS.get(segs[0], segs[0]) if len(segs) < 6 and segs else "분류 미확정"
        segment_mix[label] = segment_mix.get(label, 0) + 1

    groups = [{"key": key, "label": label, "score": details.get("groups", {}).get(key, 0), "max": weight}
              for key, label, weight in (("market", "시장성", 25), ("technology", "제품/기술력", 30),
                                         ("competition", "경쟁 우위", 20), ("growth", "성장가능성", 15),
                                         ("deal", "투자조건", 10))]
    total = float(details.get("total", (record.get("evaluation_scores") or {}).get("total", 0)))
    content = {
        "title": f"{state.get('target_domain', 'Semiconductor')} 스타트업 투자 평가 보고서",
        "company": profile.get("name", chosen),
        "recommended": recommended,
        "decision_label": "투자 추천" if recommended else "보류",
        "total": total, "threshold": details.get("threshold", 70),
        "risk_penalty": details.get("risk_penalty", 0),
        "penalized_risk_types": details.get("penalized_risk_types", []),
        "sample_totals": details.get("sample_totals", []),
        "profile": {
            "기업": profile.get("name", chosen), "국가": profile.get("country", "-"),
            "설립": profile.get("founded_year", "-"),
            "투자 단계": STAGE_LABELS.get(profile.get("funding_stage"), profile.get("funding_stage") or "-"),
            "주요 제품": ", ".join(profile.get("main_products") or []) or "-",
            "기술 분류": category_label(record.get("tech_category")),
            "홈페이지": profile.get("website", "-"),
        },
        "process": {
            "candidates": len(state.get("candidate_startups") or history), "evaluated": len(history),
            "kr": counts.get("KR", {}).get("secured"), "overseas": counts.get("OVERSEAS", {}).get("secured"),
            "qualified": qualified, "forced": forced,
            "termination": TERMINATION_LABELS.get(state.get("termination_reason"), state.get("termination_reason") or "-"),
        },
        "ranking": table, "groups": groups, "items": items, "risks": risks,
        "segments": [MARKET_SEGMENT_LABELS.get(s, s) for s in segments], "segment_mix": segment_mix,
        "market": market, "market_points": market_points(record.get("market_analysis")),
        "market_missing": [m for m in (record.get("market_analysis") or {}).get("missing_information", [])
                           if "serviceable" in m],
        "tech": tech, "competition": competition,
        "competitors": (record.get("competitor_analysis") or {}).get("data", {}).get("main_competitors", []),
        "hold_reason": record.get("hold_reason"),
    }
    # 다른 평가 기업이 속한 세부 시장 비교 (시장성 에이전트 결과)
    peer_markets = {}
    for r in history:
        segs = (r.get("market_analysis") or {}).get("segments") or []
        if len(segs) >= 6 or not segs:
            continue
        label = MARKET_SEGMENT_LABELS.get(segs[0], segs[0])
        brief = market_brief(r.get("market_analysis"))
        if brief != "시장 수치 근거 부족":
            peer_markets.setdefault(label, {"segment": label, "brief": brief, "companies": []})
        if label in peer_markets:
            peer_markets[label]["companies"].append(r["startup"])
    content["peer_markets"] = list(peer_markets.values())
    content["market_brief"] = market_brief(record.get("market_analysis"))
    # 기준 통과 기업 분야별 점수 비교 (투자 판단 에이전트 결과)
    peers = []
    for row in table:
        r = rows.get(row["startup"], {})
        g = (r.get("evaluation_details") or {}).get("groups") or {}
        if row["status"] in ("추천", "기준 통과") or row["startup"] == chosen:
            peers.append({"startup": row["startup"], "status": row["status"], "total": row["total"],
                          **{key: g.get(key, 0) for key in ("market", "technology", "competition", "growth", "deal")}})
    content["peers"] = peers[:8]
    # 자료 출처 요약 (전체 실행에서 에이전트가 사용한 출처)
    summary: dict[str, dict] = {}
    for source in collect_sources(state).values():
        if source.get("company") in MARKET_SEGMENT_LABELS:
            kind = "시장 보고서 (시장성 RAG)"
        elif source.get("page_basis") == "web_search":
            kind = "웹 검색 결과 (경쟁사 비교)"
        elif source.get("document_type"):
            kind = "기술 문서 (기술 RAG)"
        else:
            kind = "후보 검증 출처 (스타트업 탐색)"
        item = summary.setdefault(kind, {"kind": kind, "documents": set(), "chunks": 0, "types": set()})
        item["documents"].add(source.get("url"))
        item["chunks"] += 1
        if source.get("document_type"):
            item["types"].add(source["document_type"])
    content["source_summary"] = [{"kind": v["kind"], "documents": len(v["documents"]), "chunks": v["chunks"],
                                  "types": ", ".join(sorted(DOCUMENT_TYPE_LABELS.get(t, t) for t in v["types"])) or "투자·설립 확인 자료"}
                                 for v in summary.values()]
    content["references"] = [(*reference_line(entry), entry["number"]) for entry in refs.entries]
    content["summary"] = summary_lines(content)
    content["narrative"] = narrative(content)
    return content


def category_label(value: str | None) -> str:
    labels = [CATEGORY_LABELS.get(v.strip(), v.strip()) for v in (value or "").split("/") if v.strip()]
    return " / ".join(labels) or "분류 근거 부족"


def summary_lines(c: dict) -> list[tuple[str, str]]:
    """1페이지 요약: 결론·과정·근거·리스크·시장·경쟁을 한 줄씩 (담당자가 이것만 보고 판단)."""
    p = c["process"]
    items = sorted(c["items"], key=lambda i: (-(i["score"] / i["max_points"]), -i["max_points"]))
    strong = [f"{short_question(i)} {i['score']}/{i['max_points']}" for i in items[:3] if i["score"]]
    weak = [f"{short_question(i)} {i['score']}/{i['max_points']}" for i in reversed(items)
            if i["score"] < i["max_points"] / 2][:3]
    fatal = [r for r in c["risks"] if r.get("fatal")]
    if c["recommended"]:
        verdict = (f"{c['company']} 투자 추천 — 총점 {c['total']:.0f}/100 (추천 기준 {c['threshold']}점), "
                   f"기준 통과 {p['qualified']}개사 중 1위")
    else:
        verdict = f"전원 보류 — 70점 이상 기업 없음. 최고점 후보 {c['company']} {c['total']:.0f}점"
    lines = [
        ("결론", verdict),
        ("평가 과정", f"후보 {p['evaluated']}개사(국내 {p['kr']}·해외 {p['overseas']}) 평가 → 기준 통과 "
                   f"{p['qualified']}개사, 핵심 정보 부족 보류 {p['forced']}개사 → {p['termination']}"),
        ("강점", " · ".join(strong) or "근거 있는 강점 없음"),
        ("약점", " · ".join(weak) or "절반 미만 항목 없음"),
        ("리스크", (f"치명 리스크 {', '.join(c['penalized_risk_types'])} ({c['risk_penalty']}점)" if fatal
                  else f"치명 리스크 없음 (참고 리스크 {len(c['risks'])}건)")),
        ("시장", f"{' / '.join(c['segments'][:2])}: {c['market_brief']}"),
        ("경쟁", f"주요 경쟁사 {', '.join(c['competitors']) or '확인 불가'}"),
    ]
    return [(label, shorten(re.sub(r"\[\d+\]", "", text), 150)) for label, text in lines]


def narrative(c: dict) -> str:
    """1페이지 서술 문단: 어떤 과정을 거쳐 어떤 결론이 나왔는지 한 문단으로."""
    p = c["process"]
    rivals = ", ".join(c["competitors"]) or "확인된 경쟁사 없음"
    tech_claims = sum(len(v) for v in c["tech"].values())
    conclusion = (f"{c['company']}를 최종 투자 추천 기업으로 선정했습니다" if c["recommended"]
                  else f"추천 기준을 넘은 기업이 없어 전원 보류로 결론 내렸으며, 최고점 후보는 {c['company']}입니다")
    return (f"스타트업 탐색 에이전트가 국내 {p['kr']}개·해외 {p['overseas']}개 후보의 비상장·투자 단계·Exit 여부를 확인한 뒤, "
            f"각 후보마다 기술 요약·분류 에이전트가 기술 문서에서 근거를 찾고(선정 기업 {tech_claims}건, 분류: {c['profile']['기술 분류']}), "
            f"시장성 평가 에이전트가 세부 시장 보고서를({'/'.join(c['segments'][:2]) or '해당 시장'}: {c['market_brief']}), "
            f"경쟁사 비교 에이전트가 웹 검색으로 경쟁사({rivals})를 분석했습니다. 투자 판단 에이전트는 설계서의 13개 체크리스트를 "
            f"3회 채점해 항목별 중앙값을 쓰고({c['sample_totals']}), 치명 리스크를 유형별로 감점했습니다. 그 결과 {p['evaluated']}개사 중 "
            f"{p['qualified']}개사가 70점 기준을 통과했고 {p['forced']}개사는 핵심 정보 부족으로 보류되었으며, "
            f"총점 {c['total']:.0f}점으로 {conclusion}.")


def short_question(item: dict) -> str:
    return {
        "market_size": "시장 규모", "willingness_to_pay": "지불 의사", "early_traction": "초기 반응",
        "solves_problem": "문제 해결", "core_technology": "독창적 기술", "revenue_model": "수익 모델",
        "differentiation": "차별성", "entry_barrier": "진입장벽", "market_leadership": "시장 선점",
        "scalability": "확장성", "long_term": "10년 경쟁력", "team": "팀", "deal_terms": "투자 조건",
    }.get(item["key"], item["question"])
