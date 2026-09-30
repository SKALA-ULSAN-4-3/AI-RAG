"""검증 가능한 5페이지 투자 보고서 PDF와 Markdown 요약 생성."""

from __future__ import annotations

from datetime import date
import html
import os
from pathlib import Path
from typing import Any, Iterable

MAX_SUMMARY_CHARS = 700
MAX_PDF_PAGES = 5


def _shorten(value: Any, limit: int, fallback: str = "근거 미제공") -> str:
    text = " ".join(str(value or "").split()) or fallback
    return text if len(text) <= limit else text[: max(0, limit - 1)].rstrip() + "…"


def _escape(value: Any) -> str:
    return html.escape(str(value or ""), quote=False)


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
    raise RuntimeError(
        "한국어 PDF 글꼴을 찾지 못했습니다. REPORT_FONT_PATH에 TTF/TTC 경로를 지정하세요."
    )


def _register_fonts() -> tuple[str, str]:
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont

    regular = "InvestmentReportKorean"
    bold = "InvestmentReportKoreanBold"
    if regular not in pdfmetrics.getRegisteredFontNames():
        path = _font_path()
        pdfmetrics.registerFont(TTFont(regular, str(path)))
        # 별도 볼드 파일이 없어도 같은 글꼴을 등록해 한글 누락을 방지합니다.
        pdfmetrics.registerFont(TTFont(bold, str(path)))
    return regular, bold


def _paragraphs(values: Iterable[Any], style, *, limit: int, empty: str = "근거 미제공") -> list:
    from reportlab.platypus import Paragraph, Spacer

    rows = list(values)
    if not rows:
        rows = [empty]
    result = []
    for value in rows:
        if isinstance(value, dict):
            value = value.get("description") or value.get("summary") or value.get("text") or value
        result.extend([Paragraph(f"• {_escape(_shorten(value, limit))}", style), Spacer(1, 4)])
    return result


def _section_summary(section: dict, *, keys: tuple[str, ...]) -> list[str]:
    values = []
    for key in keys:
        value = section.get(key)
        if value not in (None, "", [], {}):
            values.append(f"{key}: {_shorten(value, 260)}")
    if not values and section.get("summary"):
        values.append(_shorten(section["summary"], 700))
    return values


def format_reference(source: dict, number: int) -> str:
    """가이드용 참고문헌 형식: 발행주체, 제목, 날짜, URL, 페이지/접근일."""
    author = source.get("publisher") or source.get("author") or source.get("company") or "발행 주체 미상"
    title = source.get("title") or "제목 미상"
    published = source.get("published_at") or "발행일 미상"
    accessed = source.get("accessed_at") or "확인일 미상"
    page = source.get("page")
    page_text = f", p.{page}" if page else ""
    return f"[{number}] {author}. {title}. {published}. {source.get('url')}{page_text}. (확인: {accessed})"


def build_markdown_report(data: dict) -> str:
    """LangGraph final_report에 저장할 간결한 Markdown 버전."""
    lines = [
        f"# {data['title']}",
        "",
        f"- 대상 기업: {data['company']['name']}",
        f"- 투자 판단: {data['decision']}",
        f"- 최종 점수: {data['total_score']:.1f}/100",
        f"- 판단 근거: {data['decision_reason']}",
        "",
        "## Summary",
        _shorten(data["summary"], MAX_SUMMARY_CHARS),
        "",
        "## 점수표",
        "| 항목 | 점수 | 근거 |",
        "| --- | ---: | --- |",
    ]
    for row in data["scorecard"]:
        score = "미제공" if row["score"] is None else f"{row['score']:.1f}/{row['max_score']:.0f}"
        lines.append(f"| {row['label']} | {score} | {row['reason']} |")
    lines += ["", "## 리스크"]
    if data["risks"]:
        for risk in data["risks"]:
            lines.append(f"- {risk['description']} ({risk['penalty']:.0f}점)")
    else:
        lines.append("- 보고된 리스크 없음")
    lines += ["", "## Reference"]
    if data["references"]:
        lines.extend(f"- {format_reference(source, number)}" for number, source in enumerate(data["references"], 1))
    else:
        lines.append("- 보고서에 사용된 출처 없음")
    return "\n".join(lines)


def render_pdf_report(
    data: dict,
    output: Path,
    *,
    team: str = "울산 캠퍼스 4반 3조",
    contributors: str = "안영준, 정하윤, 손수경, 손경락",
) -> dict:
    """정규화된 역할 3 결과를 정확히 5개 섹션의 PDF로 만듭니다."""
    try:
        from pypdf import PdfReader
        from reportlab.lib import colors
        from reportlab.lib.enums import TA_CENTER, TA_LEFT
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
        from reportlab.lib.units import mm
        from reportlab.platypus import (
            PageBreak,
            Paragraph,
            SimpleDocTemplate,
            Spacer,
            Table,
            TableStyle,
        )
    except ImportError as exc:
        raise RuntimeError("PDF 생성 의존성을 설치하세요: uv sync --extra report") from exc

    regular, bold = _register_fonts()
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    styles = getSampleStyleSheet()
    title = ParagraphStyle(
        "ReportTitle", parent=styles["Title"], fontName=bold, fontSize=22,
        leading=28, textColor=colors.HexColor("#102A43"), alignment=TA_LEFT,
        spaceAfter=12,
    )
    h1 = ParagraphStyle(
        "H1K", parent=styles["Heading1"], fontName=bold, fontSize=17,
        leading=22, textColor=colors.HexColor("#0B7285"), spaceAfter=10,
    )
    h2 = ParagraphStyle(
        "H2K", parent=styles["Heading2"], fontName=bold, fontSize=11,
        leading=15, textColor=colors.HexColor("#334E68"), spaceBefore=7, spaceAfter=5,
    )
    body = ParagraphStyle(
        "BodyK", parent=styles["BodyText"], fontName=regular, fontSize=9.2,
        leading=14, textColor=colors.HexColor("#243B53"), wordWrap="CJK",
    )
    small = ParagraphStyle(
        "SmallK", parent=body, fontSize=7.3, leading=10, textColor=colors.HexColor("#486581"),
    )
    table_header = ParagraphStyle(
        "TableHeaderK", parent=small, fontName=bold, textColor=colors.white, alignment=TA_CENTER,
    )
    table_body = ParagraphStyle("TableBodyK", parent=small, fontSize=7.4, leading=9.6)
    center = ParagraphStyle("CenterK", parent=body, alignment=TA_CENTER)

    def header_footer(canvas, doc):
        canvas.saveState()
        width, height = A4
        canvas.setStrokeColor(colors.HexColor("#D9E2EC"))
        canvas.line(18 * mm, height - 15 * mm, width - 18 * mm, height - 15 * mm)
        canvas.setFont(regular, 7)
        canvas.setFillColor(colors.HexColor("#627D98"))
        canvas.drawString(18 * mm, 9 * mm, _shorten(team, 45))
        canvas.drawRightString(width - 18 * mm, 9 * mm, f"{doc.page} / 5")
        canvas.restoreState()

    story = []
    # Page 1 - Summary. 본문을 700자로 제한해 반 페이지를 넘기지 않습니다.
    demo = " [DEMO]" if data.get("is_demo") else ""
    story += [
        Spacer(1, 8 * mm),
        Paragraph(_escape(data["title"] + demo), title),
        Paragraph(f"대상 기업: <b>{_escape(data['company']['name'])}</b>", body),
        Spacer(1, 8),
    ]
    decision_color = colors.HexColor("#2B8A3E") if data["decision"] == "RECOMMENDED" else colors.HexColor("#C92A2A")
    decision_table = Table([
        [Paragraph("투자 판단", table_header), Paragraph("최종 점수", table_header), Paragraph("평가 기업", table_header)],
        [Paragraph(_escape(data["decision"]), center), Paragraph(f"{data['total_score']:.1f} / 100", center),
         Paragraph(str(len(data.get("evaluated_companies") or [])), center)],
    ], colWidths=[55 * mm, 55 * mm, 55 * mm])
    decision_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#334E68")),
        ("BOX", (0, 0), (-1, -1), 0.7, colors.HexColor("#9FB3C8")),
        ("INNERGRID", (0, 0), (-1, -1), 0.3, colors.HexColor("#BCCCDC")),
        ("TEXTCOLOR", (0, 1), (0, 1), decision_color),
        ("FONTNAME", (0, 1), (0, 1), bold),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 7), ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
    ]))
    story += [decision_table, Spacer(1, 10), Paragraph("Executive Summary", h1),
              Paragraph(_escape(_shorten(data["summary"], MAX_SUMMARY_CHARS)), body),
              Spacer(1, 10), Paragraph("판단 근거", h2),
              Paragraph(_escape(_shorten(data["decision_reason"], 500)), body),
              Spacer(1, 15), Paragraph(f"작성: {_escape(team)} / {_escape(contributors)} / {date.today().isoformat()}", small),
              PageBreak()]

    # Page 2 - Market
    market = data["market"]
    story += [Paragraph("2. 시장 및 고객 수요", h1)]
    story += _paragraphs(_section_summary(
        market, keys=("tam", "sam", "serviceable_market", "market_size", "growth_rate", "customer_demand",
                      "target_customers", "summary")
    ), body, limit=700)
    story += [Spacer(1, 8), Paragraph("시장 해석", h2),
              Paragraph(_escape(_shorten(market.get("interpretation") or market.get("summary"), 1200)), body),
              Spacer(1, 8), Paragraph("시장 근거 ID", h2),
              Paragraph(_escape(", ".join(market.get("source_ids") or []) or "근거 ID 미제공"), small),
              PageBreak()]

    # Page 3 - Company / scorecard
    company = data["company"]
    technology = data["technology"]
    story += [Paragraph("3. 기업 및 투자 점수", h1)]
    profile_rows = [
        ["기업", company.get("name", "-")], ["설립연도", company.get("founded_year", "-")],
        ["주요 제품", _as_display(company.get("main_products"))], ["투자 단계", company.get("funding_stage", "-")],
        ["기술 분류", company.get("tech_category") or technology.get("category") or "-"],
    ]
    profile = Table([[Paragraph(_escape(a), table_header), Paragraph(_escape(_shorten(b, 240)), table_body)] for a, b in profile_rows],
                    colWidths=[35 * mm, 130 * mm])
    profile.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#334E68")),
        ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#BCCCDC")),
        ("INNERGRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#D9E2EC")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING", (0, 0), (-1, -1), 4), ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))
    story += [profile, Spacer(1, 8), Paragraph("핵심 기술", h2),
              Paragraph(_escape(_shorten(technology.get("summary") or technology, 650)), body),
              Spacer(1, 8), Paragraph("평가 점수표", h2)]
    score_rows = [[Paragraph("항목", table_header), Paragraph("점수", table_header), Paragraph("평가 근거", table_header)]]
    for item in data["scorecard"]:
        score = "미제공" if item["score"] is None else f"{item['score']:.1f}/{item['max_score']:.0f}"
        score_rows.append([Paragraph(_escape(item["label"]), table_body), Paragraph(score, table_body),
                           Paragraph(_escape(_shorten(item["reason"], 170)), table_body)])
    score_rows.append([Paragraph("리스크 반영 최종", table_header),
                       Paragraph(f"{data['total_score']:.1f}/100", table_header),
                       Paragraph(f"리스크 감점 {data['risk_penalty']:.1f}", table_header)])
    score_table = Table(score_rows, colWidths=[35 * mm, 25 * mm, 105 * mm], repeatRows=1)
    score_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#0B7285")),
        ("BACKGROUND", (0, -1), (-1, -1), colors.HexColor("#334E68")),
        ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#9FB3C8")),
        ("INNERGRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#D9E2EC")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING", (0, 0), (-1, -1), 4), ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))
    story += [score_table, PageBreak()]

    # Page 4 - Growth / risk
    story += [Paragraph("4. 성장 가능성과 리스크", h1), Paragraph("성장 전망", h2)]
    story += _paragraphs(data["growth_outlook"], body, limit=450, empty="역할 3 성장 전망 미제공")
    story += [Spacer(1, 7), Paragraph("경쟁 구도", h2),
              Paragraph(_escape(_shorten(data["competition"].get("summary") or data["competition"], 700)), body),
              Spacer(1, 7), Paragraph("핵심 리스크", h2)]
    if data["risks"]:
        risk_rows = [[Paragraph("구분", table_header), Paragraph("리스크", table_header),
                      Paragraph("대응", table_header), Paragraph("감점", table_header)]]
        for risk in data["risks"]:
            risk_rows.append([
                Paragraph("치명" if risk["fatal"] else "일반", table_body),
                Paragraph(_escape(_shorten(risk["description"], 220)), table_body),
                Paragraph(_escape(_shorten(risk["mitigation"], 180)), table_body),
                Paragraph(f"{risk['penalty']:.0f}", table_body),
            ])
        risk_table = Table(risk_rows, colWidths=[18 * mm, 70 * mm, 62 * mm, 15 * mm], repeatRows=1)
        risk_table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#9C2C2C")),
            ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#D9A5A5")),
            ("INNERGRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#E6CACA")),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ]))
        story.append(risk_table)
    else:
        story.append(Paragraph("역할 3에서 전달한 리스크가 없습니다.", body))
    story += [Spacer(1, 10), Paragraph("최종 판단", h2),
              Paragraph(_escape(f"{data['decision']}: {_shorten(data['decision_reason'], 650)}"), body),
              PageBreak()]

    # Page 5 - only references actually used by role 3 fields.
    story += [Paragraph("5. Reference", h1),
              Paragraph("본문·점수·리스크에서 source_ids로 실제 참조한 출처만 포함합니다.", small), Spacer(1, 6)]
    references = [format_reference(source, number) for number, source in enumerate(data["references"], 1)]
    if not references:
        story.append(Paragraph("보고서에 사용된 출처가 없습니다.", body))
    elif len(references) <= 12:
        story += _paragraphs(references, small, limit=430)
    else:
        left = references[::2]
        right = references[1::2]
        rows = []
        for index in range(max(len(left), len(right))):
            rows.append([
                Paragraph(_escape(_shorten(left[index], 360)), small) if index < len(left) else "",
                Paragraph(_escape(_shorten(right[index], 360)), small) if index < len(right) else "",
            ])
        reference_table = Table(rows, colWidths=[82 * mm, 82 * mm])
        reference_table.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"),
                                              ("LEFTPADDING", (0, 0), (-1, -1), 2),
                                              ("RIGHTPADDING", (0, 0), (-1, -1), 4),
                                              ("BOTTOMPADDING", (0, 0), (-1, -1), 4)]))
        story.append(reference_table)

    document = SimpleDocTemplate(
        str(output), pagesize=A4, rightMargin=18 * mm, leftMargin=18 * mm,
        topMargin=20 * mm, bottomMargin=16 * mm,
        title=data["title"], author=team,
    )
    document.build(story, onFirstPage=header_footer, onLaterPages=header_footer)

    reader = PdfReader(str(output))
    pages = len(reader.pages)
    if pages != MAX_PDF_PAGES:
        output.unlink(missing_ok=True)
        raise RuntimeError(f"보고서는 정확히 {MAX_PDF_PAGES}페이지여야 합니다. 생성 결과: {pages}페이지")
    if len(_shorten(data["summary"], MAX_SUMMARY_CHARS)) > MAX_SUMMARY_CHARS:
        raise RuntimeError("Summary 길이 제한 검증에 실패했습니다.")
    return {"path": str(output.resolve()), "pages": pages, "references": len(data["references"])}


def _as_display(value: Any) -> str:
    if isinstance(value, (list, tuple)):
        return ", ".join(str(item) for item in value)
    return str(value or "-")
