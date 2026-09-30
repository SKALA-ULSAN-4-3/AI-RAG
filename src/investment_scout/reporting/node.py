"""LangGraph 보고서 생성 노드."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Dict

from investment_scout.contracts import validate_node_update
from investment_scout.reporting.contracts import normalize_report_input
from investment_scout.reporting.pdf_report import build_markdown_report, render_pdf_report


def make_report_node(
    output_path: Path | str,
    *,
    team: str = "울산 캠퍼스 4반 3조",
    contributors: str = "안영준, 정하윤, 손수경, 손경락",
) -> Callable[[Dict[str, Any]], Dict[str, Any]]:
    """현재 State 또는 향후 role3_handoff를 받아 PDF와 final_report를 생성합니다.

    State 계약을 깨지 않도록 노드 업데이트는 기존 ``final_report``만 반환합니다.
    PDF 경로는 노드를 만들 때 명시하므로 역할 3의 State 필드 변경과 독립적입니다.
    """
    output = Path(output_path)

    def generate_report(state: Dict[str, Any]) -> Dict[str, Any]:
        data = normalize_report_input(state)
        render_pdf_report(data, output, team=team, contributors=contributors)
        return validate_node_update(
            {"final_report": build_markdown_report(data)},
            allowed_fields=("final_report",),
            node_name="generate_report",
        )

    return generate_report


__all__ = ["make_report_node"]
