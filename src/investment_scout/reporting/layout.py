"""증권 리서치형 고밀도 레이아웃. 데이터가 없는 수익률·주가 그래프는 만들지 않는다."""

from __future__ import annotations

from datetime import date
from pathlib import Path
import tempfile

from pypdf import PdfReader
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import Flowable, PageBreak, Paragraph, SimpleDocTemplate, Table, TableStyle

from investment_scout.reporting.pdf_report import (
    MAX_SUMMARY_CHARS, _as_display, _escape, _register_fonts, _shorten, format_reference,
)

NAVY = colors.HexColor("#193047")
TEAL = colors.HexColor("#167D8D")
ORANGE = colors.HexColor("#DC762F")
GRAY = colors.HexColor("#566776")
PALE = colors.HexColor("#EDF2F5")
WIDTH = A4[0] - 32 * mm
HEIGHT = A4[1] - 38 * mm
CONTENT_TOP = 22 * mm
SUMMARY_MAX_HEIGHT = A4[1] / 2 - CONTENT_TOP
LABELS = {"core_technology": "핵심 기술", "advantages": "기술 장점", "limitations": "기술 한계",
          "commercialization": "상용화", "differentiation": "차별성", "market_size": "세부 시장 규모",
          "tam": "전체 잠재시장 TAM", "sam": "접근 가능시장 SAM", "growth_rate": "성장률 / 기간",
          "customer_demand": "수요 요인", "target_customers": "목표 고객", "main_competitors": "경쟁사",
          "competitive_comparison": "경쟁 비교", "entry_barriers": "진입장벽"}


class BarChart(Flowable):
    """내장 벡터 차트: 점수, 사용 출처 수 또는 명시적으로 전달된 시계열."""

    def __init__(self, rows, font, *, height=132, percent=False, caption=None):
        super().__init__()
        self.rows, self.font, self.height, self.percent = rows, font, height, percent
        self.width = WIDTH
        self.caption = caption

    def wrap(self, width, height):
        self.width = width
        return width, self.height

    def draw(self):
        c, width = self.canv, self.width
        left, right = 105, width - 65
        step = (self.height - 24) / max(1, len(self.rows))
        maximum = max([r[2] for r in self.rows] or [1]) or 1
        for n, (label, value, limit) in enumerate(self.rows):
            y = self.height - 18 - n * step
            c.setFont(self.font, 8)
            c.setFillColor(NAVY)
            c.drawString(0, y, _shorten(label, 22))
            c.setFillColor(PALE)
            c.rect(left, y - 2, right - left, 10, fill=1, stroke=0)
            if value is not None:
                fraction = value / (limit if self.percent else maximum) if limit else 0
                c.setFillColor(TEAL)
                c.rect(left, y - 2, (right - left) * max(0, min(1, fraction)), 10, fill=1, stroke=0)
                c.setFillColor(NAVY)
                c.drawRightString(width, y, f"{value:g}/{limit:g}" if self.percent else f"{value:g}")
            else:
                c.setFillColor(GRAY)
                c.drawRightString(width, y, "미제공")
        c.setFillColor(GRAY)
        c.setFont(self.font, 7)
        c.drawString(0, 1, self.caption or ("막대 길이: 항목별 배점 대비 득점률" if self.percent
                                          else "입력 자료에 포함된 값만 표시 / 중간 연도 추정 없음"))


class PageSheet(Flowable):
    """페이지 단위 측정·배치. 과도한 축소는 실패시켜 읽을 수 없는 PDF를 방지."""

    def __init__(self, blocks, number, *, summary_height=0, summary_count=0, expand_spacing=True):
        super().__init__()
        self.blocks, self.number = blocks, number
        self.summary_height = summary_height
        self.summary_count = summary_count
        self.expand_spacing = expand_spacing
        self.width, self.height = WIDTH, HEIGHT
        self.metrics = {}

    def wrap(self, width, height):
        self.width = width
        measured = [block.wrap(width, 10000)[1] for block in self.blocks]
        natural = sum(measured) + 5 * (len(measured) - 1)
        scale = min(1, self.height / max(natural, 1))
        if scale < 0.9:
            raise ValueError(f"{self.number}페이지 내용이 너무 많습니다. 입력을 요약하거나 출처를 정리하세요. "
                             "가독성을 해치는 글자 축소는 하지 않습니다.")
        self.scale = scale
        self.measured = measured
        free = max(0, self.height / scale - natural) if self.expand_spacing else 0
        flexible_gaps = max(1, len(measured) - max(1, self.summary_count))
        self.gaps = [5 if n < self.summary_count - 1 else 5 + min(18, free / flexible_gaps)
                     for n in range(len(measured) - 1)] + [0]
        self.metrics = {"page": self.number, "scale": round(scale, 3),
                        "content_height": round(natural * scale, 1),
                        "available_height": round(self.height, 1),
                        "summary_height": round(self.summary_height * scale, 1),
                        "summary_bottom": round(CONTENT_TOP + self.summary_height * scale, 1)
                            if self.summary_count else 0}
        if self.summary_height * scale > SUMMARY_MAX_HEIGHT:
            raise ValueError("Summary가 반 페이지를 초과했습니다.")
        return width, self.height

    def draw(self):
        c = self.canv
        c.saveState()
        c.scale(self.scale, self.scale)
        y = self.height / self.scale
        for block, height, gap in zip(self.blocks, self.measured, self.gaps):
            y -= height
            block.drawOn(c, 0, y)
            y -= gap
        c.restoreState()


def render_research_report(data: dict, output: Path, *, team: str, contributors: str) -> dict:
    regular, bold = _register_fonts()
    body = ParagraphStyle("ResearchBody", fontName=regular, fontSize=8.6, leading=12,
                          textColor=NAVY, wordWrap="CJK", splitLongWords=True)
    small = ParagraphStyle("ResearchSmall", parent=body, fontSize=7.5, leading=10)
    heading = ParagraphStyle("ResearchSection", parent=body, fontName=bold, fontSize=10.5, leading=15)
    title = ParagraphStyle("ResearchTitle", parent=body, fontName=bold, fontSize=21, leading=27,
                           textColor=TEAL)
    white = ParagraphStyle("ResearchWhite", parent=small, textColor=colors.white)
    refs = {r["source_id"]: n for n, r in enumerate(data["references"], 1)}

    def citation(ids):
        numbers = sorted({refs[i] for i in ids if i in refs})
        return " ".join(f"[{n}]" for n in numbers) or "[출처 미제공]"

    def p(text, style=body, limit=950):
        return Paragraph(_escape(_shorten(_as_display(text), limit)), style)

    def section(text):
        table = Table([[p(text, heading)]], colWidths=[WIDTH])
        table.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), PALE),
                                   ("LINEBELOW", (0, 0), (-1, -1), .6, TEAL),
                                   ("TOPPADDING", (0, 0), (-1, -1), 4),
                                   ("BOTTOMPADDING", (0, 0), (-1, -1), 4)]))
        return table

    def table(headers, rows, widths, *, limit=320):
        cells = [[p(h, white) for h in headers]]
        cells += [[p(v, small, limit=limit) for v in row] for row in rows]
        result = Table(cells, colWidths=[WIDTH * w for w in widths])
        result.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, 0), NAVY),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F6F8FA")]),
            ("LINEBELOW", (0, 0), (-1, -1), .25, colors.HexColor("#CED8DF")),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("LEFTPADDING", (0, 0), (-1, -1), 5), ("RIGHTPADDING", (0, 0), (-1, -1), 5),
            ("TOPPADDING", (0, 0), (-1, -1), 5), ("BOTTOMPADDING", (0, 0), (-1, -1), 5)]))
        return result

    def evidence_rows(part, limit=4):
        rows = []
        for claim in part.get("claims", [])[:limit]:
            quotes = [c.get("quote", "") for c in claim.get("citations", []) if c.get("quote")]
            rows.append([LABELS.get((claim.get("data_keys") or [""])[0], "분석 근거"),
                         claim.get("text", "근거 미제공"),
                         citation(claim.get("source_ids") or []) + (" / " + _shorten(quotes[0], 110) if quotes else "")])
        return rows or [["분석 요약", part.get("summary") or "분석 근거 미제공",
                         citation(part.get("source_ids") or [])]]

    def field_rows(part, keys):
        rows = []
        for key in keys:
            ids = [i for claim in part.get("claims", []) if key in claim.get("data_keys", [])
                   for i in claim.get("source_ids", [])]
            # 독립 handoff에는 필드별 claims가 없을 수 있으므로 섹션 출처로 표시한다.
            if not part.get("claims"):
                ids = part.get("source_ids") or []
            rows.append([LABELS.get(key, key), part.get(key) or "근거 부족 - 추가 자료 필요",
                         citation(ids) if part.get(key) else "미제공"])
        return rows

    company, market, tech, comp = data["company"], data["market"], data["technology"], data["competition"]
    scores = data["scorecard"]
    demo = " / DEMO - 합성 자료" if data.get("is_demo") else ""
    pages = []

    # 1: Summary 영역은 반 페이지 이하. 나머지는 점수 대시보드와 투자 논점.
    summary_blocks = [p("INVESTMENT RESEARCH / SEMICONDUCTOR" + demo, small),
        p(company["name"], title), p(data["title"], heading),
        table(["투자 의견", "리스크 반영 점수", "평가 후보"],
              [[data["decision"], f"{data['total_score']:g} / 100", len(data.get("evaluated_companies") or [])]], [.34, .33, .33]),
        section("1. Executive Summary"), p(data["summary"], limit=MAX_SUMMARY_CHARS),
        p(data["decision_reason"], small, limit=440)]
    summary_height = sum(b.wrap(WIDTH, 10000)[1] for b in summary_blocks) + 5 * (len(summary_blocks) - 1)
    if summary_height > SUMMARY_MAX_HEIGHT:
        # 700자 상한에 더해 물리 높이까지 검사한다.
        raise ValueError("Summary 블록이 반 페이지보다 큽니다. 요약문 또는 제목을 줄이세요.")
    page1 = [*summary_blocks, section("투자 매력도 - 분야별 득점률"),
             BarChart([(r["label"], r["score"], r["max_score"]) for r in scores], regular, percent=True),
             section("핵심 투자 논점과 확인 사항"),
             table(["검토 축", "역할 3의 평가 근거", "출처"],
                   [[r["label"], r["reason"], citation(r["source_ids"])] for r in scores], [.2, .66, .14], limit=145)]
    candidates = data.get("evaluated_companies") or []
    if candidates:
        page1 += [table(["평가 후보 (상위 최대 4개)", "입력 점수", "입력 판정"],
            [[r.get("startup") or r.get("name") or "기업명 미제공",
              r.get("total", r.get("score", "미제공")),
              r.get("decision") or ("RECOMMENDED" if r.get("qualified") else "HOLD")]
             for r in candidates[:4]], [.5, .2, .3])]
    pages.append(PageSheet(page1, 1, summary_height=summary_height, summary_count=len(summary_blocks)))

    # 2: 출처가 없는 시장 규모·연도별 추이를 임의로 계산하지 않는다.
    page2 = [section("2. 시장 / 고객 / 수요 구조"),
             p("시장 전체 규모와 기업의 접근 가능시장은 다릅니다. 아래 수치는 전달된 분석 범위 내에서만 해석합니다.", small),
             table(["시장 지표", "확인된 내용", "근거"], field_rows(market,
                    ("market_size", "growth_rate", "tam", "sam", "customer_demand", "target_customers")), [.23, .63, .14])]
    series = market.get("series")
    if series:
        page2 += [section(series.get("title") or "출처 기반 시장 규모 추이"),
                  BarChart([(str(point["year"]), point["value"], max(p["value"] for p in series["points"]))
                            for point in series["points"]], regular, height=145),
                  p(f"단위: {series['unit']} / {citation(series['source_ids'])}. 보고서 원문 전망은 기업 매출 전망이 아닙니다.", small)]
    else:
        page2 += [section("시장 자료의 확인 범위"),
                  table(["확인 항목", "현재 제공된 정보", "투자 검토 시 유의점"], [
                    ["시장 범위", market.get("market_size") or "시장 규모 미제공", "전체 산업 규모를 기업 SAM으로 해석하지 않음"],
                    ["수요와 제품 연결", market.get("customer_demand") or "수요 근거 미제공", "고객의 비용·전력·성능 요구와 제품의 적합성 확인"],
                    ["시계열 데이터", "연도별 구조화 수치 미제공", "CAGR로 중간 연도를 보간하거나 매출을 추정하지 않음"],
                  ], [.23, .42, .35], limit=180)]
    page2 += [section("시장 분석의 핵심 근거"), table(["항목", "분석 주장", "인용 / Reference"],
                  evidence_rows(market, 4), [.17, .52, .31], limit=230),
              section("시장성 실사 - 추가 확인 과제"),
              table(["확인할 자료", "확인이 필요한 이유"], [
                ["세부 시장 정의 / 지역 / 예측 기간", "서로 다른 조사기관·범위의 수치를 합치지 않기 위해 확인"],
                ["SAM / 유료 도입 / 초기 고객 반응", "시장 성장과 해당 기업의 실제 매출 기회를 분리하기 위해 확인"],
              ], [.4, .6])]
    pages.append(PageSheet(page2, 2))

    # 3: 기업 정보, 기술 검토, 원점수·배점 및 항목별 판단 근거.
    profile = [["설립 / 투자 단계", f"{company.get('founded_year') or '미확인'} / {company.get('funding_stage') or '미확인'}"],
               ["제품 / 기술 분야", f"{_as_display(company.get('main_products') or '미확인')} / {company.get('tech_category') or tech.get('category') or '미확인'}"],
               ["홈페이지", company.get("website") or "미제공"]]
    page3 = [section("3. 기업 / 기술 / 투자 점수"),
             table(["기업 프로필", company["name"] + " " + citation(company.get("source_ids") or [])], profile, [.24, .76]),
             table(["기술 검토", "확인된 내용", "근거"], field_rows(tech,
                   ("core_technology", "advantages", "limitations", "commercialization")), [.2, .66, .14], limit=220),
             section("투자 평가표 - 배점과 근거"),
             table(["항목", "점수 / 배점", "판단 근거", "출처"],
                   [[r["label"], "미제공" if r["score"] is None else f"{r['score']:g}/{r['max_score']:g}",
                     r["reason"], citation(r["source_ids"])] for r in scores], [.17, .14, .56, .13], limit=200),
             p(f"항목 합계 {data['score_subtotal']:g} + 리스크 감점 {data['risk_penalty']:g} = 최종 {data['total_score']:g}점. "
               "미제공은 0점과 구분해 표시하며, 역할 4는 점수를 변경하지 않습니다.", small)]
    items = data.get("score_items") or []
    if items:
        page3 += [section("체크리스트 상세 - 주요 미흡 항목"),
                  table(["평가 질문", "점수", "추가 확인이 필요한 근거"],
                        [[i.get("question") or i.get("key"), f"{i.get('score', 0)}/{i.get('max_points', '-')}",
                          i.get("rationale") or "근거 미제공"] for i in sorted(items,
                          key=lambda i: (i.get("score", 0) / max(i.get("max_points", 1), 1)))[:5]],
                        [.38, .12, .5], limit=120)]
    else:
        page3 += [section("기술 근거 검토"), table(["항목", "확인된 기술 주장", "출처"], evidence_rows(tech, 2), [.18, .64, .18], limit=200)]
    pages.append(PageSheet(page3, 3))

    # 4: 투자자가 다음 의사결정에 쓸 수 있는 경쟁·성장·리스크·실사 표.
    growth_rows = [[item.get("description", "근거 미제공") if isinstance(item, dict) else item,
                    citation(item.get("source_ids", [])) if isinstance(item, dict) else "미제공"]
                   for item in data["growth_outlook"][:5]] or [["성장 전망 미제공", "미제공"]]
    risk_rows = [[("치명 / " if r["fatal"] else "참고 / ") + r["type"], r["description"],
                  r["mitigation"], f"{r['penalty']:g} / {citation(r['source_ids'])}"] for r in data["risks"]]
    page4 = [section("4. 성장 / 경쟁 / 리스크와 투자 실행 조건"),
             table(["성장 근거", "Reference"], growth_rows, [.8, .2], limit=220),
             section("경쟁 구도와 진입장벽"),
             table(["비교 축", "분석 결과", "출처"], field_rows(comp,
                   ("main_competitors", "competitive_comparison", "entry_barriers")), [.2, .65, .15], limit=230),
             section("리스크 매트릭스 - 감점과 대응"),
             table(["구분", "위험 내용", "대응 / 실사", "감점 / 출처"],
                   risk_rows or [["미보고", "전달된 리스크 없음", "위험이 없다는 뜻이 아님. 독립 실사 필요", "0"]],
                   [.15, .36, .33, .16], limit=180),
             section("투자 전 확인해야 할 실행 조건"),
             table(["실사 영역", "추가 확인 요청 (분석 사실·확정 일정 아님)"], [
                ["기술 / 양산", "동일 조건의 성능·전력 검증, 수율·양산 파트너 및 공급 가능 일정 확인"],
                ["고객 / 매출", "PoC와 유료 계약을 구분하고 고객 집중도·반복 매출·구매 의향 확인"],
                ["투자조건 / 법률", "밸류에이션, 지분 희석, 자금 소진, IP 권리·분쟁 및 계약 조건 확인"],
             ], [.24, .76]),
             section("투자 판단의 점수 구조"),
             BarChart([("항목 합계", data["score_subtotal"], 100),
                       ("리스크 반영 최종", data["total_score"], 100)], regular, height=95,
                       caption="막대 길이: 100점 척도 / 역할 3의 확정 점수와 감점만 반영"),
             p(f"확정 감점: {data['risk_penalty']:g}점 / 전달된 치명 리스크 {sum(r['fatal'] for r in data['risks'])}건. "
               "음수 총점은 막대를 0에서 멈추되 수치는 그대로 표시합니다. 추가 실사 과제는 감점하지 않습니다.", small),
             section("최종 투자 의견"), p(f"{data['decision']} / {data['decision_reason']}", limit=550),
             p("본 보고서는 공개 자료 기반의 실습용 투자 검토입니다. 목표주가·ROI·기업 매출 전망은 근거 없이 산출하지 않습니다.", small)]
    pages.append(PageSheet(page4, 4))

    # 5: 실제 사용한 출처 전부와 본문-근거 연결. URL은 자르지 않는다.
    page5 = [section("5. Reference / 근거 추적"),
             p("본문·점수·리스크·도표에서 실제 연결한 source_ids만 수록합니다. 번호는 본문 인용과 일치합니다.", small)]
    reference_paragraphs = [Paragraph(_escape(format_reference(r, n)), small)
                            for n, r in enumerate(data["references"], 1)]
    if len(reference_paragraphs) > 8:
        split = (len(reference_paragraphs) + 1) // 2
        rows = [[reference_paragraphs[i], reference_paragraphs[i + split] if i + split < len(reference_paragraphs) else ""]
                for i in range(split)]
        rt = Table(rows, colWidths=[WIDTH / 2, WIDTH / 2])
        rt.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"),
                               ("BOTTOMPADDING", (0, 0), (-1, -1), 8)]))
        page5.append(rt)
    else:
        page5 += reference_paragraphs or [p("실제 사용된 출처가 없습니다.")]
    # Reference는 길이/밀도 목표를 두지 않는다. 적으면 여백을 그대로 두고,
    # 많으면 URL·서지정보를 자르지 않고 두 단으로 배치한다.
    pages.append(PageSheet(page5, 5, expand_spacing=False))

    def furniture(canvas, doc):
        canvas.saveState()
        canvas.setFont(regular, 7.5)
        canvas.setFillColor(GRAY)
        canvas.drawString(16 * mm, A4[1] - 13 * mm, _shorten(company["name"] + demo, 65))
        canvas.drawRightString(A4[0] - 16 * mm, A4[1] - 13 * mm, date.today().isoformat())
        canvas.setStrokeColor(TEAL)
        canvas.line(16 * mm, A4[1] - 17 * mm, A4[0] - 16 * mm, A4[1] - 17 * mm)
        canvas.drawString(16 * mm, 10 * mm, "AI-RAG Investment Research / 공개 자료 기반 실습")
        canvas.drawRightString(A4[0] - 16 * mm, 10 * mm, f"{doc.page} / 5")
        canvas.restoreState()

    # 모든 페이지를 미리 측정하여 overflow가 기존 결과물을 훼손하지 않도록 한다.
    for page in pages:
        page.wrap(WIDTH, HEIGHT)
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(prefix="report-", suffix=".pdf", dir=output.parent, delete=False) as stream:
        staging = Path(stream.name)
    try:
        document = SimpleDocTemplate(str(staging), pagesize=A4, leftMargin=16 * mm, rightMargin=16 * mm,
            topMargin=22 * mm, bottomMargin=16 * mm, title=data["title"], author=team)
        # 기본 프레임의 내부 여백(6pt)을 보정해 실제 페이지 폭·높이를 맞춘다.
        document.leftMargin -= 6
        document.rightMargin -= 6
        document.topMargin -= 6
        document.bottomMargin -= 6
        story = []
        for n, page in enumerate(pages):
            if n:
                story.append(PageBreak())
            story.append(page)
        document.build(story, onFirstPage=furniture, onLaterPages=furniture)
        if len(PdfReader(staging).pages) != 5:
            raise ValueError("PDF는 정확히 5페이지여야 합니다.")
        staging.replace(output)
    finally:
        staging.unlink(missing_ok=True)
    return {"path": str(output.resolve()), "pages": 5, "references": len(data["references"]),
            "layout": [page.metrics for page in pages], "summary_max_height": SUMMARY_MAX_HEIGHT}
