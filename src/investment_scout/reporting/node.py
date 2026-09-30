"""LangGraph 보고서 생성 노드: 최종 State로 PDF를 만들고 final_report에 Markdown 요약 저장."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Dict

from investment_scout.contracts import validate_node_update
from investment_scout.reporting.content import build_report_content
from investment_scout.reporting.pdf_report import build_markdown_report, render_pdf_report


def make_report_node(output_path: Path | str, *, team: str = "울산 캠퍼스 4반 3조"
                     ) -> Callable[[Dict[str, Any]], Dict[str, Any]]:
    output = Path(output_path)

    def generate_report(state: Dict[str, Any]) -> Dict[str, Any]:
        content = build_report_content(state)
        render_pdf_report(content, output, team=team)
        return validate_node_update({"final_report": build_markdown_report(content)},
                                    allowed_fields=("final_report",), node_name="generate_report")

    return generate_report


__all__ = ["make_report_node"]
