"""`fcn-batch` 指令：批量入口（多份說明書＋參考條件表）的薄包裝。

結束碼：0 = 整體 PASS；1 = 有 MISMATCH 或 REVIEW_REQUIRED（含未支援上手）；2 = ERROR。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .batch import BatchOutcome, failed_batch
from .check_config import DEFAULTS, ConfigPaths
from .ingestion import IngestionError
from .messages import STATUS_ZH
from .saving import SaveReceipt, run_batch
from .schema import CheckStatus

EXIT = {CheckStatus.PASS: 0, CheckStatus.MISMATCH: 1, CheckStatus.REVIEW_REQUIRED: 1, CheckStatus.ERROR: 2}


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="fcn-batch",
        description=(
            "以參考條件表（FCN參考條件 的「樣本清單」）批量核對多份說明書 PDF；結果存成一份核對結果檔"
            "（「回填後」：整份通過且回填 ISIN、發行日、比價日的列；「錯誤清單」：沒通過的說明書與錯訊），"
            "原檔不動；另在執行目錄的 runtime/核對紀錄/ 寫一份內部核對紀錄（JSON）。"
        ),
    )
    p.add_argument("reference_sheet", type=Path, help="參考條件表 Excel（.xlsx）")
    p.add_argument("term_sheets", type=Path, nargs="+", help="說明書 PDF（可多份；檔名前三碼為上手編號）")
    p.add_argument(
        "--review-standard",
        type=Path,
        default=DEFAULTS.review_standard,
        help=f"審查標準設定檔（預設 {DEFAULTS.review_standard.as_posix()}）",
    )
    p.add_argument(
        "--reference-format",
        type=Path,
        default=DEFAULTS.reference_format,
        help=f"參考條件表格式設定檔（預設 {DEFAULTS.reference_format.as_posix()}）",
    )
    p.add_argument(
        "--issuer-prefixes",
        type=Path,
        default=DEFAULTS.issuer_prefixes,
        help=f"上手編號對照設定檔（預設 {DEFAULTS.issuer_prefixes.as_posix()}）",
    )
    p.add_argument(
        "--out",
        type=Path,
        default=Path("runtime/reports"),
        help="核對結果檔的輸出資料夾（預設 runtime/reports）",
    )
    return p


def _utf8_console() -> None:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")


def _run(args: argparse.Namespace) -> tuple[BatchOutcome, SaveReceipt]:
    try:
        config = ConfigPaths(args.review_standard, args.reference_format, args.issuer_prefixes).load()
    except IngestionError as e:  # 設定檔有問題：整批錯誤，不核對任何說明書
        outcome = failed_batch(args.reference_sheet, e)
        return outcome, SaveReceipt.nothing_to_save(outcome)
    return run_batch(config, args.reference_sheet, args.term_sheets, args.out, root=Path.cwd())


def main(argv: list[str] | None = None) -> int:
    _utf8_console()
    args = _parser().parse_args(argv)
    try:
        outcome, receipt = _run(args)
    except Exception as e:  # 非預期錯誤：回報後以 ERROR 結束
        print(f"執行錯誤：{type(e).__name__}: {e}", file=sys.stderr)
        return EXIT[CheckStatus.ERROR]
    for item in outcome.items:
        reason = f"  {item.not_filled_reason}" if item.not_filled_reason else ""
        print(f"{item.status_label}  {item.term_sheet.name}{'  已回填' if receipt.filled(item) else ''}{reason}")
    for e in (*outcome.errors, *receipt.errors):
        print(f"  [ERROR] {e.field}：{e.message}", file=sys.stderr)
    print(f"整體狀態：{receipt.status.value}（{STATUS_ZH[receipt.status]}）")
    if receipt.output is not None:
        print(f"核對結果檔：{receipt.output}")
    if receipt.record is not None:
        print(f"核對紀錄：{receipt.record}")
    return EXIT[receipt.status]


if __name__ == "__main__":
    raise SystemExit(main())
