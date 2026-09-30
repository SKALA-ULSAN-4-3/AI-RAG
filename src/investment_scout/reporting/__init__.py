"""역할 4: 역할 3 결과를 5페이지 투자 보고서로 변환합니다."""

from investment_scout.reporting.contracts import Role3HandoffError, normalize_report_input
from investment_scout.reporting.node import make_report_node
from investment_scout.reporting.pdf_report import build_markdown_report, render_pdf_report

__all__ = [
    "Role3HandoffError",
    "build_markdown_report",
    "make_report_node",
    "normalize_report_input",
    "render_pdf_report",
]
