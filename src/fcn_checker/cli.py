"""`fcn-check`（說明書＋詢價表）與 `fcn-batch`（多份說明書＋參考條件表）指令：核對入口的薄包裝。

結束碼：0 = 整體 PASS；1 = 有 MISMATCH 或 REVIEW_REQUIRED（含未支援上手）；2 = ERROR。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .batch import run_batch
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


def _utf8_console() -> None:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")


def main(argv: list[str] | None = None) -> int:
    _utf8_console()
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


def _batch_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="fcn-batch",
        description=(
            "以參考條件表（FCN參考條件 的「樣本清單」）批量核對多份說明書 PDF；整份通過的說明書回填 ISIN 與比價日，"
            "結果另存為新檔（原檔不動），每份說明書另有 JSON 與 Markdown 報告。"
        ),
    )
    p.add_argument("reference_sheet", type=Path, help="參考條件表 Excel（.xlsx）")
    p.add_argument("term_sheets", type=Path, nargs="+", help="說明書 PDF（可多份；檔名前三碼為上手編號）")
    p.add_argument(
        "--review-standard",
        type=Path,
        default=Path("config/review_standard.toml"),
        help="審查標準設定檔（預設 config/review_standard.toml）",
    )
    p.add_argument(
        "--reference-format",
        type=Path,
        default=Path("config/reference_sheet.toml"),
        help="參考條件表格式設定檔（預設 config/reference_sheet.toml）",
    )
    p.add_argument(
        "--issuer-prefixes",
        type=Path,
        default=Path("config/issuer_prefixes.toml"),
        help="上手編號對照設定檔（預設 config/issuer_prefixes.toml）",
    )
    p.add_argument("--out", type=Path, default=Path("runtime/reports"), help="報告輸出資料夾（預設 runtime/reports）")
    return p


def batch_main(argv: list[str] | None = None) -> int:
    _utf8_console()
    args = _batch_parser().parse_args(argv)
    try:
        outcome = run_batch(
            args.term_sheets,
            args.reference_sheet,
            args.review_standard,
            args.out,
            reference_format=args.reference_format,
            issuer_prefixes=args.issuer_prefixes,
        )
    except Exception as e:  # 非預期錯誤：回報後以 ERROR 結束
        print(f"執行錯誤：{type(e).__name__}: {e}", file=sys.stderr)
        return EXIT[CheckStatus.ERROR]
    for item in outcome.items:
        label = "未支援上手" if item.unsupported else f"{item.report.status.value}（{STATUS_ZH[item.report.status]}）"
        print(f"{label}  {item.term_sheet.name}{'  已回填' if item.filled else ''}")
    for e in outcome.errors:
        print(f"  [ERROR] {e.field}：{e.message}", file=sys.stderr)
    print(f"整體狀態：{outcome.status.value}（{STATUS_ZH[outcome.status]}）")
    if outcome.output is not None:
        print(f"回填新檔：{outcome.output}")
    if outcome.items:
        print(f"報告資料夾：{args.out}")
    return EXIT[outcome.status]


if __name__ == "__main__":
    raise SystemExit(main())
