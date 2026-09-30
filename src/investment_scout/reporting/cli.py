"""보고서만 다시 생성: pipeline이 저장한 최종 State JSON → 5페이지 PDF (API 호출 없음).

예시:
    investment-report --input out/tech_rag/pipeline.json --out output/pdf/investment_report.pdf
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from investment_scout.reporting.content import build_report_content
from investment_scout.reporting.pdf_report import build_markdown_report, render_pdf_report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=Path("out/tech_rag/pipeline.json"),
                        help="pipeline이 저장한 최종 State JSON")
    parser.add_argument("--out", type=Path, default=Path("output/pdf/investment_report.pdf"))
    parser.add_argument("--markdown-out", type=Path, default=None)
    parser.add_argument("--team", default="울산 캠퍼스 4반 3조")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        content = build_report_content(json.loads(args.input.read_text(encoding="utf-8")))
        result = render_pdf_report(content, args.out, team=args.team)
        if args.markdown_out:
            args.markdown_out.parent.mkdir(parents=True, exist_ok=True)
            args.markdown_out.write_text(build_markdown_report(content), encoding="utf-8")
        print(f"보고서 저장: {result['path']} ({result['pages']}페이지, 인용 자료 {result['references']}건)")
        return 0
    except Exception as exc:
        print(f"ERROR [{type(exc).__name__}]: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
