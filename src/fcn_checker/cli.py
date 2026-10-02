"""`fcn-batch` 指令：批量入口（多份說明書＋參考條件表）的薄包裝。

結束碼：0 = 整體 PASS；1 = 有 MISMATCH 或 REVIEW_REQUIRED（含未支援上手）；2 = ERROR。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .batch import run_batch
from .reporting import STATUS_ZH
from .schema import CheckStatus

EXIT = {CheckStatus.PASS: 0, CheckStatus.MISMATCH: 1, CheckStatus.REVIEW_REQUIRED: 1, CheckStatus.ERROR: 2}


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="fcn-batch",
        description=(
            "以參考條件表（FCN參考條件 的「樣本清單」）批量核對多份說明書 PDF；結果存成一份核對結果檔"
            "（「回填後」：整份通過且回填 ISIN、發行日、比價日的列；「錯誤清單」：沒通過的說明書與錯訊），"
            "原檔不動；每份說明書另有 JSON 與 Markdown 報告。"
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
    p.add_argument(
        "--out",
        type=Path,
        default=Path("runtime/reports"),
        help="核對結果檔與每份報告的輸出資料夾（預設 runtime/reports）",
    )
    return p


def _utf8_console() -> None:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")


def main(argv: list[str] | None = None) -> int:
    _utf8_console()
    args = _parser().parse_args(argv)
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
        print(f"{item.status_label}  {item.term_sheet.name}{'  已回填' if item.filled else ''}")
        if item.save_error:
            print(f"  [ERROR] 報告未儲存：{item.save_error}", file=sys.stderr)
    for e in outcome.errors:
        print(f"  [ERROR] {e.field}：{e.message}", file=sys.stderr)
    print(f"整體狀態：{outcome.status.value}（{STATUS_ZH[outcome.status]}）")
    if outcome.output is not None:
        print(f"核對結果檔：{outcome.output}")
    if outcome.items:
        print(f"報告資料夾：{args.out}")
    return EXIT[outcome.status]


if __name__ == "__main__":
    raise SystemExit(main())
