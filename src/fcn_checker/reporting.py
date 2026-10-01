"""報告輸出：JSON（完整、可重現）與 Markdown（問題項目優先）。"""

from __future__ import annotations

import datetime as dt
import json
from decimal import Decimal
from pathlib import Path
from typing import Any

from .checker import CheckReport
from .schema import CheckResult, CheckStatus

STATUS_ZH = {
    CheckStatus.PASS: "通過",
    CheckStatus.MISMATCH: "不一致",
    CheckStatus.REVIEW_REQUIRED: "需人工覆核",
    CheckStatus.NOT_APPLICABLE: "不適用",
    CheckStatus.ERROR: "執行錯誤",
}
FIELD_ZH = {
    "template": "範本",
    "product_code": "商品代號",
    "currency": "幣別",
    "underlyings": "標的彭博代號",
    "strike_pct": "執行 %",
    "ko_pct": "KO %",
    "ko_type": "KO 觀察方式／記憶式",
    "ki_type": "KI 型態",
    "ki_pct": "KI %",
    "coupon_pa_pct": "年利率 %",
    "monthly_coupon_pct": "月配息率 %",
    "tenor_months": "天期（月）",
    "trade_date": "交易日",
    "issue_date": "發行日",
    "final_valuation_date": "最終評價日",
    "maturity_date": "到期日",
    "issue_date_offset_days": "發行日 − 交易日（天）",
    "price_table": "價格表",
    "denomination": "面額",
    "subscription_start_date": "開始受理申購日",
    "print_date": "刊印日期",
    "approval_date": "受託機構審查通過日期",
    "chairman": "受託機構負責人",
    "fixed_warning": "固定風險警語",
    "risk_level": "風險等級",
    "forbidden_wording": "禁用語「受託投資」",
    "name_zh": "中文商品名稱",
    "name_en": "英文商品名稱",
}
_ORDER = {CheckStatus.ERROR: 0, CheckStatus.MISMATCH: 1, CheckStatus.REVIEW_REQUIRED: 2}


def _plain(v: Any) -> Any:
    if isinstance(v, Decimal):
        return str(v)
    if isinstance(v, dt.date):
        return v.isoformat()
    if isinstance(v, (list, tuple)):
        return [_plain(x) for x in v]
    if isinstance(v, dict):
        return {str(k): _plain(x) for k, x in v.items()}
    return v


def _result_dict(r: CheckResult) -> dict[str, Any]:
    return {
        "rule_id": r.rule_id,
        "rule_version": r.rule_version,
        "field": r.field,
        "status": r.status.value,
        "expected": _plain(r.expected),
        "actual": _plain(r.actual),
        "tolerance": r.tolerance,
        "reason_code": r.reason_code,
        "message": r.message,
        "document_evidence": [e.to_dict() for e in r.document_evidence],
        "order_source": list(r.order_source),
    }


def to_json(report: CheckReport) -> dict[str, Any]:
    counts = {s.value: sum(1 for r in report.results if r.status == s) for s in CheckStatus}
    return {
        "status": report.status.value,
        "template": report.template,
        "summary": counts,
        "results": [_result_dict(r) for r in report.results],
        "not_covered": report.not_covered,
        "metadata": _plain(report.metadata),
    }


def _cell(v: Any) -> str:
    if v is None or v == "":
        return "—"
    v = _plain(v)
    if isinstance(v, list):
        v = "、".join(str(x) for x in v)
    elif isinstance(v, dict):
        v = "；".join(f"{k}：{_cell(x)}" for k, x in v.items())
    return str(v).replace("|", "\\|").replace("\n", " ")


def _where(r: CheckResult) -> str:
    if not r.document_evidence:
        return "—"
    parts = [_cell(f"p.{e.page}「{e.text[:40]}{'…' if len(e.text) > 40 else ''}」") for e in r.document_evidence[:3]]
    more = f" 等 {len(r.document_evidence)} 處" if len(r.document_evidence) > 3 else ""
    return "<br>".join(parts) + more


def _table(rows: list[CheckResult], with_message: bool = True) -> list[str]:
    head = "| 狀態 | 規則 | 欄位 | 詢價表／標準值 | 說明書值 | 容差 | 說明 | 說明書位置 | 詢價表位置 |"
    out = [head, "|" + "---|" * 9]
    for r in rows:
        out.append(
            f"| {STATUS_ZH[r.status]} | `{r.rule_id}` | {_cell(FIELD_ZH.get(r.field, r.field))} | {_cell(r.expected)} | {_cell(r.actual)} "
            f"| {_cell(r.tolerance)} | {_cell(r.message if with_message else '')} | {_where(r)} "
            f"| {_cell(r.order_source)} |"
        )
    return out


def to_markdown(report: CheckReport) -> str:
    meta = report.metadata
    issues = sorted(
        (r for r in report.results if r.status in _ORDER),
        key=lambda r: _ORDER[r.status],
    )
    ok = [r for r in report.results if r.status not in _ORDER]
    ts, order = meta["inputs"]["term_sheet"], meta["inputs"]["order"]
    lines = [
        "# FCN Term Sheet 核對報告",
        "",
        f"- **整體狀態：{report.status.value}（{STATUS_ZH[report.status]}）**",
        f"- 範本：{report.template or '未辨識'}",
        f"- 說明書：`{ts['file']}`（sha256 `{ts['sha256']}`）",
        f"- 詢價表：`{order['file']}`（sha256 `{order['sha256']}`）",
        f"- 審查標準：`{meta['review_standard']['file']}` 版本 {meta['review_standard'].get('version', '—')}"
        f"（生效 {meta['review_standard'].get('effective_date', '—')}）",
        f"- 詢價格式：`{meta['order_format']['file'] or '—'}` {meta['order_format'].get('issuer', '—')} "
        f"版本 {meta['order_format'].get('version', '—')}",
        f"- 程式版本 {meta['program_version']}；{meta['extractor']}；{meta['excel_reader']}；產生時間 {meta['generated_at']}",
        "",
        "> 整體狀態只涵蓋本報告列出的規則；「未涵蓋」區的項目仍須人工核對。",
        "",
        f"## 問題項目（{len(issues)}）",
        "",
    ]
    lines += _table(issues) if issues else ["沒有不一致或需人工覆核的項目。"]
    lines += [
        "",
        f"## 未涵蓋規則（{len(report.not_covered)}）",
        "",
        "以下規則本階段尚未實作，不影響整體狀態，也不代表通過：",
        "",
    ]
    lines += [f"- `{n['rule_id']}`：{n['description']}" for n in report.not_covered]
    lines += ["", f"## 通過與不適用項目（{len(ok)}）", ""]
    lines += _table(ok) if ok else ["（無）"]
    return "\n".join(lines) + "\n"


def write_reports(report: CheckReport, out_dir: Path, stem: str) -> tuple[Path, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    jp = out_dir / f"{stem}.check.json"
    mp = out_dir / f"{stem}.check.md"
    jp.write_text(json.dumps(to_json(report), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    mp.write_text(to_markdown(report), encoding="utf-8")
    return jp, mp
