"""`fcn-check` 指令：核對入口的薄包裝。

結束碼：0 = 整體 PASS；1 = 有 MISMATCH 或 REVIEW_REQUIRED；2 = ERROR。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .checker import run_check
from .reporting import STATUS_ZH, write_reports
from .schema import CheckStatus

EXIT = {CheckStatus.PASS: 0, CheckStatus.MISMATCH: 1, CheckStatus.REVIEW_REQUIRED: 1, CheckStatus.ERROR: 2}


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="fcn-check",
        description="核對上手中文產品說明書（PDF）與該上手詢價表（Excel），輸出 JSON 與 Markdown 報告。",
    )
    p.add_argument("term_sheet", type=Path, help="說明書 PDF")
    p.add_argument("order", type=Path, help="詢價表 Excel（.xlsx）")
    p.add_argument(
        "--review-standard",
        type=Path,
        default=Path("config/review_standard.toml"),
        help="審查標準設定檔（預設 config/review_standard.toml）",
    )
    p.add_argument(
        "--order-format",
        type=Path,
        default=None,
        help="詢價格式設定檔（預設依辨識到的上手，例如 BARC → config/order_formats/barc.toml）",
    )
    p.add_argument("--out", type=Path, default=Path("runtime/reports"), help="報告輸出資料夾（預設 runtime/reports）")
    return p


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    args = _parser().parse_args(argv)
    try:
        report = run_check(args.term_sheet, args.order, args.review_standard, args.order_format)
        json_path, md_path = write_reports(report, args.out, args.term_sheet.stem.replace(" ", ""))
    except Exception as e:  # 非預期錯誤：回報後以 ERROR 結束，不輸出堆疊到報告
        print(f"執行錯誤：{type(e).__name__}: {e}", file=sys.stderr)
        return EXIT[CheckStatus.ERROR]
    counts = {s: sum(1 for r in report.results if r.status == s) for s in CheckStatus}
    print(f"整體狀態：{report.status.value}（{STATUS_ZH[report.status]}）")
    print(
        f"不一致 {counts[CheckStatus.MISMATCH]}、需人工覆核 {counts[CheckStatus.REVIEW_REQUIRED]}、"
        f"執行錯誤 {counts[CheckStatus.ERROR]}、通過 {counts[CheckStatus.PASS]}；未涵蓋規則 {len(report.not_covered)} 項"
    )
    for r in report.results:
        if r.status == CheckStatus.ERROR:
            print(f"  [ERROR] {r.field}：{r.message}", file=sys.stderr)
    print(f"報告：{json_path}")
    print(f"報告：{md_path}")
    return EXIT[report.status]


if __name__ == "__main__":
    raise SystemExit(main())
