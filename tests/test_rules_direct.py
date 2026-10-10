"""不經 PDF 直接打規則：以 dict 建讀出結果與參考條件表列（rule_fixtures），驗證規則各分支與核對結果 builder。

測試切點：規則函式（`reference.ki_pct`、`review_standard_rules`、`barc.distributor_product_code`）與 `kit.Check`。
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from fcn_checker import standard_fields
from fcn_checker.rules import barc, reference
from fcn_checker.rules.kit import Check
from fcn_checker.rules.review_standard import review_standard_rules
from fcn_checker.schema import Evidence, Item, ParsedField
from fcn_checker.standard_fields import Money, Occurrence, occurrences
from harness import CONFIG, MISMATCH, NA, PASS, REVIEW
from rule_fixtures import context, issuer_context, term_sheet

# ---------------------------------------------------------------- 參考條件表欄位：KI %


@pytest.mark.parametrize(
    "sheet,ki_type,ki_pct,status,reason,text",
    [
        (None, "AM", Decimal("60.00"), REVIEW, "order_missing", "沒有此欄位或值為空白"),
        ("abc", "AM", Decimal("60.00"), REVIEW, "order_invalid", "不是數字或 -"),
        (Decimal("60"), None, Decimal("60.00"), REVIEW, "document_missing", "說明書抓不到此欄位"),
        ("-", "none", None, NA, "", "雙方皆無 KI"),
        (Decimal("60"), "none", None, MISMATCH, "value_mismatch", "KI(%) 應為 -"),
        (Decimal("60"), "AM", None, REVIEW, "document_missing", "說明書抓不到此欄位"),
        ("-", "AM", Decimal("60.00"), MISMATCH, "value_mismatch", "表上 KI(%) 卻是空值"),
        (Decimal("60"), "AM", Decimal("60.00"), PASS, "", ""),
        (Decimal("61"), "AM", Decimal("60.00"), MISMATCH, "value_mismatch", ""),
    ],
)
def test_ki_pct_branches(sheet, ki_type, ki_pct, status, reason, text):
    fields = {name: v for name, v in (("ki_type", ki_type), ("ki_pct", ki_pct)) if v is not None}
    ctx = context(term_sheet(**fields), ki_pct=sheet)

    r = reference.ki_pct(ctx)

    assert (r.rule_id, r.status, r.reason_code) == ("field.ki_pct", status, reason)
    assert text in r.message
    assert r.item.name == "KI(%)" and r.item.columns == ("KI(%)",), "項目是那一欄"
    if sheet is not None:
        assert r.order_source and r.order_source[0].endswith("4"), "來源儲存格是這一列"


def test_ki_pct_pass_shows_the_rounded_sheet_value():
    r = reference.ki_pct(context(term_sheet(ki_type="AM", ki_pct=Decimal("60.00")), ki_pct=Decimal("60")))
    assert (r.expected, r.actual, r.tolerance) == (Decimal("60.00"), Decimal("60.00"), reference.PCT_TOLERANCE)


# ---------------------------------------------------------------- 審查標準：負責人


CHAIRMAN = CONFIG.review_standard.chairman


def _chairman(ts):
    [r] = [r for r in review_standard_rules(context(ts)) if r.rule_id == "standard.chairman"]
    return r


def test_chairman_matches_the_review_standard():
    r = _chairman(term_sheet(chairman=CHAIRMAN))
    assert (r.status, r.expected, r.actual, r.message) == (PASS, CHAIRMAN, CHAIRMAN, "")


def test_chairman_mismatch_spells_out_the_codepoints():
    r = _chairman(term_sheet(chairman=CHAIRMAN + "（代理）"))
    assert (r.status, r.reason_code) == (MISMATCH, "value_mismatch")
    assert "U+" in r.message and r.item.name == "受託機構負責人"


def test_chairman_unreadable_is_review_with_the_standard_shown():
    r = _chairman(term_sheet())
    assert (r.status, r.reason_code, r.expected) == (REVIEW, "document_missing", CHAIRMAN)
    assert r.message.startswith("說明書抓不到此欄位")


# ---------------------------------------------------------------- 上手說明書內部規則：BARC 受託機構商品代號


def test_barc_distributor_code_equals_product_code():
    ctx = issuer_context(term_sheet(product_code="029199990001", distributor_product_code="029199990001"))
    r = barc.distributor_product_code(ctx)
    assert (r.status, r.expected, r.actual) == (PASS, "029199990001", "029199990001")
    assert r.message == "封面「受託或銷售機構商品代號」須等於「商品代號」"

    ctx = issuer_context(term_sheet(product_code="029199990001", distributor_product_code="029199990002"))
    r = barc.distributor_product_code(ctx)
    assert (r.status, r.reason_code, r.item.name) == (MISMATCH, "document_inconsistent", "受託或銷售機構商品代號")

    r = barc.distributor_product_code(issuer_context(term_sheet(product_code="029199990001")))
    assert (r.status, r.reason_code) == (REVIEW, "document_missing")
    assert "distributor_product_code" in r.message, "缺漏的是受託機構商品代號那一欄"


# ---------------------------------------------------------------- 核對結果 builder


def test_check_reviews_on_the_first_dependency_that_is_not_ok():
    ok = ParsedField.present("a", 1, [])
    missing = ParsedField.missing("b", "找不到 b")
    invalid = ParsedField.invalid("c", [], "c 壞掉")
    check = Check("x.y", "f", Item.expected("項目")).needs(ok, missing, invalid)

    r = check.compare(1, 1)

    assert (r.status, r.reason_code, r.message) == (REVIEW, "document_missing", "說明書抓不到此欄位：找不到 b")
    assert (r.rule_id, r.field, r.item.name, r.expected) == ("x.y", "f", "項目", None)


def test_check_compare_writes_the_failure_text_only_when_it_fails():
    check = Check("x.y", "f", Item.expected("項目")).needs(ParsedField.present("a", 1, []))

    good = check.compare(1, 1, message="說明", fail_message="（不符）")
    bad = check.compare(1, 2, message="說明", fail_message="（不符）", reason="document_inconsistent")

    assert (good.status, good.reason_code, good.message) == (PASS, "", "說明")
    assert (bad.status, bad.reason_code, bad.message, bad.expected, bad.actual) == (
        MISMATCH,
        "document_inconsistent",
        "說明（不符）",
        1,
        2,
    )


def test_check_carries_the_expected_value_into_the_review():
    r = Check("x.y", "f", Item.expected("項目"), expected="預期").needs(ParsedField.missing("a")).compare("預期", None)
    assert (r.status, r.expected) == (REVIEW, "預期")


# ---------------------------------------------------------------- 參考條件表欄位：金額旁的幣別（Issue #170）


def _currency_others(cover, items, **row):
    occs = [
        Occurrence(
            field,
            f"{field} 名稱",
            f"{field} 位置",
            v if isinstance(v, ParsedField) else ParsedField.present(field, v, []),
        )
        for field, v in items.items()
    ]
    ts = term_sheet(currency_zh=cover, currency_others=occurrences("currency_others", occs))
    return reference.currency_others(context(ts, **row))


def test_currency_occurrences_map_chinese_words_and_iso_codes_to_the_sheet():
    out = _currency_others("美元", {"a": "美元", "b": "USD", "c": "日幣", "d": "JPY"}, currency="USD")
    assert [(r.field, r.status, r.actual) for r in out] == [
        ("a", PASS, "USD"),
        ("b", PASS, "USD"),
        ("c", MISMATCH, "JPY"),
        ("d", MISMATCH, "JPY"),
    ]
    assert out[2].message == "c 位置須等於參考條件表「承作幣別」（日幣 → JPY）" and out[2].item.name == "c 名稱"


def test_currency_word_outside_the_table_is_reported_once_at_the_cover():
    """封面幣別不在對照表時封面那筆已轉人工覆核；寫同一個字的出處不重複，寫別的字才各自報。"""
    assert _currency_others("澳幣", {"a": "澳幣"}, currency="AUD") == []
    [r] = _currency_others("澳幣", {"a": "歐元"}, currency="AUD")
    assert (r.status, r.reason_code, r.actual) == (REVIEW, "currency_unknown", "歐元")
    [r] = _currency_others("美元", {"a": "歐元"}, currency="USD")
    assert (r.status, r.reason_code) == (REVIEW, "currency_unknown") and "「歐元」" in r.message


def test_scenario_currency_lists_only_the_amounts_that_do_not_match():
    ev = Evidence(1, (0.0, 0.0, 1.0, 1.0), "x")
    good = Money("100.00美元", "美元", (ev,))
    bad, odd = Money("10,100.00日幣", "日幣", (ev,)), Money("1.00歐元", "歐元", ())
    [r] = _currency_others("美元", {"s": (good, good)}, currency="USD")
    assert (r.status, r.expected, r.actual) == (PASS, "USD", "USD")
    [r] = _currency_others("美元", {"s": (good, bad, odd)}, currency="USD")
    assert (r.status, r.actual, r.document_evidence) == (MISMATCH, ["10,100.00日幣"], [ev])
    [r] = _currency_others("美元", {"s": (good, odd)}, currency="USD")
    assert (r.status, r.reason_code, r.actual) == (REVIEW, "currency_unknown", "歐元")
    assert _currency_others("澳幣", {"s": (Money("1澳幣", "澳幣", ()),)}, currency="AUD") == []


def test_currency_occurrences_are_silent_when_the_sheet_currency_is_blank_or_absent():
    assert _currency_others("美元", {"a": "美元"}) == []
    ts = term_sheet(currency_zh="美元", currency_others=standard_fields.absent("currency_others", "其他出處"))
    assert reference.currency_others(context(ts, currency="USD")) == []
    [r] = _currency_others("美元", {"a": ParsedField.missing("a", "找不到")}, currency="USD")
    assert (r.status, r.reason_code) == (REVIEW, "document_missing")
