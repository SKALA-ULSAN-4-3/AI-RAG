"""출처와 점수 계약을 유지하는 5페이지 투자 검토 보고서."""

from __future__ import annotations

import html
import os
from pathlib import Path
from typing import Any

MAX_SUMMARY_CHARS = 700
MAX_PDF_PAGES = 5


def _shorten(value: Any, limit: int, fallback: str = "근거 미제공") -> str:
    text = " ".join(str(value or "").split()) or fallback
    return text if len(text) <= limit else text[:max(0, limit - 1)].rstrip() + "…"


def _escape(value: Any) -> str:
    return html.escape(str(value or ""), quote=False)


def _as_display(value: Any) -> str:
    if isinstance(value, dict):
        return "; ".join(f"{k}: {_as_display(v)}" for k, v in value.items())
    if isinstance(value, (list, tuple)):
        return ", ".join(_as_display(item) for item in value)
    return str(value if value is not None else "근거 미제공")


def _font_path() -> Path:
    configured = os.getenv("REPORT_FONT_PATH", "").strip()
    candidates = [Path(configured) if configured else None,
        Path("/System/Library/Fonts/Supplemental/AppleGothic.ttf"),
        Path("/System/Library/Fonts/Supplemental/NotoSansGothic-Regular.ttf"),
        Path("/usr/share/fonts/truetype/nanum/NanumGothic.ttf"),
        Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"),
        Path("C:/Windows/Fonts/malgun.ttf")]
    for candidate in candidates:
        if candidate and candidate.is_file():
            return candidate
    raise RuntimeError("한국어 글꼴이 없습니다. REPORT_FONT_PATH에 TTF/TTC 경로를 지정하세요.")


def _register_fonts() -> tuple[str, str]:
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    regular, bold = "InvestmentReportKorean", "InvestmentReportKoreanBold"
    if regular not in pdfmetrics.getRegisteredFontNames():
        path = _font_path()
        pdfmetrics.registerFont(TTFont(regular, str(path)))
        pdfmetrics.registerFont(TTFont(bold, str(path)))
    return regular, bold


def format_reference(source: dict, number: int) -> str:
    author = source.get("publisher") or source.get("author") or source.get("company") or "발행 주체 미상"
    page = f", p.{source['page']}" if source.get("page") else ""
    return (f"[{number}] {author}. {source.get('title') or '제목 미상'}. "
            f"{source.get('published_at') or '발행일 미상'}. {source.get('url')}{page}. "
            f"(확인: {source.get('accessed_at') or '확인일 미상'})")


def build_markdown_report(data: dict) -> str:
    lines = [f"# {data['title']}", "", f"- 대상 기업: {data['company']['name']}",
             f"- 투자 판단: {data['decision']}", f"- 최종 점수: {data['total_score']:.1f}/100",
             f"- 판단 근거: {data['decision_reason']}", "", "## Summary",
             _shorten(data["summary"], MAX_SUMMARY_CHARS), "", "## 점수표",
             "| 항목 | 점수 | 근거 |", "| --- | ---: | --- |"]
    for row in data["scorecard"]:
        score = "미제공" if row["score"] is None else f"{row['score']:.1f}/{row['max_score']:.0f}"
        lines.append(f"| {row['label']} | {score} | {row['reason']} |")
    lines += ["", "## 리스크"]
    lines += [f"- {r['description']} ({r['penalty']:.0f}점)" for r in data["risks"]] or ["- 보고된 리스크 없음"]
    lines += ["", "## Reference"]
    lines += [f"- {format_reference(s, n)}" for n, s in enumerate(data["references"], 1)] or ["- 보고서에 사용된 출처 없음"]
    return "\n".join(lines)


def render_pdf_report(data: dict, output: Path, *, team: str = "울산 캠퍼스 4반 3조",
                      contributors: str = "안영준, 정하윤, 손수경, 손경락") -> dict:
    """자료 부족은 공개하고, 입력 점수와 출처가 명시된 수치만 도표로 표시한다."""
    try:
        from investment_scout.reporting.layout import render_research_report
        return render_research_report(data, Path(output), team=team, contributors=contributors)
    except ImportError as exc:
        raise RuntimeError("PDF 의존성을 설치하세요: uv sync --extra report") from exc
