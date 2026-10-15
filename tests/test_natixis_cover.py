"""Natixis 封面 TDCC 行的真實版面（Issue #270）：新版標籤「受託或銷售機構商品代號為」且值空白、ISIN 換到下一行。

合成投資人須知的這一行加長後會超出版面（合成器不換行），這裡直接用擷取後的行測解析。代號與 ISIN 皆虛構。
"""

from __future__ import annotations

from fcn_checker.parsers import natixis
from fcn_checker.parsers.layout import TextIndex
from fcn_checker.schema import FieldStatus, Line


def ti(*texts: str) -> TextIndex:
    return TextIndex([Line(1, 70.0, 79.0 + 12 * i, 560.0, 90.0 + 12 * i, t) for i, t in enumerate(texts)])


def test_new_distributor_label_with_a_blank_code():
    text = ti(
        "商品種類：股權連結結構型商品，TDCC商品代號為328199990001，受託或銷售機構商品代號為", "，ISIN為 XS1999900001"
    )
    found = list(text.finditer(natixis.PRODUCT_CODES))
    assert [(m[1], m[2], m[3]) for m in found] == [("328199990001", "", "XS1999900001")]
    code = natixis.distributor_code(text, natixis.PRODUCT_CODES)
    assert code.status == FieldStatus.PRESENT and code.value == ""


def test_old_distributor_label_and_no_label_still_read():
    old = ti("TDCC商品代號為328199990001，受託機構商品代號為，ISIN為XS1999900001")
    assert natixis.distributor_code(old, natixis.PRODUCT_CODES).value == ""
    none = ti("TDCC商品代號為328199990001，ISIN為XS1999900001")
    assert natixis.distributor_code(none, natixis.PRODUCT_CODES).status != FieldStatus.PRESENT
