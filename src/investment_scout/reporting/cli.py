"""역할 4 보고서 생성 CLI.

예시:
    investment-report --input out/role3/handoff.json --out output/pdf/investment_report.pdf
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from investment_scout.reporting.contracts import normalize_report_input
from investment_scout.reporting.pdf_report import build_markdown_report, render_pdf_report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True, help="role3_handoff 또는 전체 State JSON")
    parser.add_argument("--out", type=Path, default=Path("output/pdf/investment_report.pdf"))
    parser.add_argument("--markdown-out", type=Path, default=None)
    parser.add_argument("--team", default="울산 캠퍼스 4반 3조")
    parser.add_argument("--contributors", default="안영준, 정하윤, 손수경, 손경락")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        payload = json.loads(args.input.read_text(encoding="utf-8"))
        data = normalize_report_input(payload)
        result = render_pdf_report(
            data, args.out, team=args.team, contributors=args.contributors,
        )
        markdown = build_markdown_report(data)
        if args.markdown_out:
            args.markdown_out.parent.mkdir(parents=True, exist_ok=True)
            args.markdown_out.write_text(markdown, encoding="utf-8")
        print(
            f"보고서 저장: {result['path']} "
            f"({result['pages']}페이지, 실제 사용 출처 {result['references']}건)"
        )
        return 0
    except Exception as exc:
        print(f"ERROR [{type(exc).__name__}]: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
