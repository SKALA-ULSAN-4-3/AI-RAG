"""5페이지 투자 보고서 PDF: 설계서 목차(Summary → 시장 → 기업 → 성장·리스크 → Reference).

내용은 content.build_report_content()가 최종 State에서 추출한 값만 사용한다.
각 페이지는 한 쪽 틀(KeepInFrame)에 넣어 정확히 5페이지를 보장하고, 1페이지 요약은 반 페이지 틀로 제한한다.
"""

from __future__ import annotations

from datetime import date
import html
import os
from pathlib import Path
from typing import Any

from investment_scout.reporting.content import FIELD_LABELS, shorten

MAX_PDF_PAGES = 5
TEAM = "울산 캠퍼스 4반 3조"
MEMBERS = "신한수, 안영준, 정하윤, 손수경, 손경락"
NAVY, TEAL, GREEN, RED, GREY = "#102A43", "#0B7285", "#2B8A3E", "#C92A2A", "#829AB1"


def _escape(value: Any) -> str:
    return html.escape(str(value if value is not None else ""), quote=False)


def _font_path() -> Path:
    configured = os.getenv("REPORT_FONT_PATH", "").strip()
    candidates = [
        Path(configured) if configured else None,
        Path("/System/Library/Fonts/Supplemental/AppleGothic.ttf"),
        Path("/System/Library/Fonts/Supplemental/NotoSansGothic-Regular.ttf"),
        Path("/usr/share/fonts/truetype/nanum/NanumGothic.ttf"),
        Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"),
        Path("C:/Windows/Fonts/malgun.ttf"),
    ]
    for candidate in candidates:
        if candidate and candidate.is_file():
            return candidate
    raise RuntimeError("한국어 PDF 글꼴을 찾지 못했습니다. REPORT_FONT_PATH에 TTF/TTC 경로를 지정하세요.")


def _register_font() -> str:
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont

    name = "InvestmentReportKorean"
    if name not in pdfmetrics.getRegisteredFontNames():
        pdfmetrics.registerFont(TTFont(name, str(_font_path())))
    return name


def render_pdf_report(content: dict, output: Path, *, team: str = TEAM, members: str = MEMBERS) -> dict:
    try:
        from pypdf import PdfReader
        from reportlab.graphics.charts.barcharts import HorizontalBarChart, VerticalBarChart
        from reportlab.graphics.shapes import Drawing, Line, String
        from reportlab.lib import colors
        from reportlab.lib.enums import TA_CENTER
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.styles import ParagraphStyle
        from reportlab.lib.units import mm
        from reportlab.platypus import (KeepInFrame, PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table,
                                        TableStyle)
    except ImportError as exc:
        raise RuntimeError("PDF 생성 의존성을 설치하세요: uv sync --extra report") from exc

    font = _register_font()
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    width, height = A4
    margin = 16 * mm
    frame_w, frame_h = width - 2 * margin, height - 36 * mm
    c = colors.HexColor

    def style(name, size, color=NAVY, leading=None, **kw):
        return ParagraphStyle(name, fontName=font, fontSize=size, leading=leading or size * 1.45,
                              textColor=c(color), wordWrap="CJK", **kw)

    title_s = style("t", 19, NAVY, 25)
    h1 = style("h1", 14, TEAL, 19, spaceAfter=5)
    h2 = style("h2", 10.5, NAVY, 14, spaceBefore=6, spaceAfter=3)
    body = style("b", 8.6, "#243B53", 13)
    small = style("s", 7.4, "#334E68", 10.4)
    cell = style("c", 7.6, "#243B53", 10.6)
    cell_head = style("ch", 7.8, "#FFFFFF", 10.6, alignment=TA_CENTER)
    big = style("big", 15, NAVY, 19, alignment=TA_CENTER)
    label_s = style("lab", 7.4, "#486581", 10, alignment=TA_CENTER)

    def P(text, st=body):
        return Paragraph(_escape(text), st)

    def grid(rows, widths, header=True, head_color=TEAL, zebra=True):
        data = [[P(v, cell_head if header and r == 0 else cell) if isinstance(v, str) else v for v in row]
                for r, row in enumerate(rows)]
        table = Table(data, colWidths=widths, repeatRows=1 if header else 0)
        commands = [("BOX", (0, 0), (-1, -1), 0.5, c("#9FB3C8")),
                    ("INNERGRID", (0, 0), (-1, -1), 0.25, c("#D9E2EC")),
                    ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                    ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 3)]
        if header:
            commands.append(("BACKGROUND", (0, 0), (-1, 0), c(head_color)))
        if zebra:
            commands += [("BACKGROUND", (0, r), (-1, r), c("#F0F4F8"))
                         for r in range(2 if header else 1, len(rows), 2)]
        table.setStyle(TableStyle(commands))
        return table

    def bullets(lines, st=body, empty="에이전트 결과에 해당 근거 없음", limit=260):
        return [Paragraph("• " + _escape(shorten(line, limit)), st) for line in (lines or [empty])]

    def hbar(names, values, fills, maximum, threshold=None, w=frame_w, h=80 * mm):
        drawing = Drawing(w, h)
        chart = HorizontalBarChart()
        chart.x, chart.y, chart.width, chart.height = 70, 10, w - 110, h - 22
        chart.data = [list(reversed(values))]
        chart.categoryAxis.categoryNames = list(reversed(names))
        chart.categoryAxis.labels.fontName = font
        chart.categoryAxis.labels.fontSize = 7
        chart.valueAxis.valueMin, chart.valueAxis.valueMax = 0, maximum
        chart.valueAxis.valueStep = maximum / 5
        chart.valueAxis.labels.fontName = font
        chart.valueAxis.labels.fontSize = 6.5
        chart.barLabels.fontName = font
        chart.barLabels.fontSize = 6.5
        chart.barLabelFormat = "%.0f"
        chart.barLabels.nudge = 7
        chart.bars.strokeColor = None
        for index, fill in enumerate(reversed(fills)):
            chart.bars[(0, index)].fillColor = c(fill)
        drawing.add(chart)
        if threshold is not None:
            x = chart.x + chart.width * threshold / maximum
            drawing.add(Line(x, chart.y, x, chart.y + chart.height, strokeColor=c(RED), strokeDashArray=[3, 2]))
            drawing.add(String(x + 2, chart.y + chart.height + 3, f"추천 기준 {threshold}점",
                               fontName=font, fontSize=6.5, fillColor=c(RED)))
        return drawing

    def vbar(names, values, w, h, unit=""):
        drawing = Drawing(w, h)
        chart = VerticalBarChart()
        chart.x, chart.y, chart.width, chart.height = 32, 20, w - 45, h - 34
        chart.data = [values]
        chart.categoryAxis.categoryNames = names
        chart.categoryAxis.labels.fontName = font
        chart.categoryAxis.labels.fontSize = 7
        chart.valueAxis.valueMin = 0
        chart.valueAxis.labels.fontName = font
        chart.valueAxis.labels.fontSize = 6.5
        chart.barLabels.fontName = font
        chart.barLabels.fontSize = 6.5
        chart.barLabelFormat = "%.1f" + unit
        chart.barLabels.nudge = 6
        chart.bars[0].fillColor = c(TEAL)
        chart.bars.strokeColor = None
        drawing.add(chart)
        return drawing

    def page(flowables):
        return [KeepInFrame(frame_w, frame_h, flowables, mode="shrink"), PageBreak()]

    story = []
    k = content
    p = k["process"]

    # ---- Page 1: Summary (반 페이지) + 평가 과정·순위
    verdict_color = GREEN if k["recommended"] else RED
    cards = Table([[P("투자 판단", label_s), P("대상 기업", label_s), P("최종 점수", label_s), P("평가 기업", label_s)],
                   [Paragraph(f'<font color="{verdict_color}">{_escape(k["decision_label"])}</font>', big),
                    P(k["company"], big), P(f"{k['total']:.0f} / 100", big), P(f"{p['evaluated']}개사", big)]],
                  colWidths=[frame_w / 4] * 4)
    cards.setStyle(TableStyle([("BOX", (0, 0), (-1, -1), 0.8, c("#9FB3C8")),
                               ("INNERGRID", (0, 0), (-1, -1), 0.3, c("#D9E2EC")),
                               ("BACKGROUND", (0, 0), (-1, 0), c("#F0F4F8")),
                               ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                               ("TOPPADDING", (0, 1), (-1, 1), 6), ("BOTTOMPADDING", (0, 1), (-1, 1), 6)]))
    summary_rows = [[P(label, cell_head), P(text, cell)] for label, text in k["summary"]]
    summary_table = Table(summary_rows, colWidths=[24 * mm, frame_w - 24 * mm])
    summary_table.setStyle(TableStyle([("BACKGROUND", (0, 0), (0, -1), c(NAVY)),
                                       ("BOX", (0, 0), (-1, -1), 0.5, c("#9FB3C8")),
                                       ("INNERGRID", (0, 0), (-1, -1), 0.25, c("#D9E2EC")),
                                       ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                                       ("TOPPADDING", (0, 0), (-1, -1), 3.5), ("BOTTOMPADDING", (0, 0), (-1, -1), 3.5)]))
    half = [P(k["title"], title_s),
            P(f"{team}({members})", body),
            P(f"작성일 {date.today().isoformat()} · 평가 대상: AI 반도체 스타트업 {p['evaluated']}개사", small),
            Spacer(1, 5), cards, Spacer(1, 6), P("1. Summary", h1), summary_table, Spacer(1, 6),
            P(k["narrative"], body)]
    # 1페이지는 요약만: 반 페이지 틀로 제한하고 나머지는 비움.
    story += [KeepInFrame(frame_w, frame_h / 2, half, mode="shrink"), PageBreak()]

    # ---- Page 2: 선정 시장
    m = k["market"]
    metric_rows = [["지표", "내용 (출처)"], ["세부 시장", " / ".join(k["segments"]) or "-"],
                   ["핵심 요약", k["market_brief"]]]
    for key in ("market_size", "growth_rate", "serviceable_market"):
        for text in m.get(key, []):
            metric_rows.append([FIELD_LABELS[key], shorten(text, 230)])
    if not m.get("serviceable_market"):
        metric_rows.append(["SAM", "자료 없음 — 확보한 보고서에 기업 제품이 공략하는 하위 시장 규모가 명시되지 않아 전체 시장 규모만 제시"])
    page2 = [P(f"2. 선정 시장: {' / '.join(k['segments'][:2]) or '-'}", h1),
             P("시장 규모와 성장률", h2), grid(metric_rows, [24 * mm, frame_w - 24 * mm])]
    points = k["market_points"]
    chart_cell = (vbar([str(y) for y, _ in points], [v for _, v in points], 78 * mm, 55 * mm)
                  if len(points) >= 2 else P("인용 원문에서 연도별 시장 규모를 추출하지 못해 그래프를 생략합니다.", small))
    mix = sorted(k["segment_mix"].items(), key=lambda kv: -kv[1])
    mix_table = grid([["세부 시장", "기업 수"]] + [[name, f"{count}개사"] for name, count in mix],
                     [52 * mm, 20 * mm])
    side = Table([[P("시장 규모 추이 (십억 달러, 인용 원문 기준)", h2), P("평가 기업들의 세부 시장 분포", h2)],
                  [chart_cell, mix_table]], colWidths=[frame_w * 0.52, frame_w * 0.48])
    side.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP")]))
    page2 += [Spacer(1, 4), side, P("대상 고객과 수요처", h2)]
    page2 += bullets(m.get("customer_demand"))
    if k["peer_markets"]:
        peer_rows = [["세부 시장", "규모·성장률 (인용 원문 수치)", "해당 평가 기업"]]
        peer_rows += [[row["segment"], row["brief"], shorten(", ".join(row["companies"]), 60)]
                      for row in k["peer_markets"]]
        page2 += [P("관련 세부 시장 비교 (시장성 평가 에이전트 결과)", h2),
                  grid(peer_rows, [34 * mm, 80 * mm, frame_w - 114 * mm])]
    page2 += [P("시장 해석", h2),
              P(f"{k['company']}의 기술 분류({k['profile']['기술 분류']})와 주요 제품({shorten(k['profile']['주요 제품'], 90)})을 "
                f"기준으로 {' / '.join(k['segments'][:2]) or '해당'} 시장을 선정했습니다. 제시한 수치는 조사기관이 추정한 "
                "세부 시장 전체 규모이며 기업 매출이 아닙니다. 조사기관마다 시장 정의가 달라 수치를 서로 합산·비교하지 않았습니다.", body)]
    story += page(page2)

    # ---- Page 3: 선정 기업
    profile_rows = [[P(key, cell_head), P(shorten(value, 110), cell)] for key, value in k["profile"].items()]
    profile_table = Table(profile_rows, colWidths=[20 * mm, 70 * mm])
    profile_table.setStyle(TableStyle([("BACKGROUND", (0, 0), (0, -1), c(NAVY)),
                                       ("BOX", (0, 0), (-1, -1), 0.5, c("#9FB3C8")),
                                       ("INNERGRID", (0, 0), (-1, -1), 0.25, c("#D9E2EC")),
                                       ("VALIGN", (0, 0), (-1, -1), "MIDDLE")]))
    groups = k["groups"]
    group_chart = hbar([g["label"] for g in groups], [100 * g["score"] / g["max"] for g in groups],
                       [TEAL] * len(groups), 100, None, w=frame_w - 92 * mm, h=48 * mm)
    top_row = Table([[profile_table, [P("분야별 달성률 (%)", h2), group_chart]]], colWidths=[92 * mm, frame_w - 92 * mm])
    top_row.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP")]))
    score_rows = [["분야", "체크리스트 항목", "점수", "판단 이유 (근거)"]]
    for item in k["items"]:
        score_rows.append([item["group_label"].split(" ")[0], item["question"], f"{item['score']}/{item['max_points']}",
                           shorten(f"{item['rationale']} {item['cite']}", 120)])
    subtotal = sum(g["score"] for g in groups)
    score_rows.append(["합계", f"항목 합계 {subtotal} + 리스크 {k['risk_penalty']}", f"{k['total']:.0f}/100",
                       f"추천 기준 {k['threshold']}점 · 3회 채점 {k['sample_totals']}"])
    page3 = [P(f"3. 선정 기업: {k['company']}", h1), top_row, P("에이전트 평가 점수표 (설계서 Score Table·체크리스트)", h2),
             grid(score_rows, [17 * mm, 58 * mm, 13 * mm, frame_w - 88 * mm])]
    team_item = next((i for i in k["items"] if i["key"] == "team"), None)
    page3 += [P("팀", h2),
              P(f"{k['profile']['설립']}년 설립, {k['profile']['투자 단계']} 단계. "
                + (team_item["rationale"] if team_item and team_item["score"] else
                   "창업자·경영진 경력을 확인할 수 있는 자료를 확보하지 못해 팀 항목은 0점으로 평가했습니다."), body),
              P("기술", h2)]
    page3 += bullets([*k["tech"].get("core_technology", []), *k["tech"].get("advantages", []),
                      *k["tech"].get("commercialization", [])][:4], limit=200)
    idea = next((i for i in k["items"] if i["key"] == "solves_problem"), None)
    page3 += [P("아이디어 (해결하는 문제와 차별성)", h2)]
    page3 += bullets([*(k["tech"].get("differentiation", [])),
                      *([f"{idea['rationale']} {idea['cite']}"] if idea and idea["score"] else [])][:3], limit=200)
    story += page(page3)

    # ---- Page 4: 성장가능성과 리스크
    growth_items = [i for i in k["items"] if i["group"] == "growth"]
    page4 = [P("4. 성장가능성과 리스크", h1), P("성장가능성", h2)]
    page4 += bullets([f"{i['question']} {i['score']}/{i['max_points']} — {i['rationale']} {i['cite']}" for i in growth_items]
                     + m.get("growth_rate", [])[:1])
    comp_rows = [["비교 항목", "내용 (출처)"]]
    for key in ("compare_product", "compare_performance", "compare_customers", "compare_patents",
                "compare_partnerships", "compare_production", "entry_barriers", "competitive_comparison"):
        for text in k["competition"].get(key, [])[:2]:
            comp_rows.append([FIELD_LABELS.get(key, "비교"), shorten(text, 210)])
    page4 += [P(f"경쟁 구도 — 주요 경쟁사: {', '.join(k['competitors']) or '확인 불가'}", h2),
              grid(comp_rows if len(comp_rows) > 1 else comp_rows + [["-", "경쟁사 비교 근거 없음"]],
                   [24 * mm, frame_w - 24 * mm])]
    risk_rows = [["유형", "구분", "리스크 내용", "감점"]]
    for risk in k["risks"]:
        penalized = risk.get("fatal") and risk.get("type") in k["penalized_risk_types"]
        risk_rows.append([risk.get("type", "-"), "치명" if risk.get("fatal") else "참고",
                          shorten(risk.get("description"), 200), "-10" if penalized else "0"])
    if len(risk_rows) == 1:
        risk_rows.append(["-", "-", "근거로 확인된 리스크 없음", "0"])
    page4 += [P(f"리스크 (기술·운영·법률 유형별 치명 리스크 −10점, 합계 {k['risk_penalty']}점)", h2),
              grid(risk_rows, [16 * mm, 14 * mm, frame_w - 44 * mm, 14 * mm], head_color="#9C2C2C")]
    weak = [f"{i['question']} {i['score']}/{i['max_points']}: {i['rationale']}" for i in k["items"]
            if i["score"] < i["max_points"] / 2]
    if len(k["peers"]) > 1:
        peer_rows = [["기업", "판단", "시장성/25", "기술력/30", "경쟁우위/20", "성장성/15", "투자조건/10", "총점"]]
        peer_rows += [[row["startup"], row["status"], *(str(row[key]) for key in
                       ("market", "technology", "competition", "growth", "deal")), f"{row['total']:.0f}"]
                      for row in k["peers"]]
        page4 += [P("기준 통과 기업 비교 (투자 판단 에이전트 점수)", h2),
                  grid(peer_rows, [30 * mm, 22 * mm] + [(frame_w - 66 * mm) / 5] * 5 + [14 * mm])]
    top = k["ranking"][:10]
    fills = [GREEN if r["status"] == "추천" else TEAL if r["status"] == "기준 통과" else GREY for r in top]
    page4 += [P("상위 10개사 총점 (녹색: 최종 추천, 청록: 기준 통과, 회색: 보류)", h2),
              hbar([r["startup"] for r in top], [r["total"] for r in top], fills, 100, k["threshold"], h=58 * mm)]
    page4 += [P("종합 의견과 유의사항", h2)]
    page4 += bullets([(f"{k['company']}는 총점 {k['total']:.0f}점으로 추천 기준({k['threshold']}점)을 넘어 최종 추천되었습니다."
                       if k["recommended"] else f"추천 기준을 넘은 기업이 없어 보류합니다. {k['hold_reason'] or ''}"),
                      *[f"보완 필요: {w}" for w in weak[:3]],
                      "점수는 에이전트가 수집한 공개 자료 기준이며, LLM 채점 변동을 줄이기 위해 3회 채점 중앙값을 사용했습니다. "
                      "투자 집행 전 재무·밸류에이션·경영진 실사가 필요합니다."], limit=240)
    story += page(page4)

    # ---- Page 5: Reference
    page5 = [P("5. Reference", h1),
             P("보고서 본문의 [번호]와 연결되며, 에이전트가 실제로 인용한 자료만 포함합니다. 형식: 가이드 REFERENCE 표기법.", small),
             Spacer(1, 2)]
    if k["source_summary"]:
        summary_rows = [["자료 구분 (사용 에이전트)", "문서 수", "인용 단위 수", "문서 유형"]]
        summary_rows += [[row["kind"], str(row["documents"]), str(row["chunks"]), row["types"]]
                         for row in k["source_summary"]]
        page5 += [P("사용된 자료의 출처 요약 (전체 평가 과정)", h2),
                  grid(summary_rows, [52 * mm, 18 * mm, 22 * mm, frame_w - 92 * mm]),
                  P(f"선정 기업({k['company']}) 보고서에 인용된 자료", h2)]
    for kind in ("기관 보고서", "학술 논문", "웹페이지"):
        lines = [(number, text) for group, text, number in k["references"] if group == kind]
        if lines:
            page5.append(P(f"{kind} ({len(lines)}건)", h2))
            page5 += [Paragraph(f"[{number}] {_escape(text)}", small) for number, text in lines]
    if not k["references"]:
        page5.append(P("인용된 자료가 없습니다.", body))
    story += [KeepInFrame(frame_w, frame_h, page5, mode="shrink")]

    def decorate(canvas, doc):
        canvas.saveState()
        canvas.setStrokeColor(c("#D9E2EC"))
        canvas.line(margin, height - 14 * mm, width - margin, height - 14 * mm)
        canvas.setFont(font, 7)
        canvas.setFillColor(c("#627D98"))
        canvas.drawString(margin, height - 12 * mm, k["title"])
        canvas.drawString(margin, 9 * mm, team)
        canvas.drawRightString(width - margin, 9 * mm, f"{doc.page} / {MAX_PDF_PAGES}")
        canvas.restoreState()

    document = SimpleDocTemplate(str(output), pagesize=A4, leftMargin=margin, rightMargin=margin,
                                 topMargin=18 * mm, bottomMargin=16 * mm, title=k["title"], author=team)
    document.build(story, onFirstPage=decorate, onLaterPages=decorate)
    pages = len(PdfReader(str(output)).pages)
    if pages != MAX_PDF_PAGES:
        output.unlink(missing_ok=True)
        raise RuntimeError(f"보고서는 정확히 {MAX_PDF_PAGES}페이지여야 합니다. 생성 결과: {pages}페이지")
    return {"path": str(output.resolve()), "pages": pages, "references": len(k["references"])}


def build_markdown_report(content: dict) -> str:
    """State final_report용 Markdown 요약."""
    lines = [f"# {content['title']}", ""]
    lines += [f"- **{label}**: {text}" for label, text in content["summary"]]
    lines += ["", "| 분야 | 점수 |", "| --- | ---: |"]
    lines += [f"| {g['label']} | {g['score']}/{g['max']} |" for g in content["groups"]]
    lines += [f"| 리스크 감점 | {content['risk_penalty']} |", f"| **총점** | **{content['total']:.0f}/100** |"]
    return "\n".join(lines)
