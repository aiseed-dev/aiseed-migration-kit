"""inquiry: 様式 xlsx の生成 → 読み取りの往復と、送信用テキストの寛容パーサ。"""

import io

import pytest
from openpyxl import load_workbook

from cmsmig.inquiry import forms, parse

FILLED = {
    "company": "株式会社○○製作所",
    "person": "山田 太郎",
    "tel": "088-000-0000",
    "email": "taro@example.co.jp",
    "message": "引張試験について相談したい",
}


def _xlsx(example, staff_label="総合窓口", ver=None, values=FILLED) -> bytes:
    """example の contact 様式を組み立てて記入し、bytes で返す。"""
    form = example.form("contact")
    wb = forms.build(example, form, "uketsuke@example.jp")
    ws = wb[forms.SHEET]
    n = forms.names(form)
    if staff_label:
        ws[n["staff"]] = staff_label
    if ver is not None:
        ws[n["form_ver"]] = ver
    for key, v in values.items():
        ws[n[f"f_{key}"]] = v
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def test_xlsx_roundtrip(example):
    inq = parse.parse_xlsx(_xlsx(example), example)
    assert inq.form.key == "contact"
    assert inq.staff.key == "general"
    assert inq.values["person"] == "山田 太郎"


def test_xlsx_missing_required(example):
    values = dict(FILLED)
    del values["tel"]
    with pytest.raises(parse.Invalid) as e:
        parse.parse_xlsx(_xlsx(example, values=values), example)
    assert any("電話番号" in s for s in e.value.issues)


def test_xlsx_no_staff(example):
    with pytest.raises(parse.Invalid) as e:
        parse.parse_xlsx(_xlsx(example, staff_label=""), example)
    assert any("宛先" in s for s in e.value.issues)


def test_xlsx_wrong_version(example):
    with pytest.raises(parse.Invalid, match="版"):
        parse.parse_xlsx(_xlsx(example, ver=99), example)


def test_not_a_form(example):
    with pytest.raises(parse.Invalid, match="様式のファイル"):
        parse.parse_xlsx(b"not an xlsx", example)


def test_workbook_structure(example):
    """様式の作り: シート保護・ドロップダウン・例シート・非表示メタ。"""
    wb = load_workbook(io.BytesIO(_xlsx(example)))
    ws = wb[forms.SHEET]
    assert ws.protection.sheet
    assert wb[forms.TEXT_SHEET]["A1"].value.startswith("送信用テキスト")
    assert "記入例" in wb.sheetnames  # example のある様式には記入例が付く
    assert ws.column_dimensions["H"].hidden


def _text(example) -> str:
    form = example.form("contact")
    keys = forms.text_keys(form)
    lines = [f"{forms.KEY_KIND}: contact", f"{forms.KEY_VER}: 1", "宛先: 総合窓口"]
    for key, v in FILLED.items():
        lines.append(f"{keys[f'f_{key}']}: {v}")
    return "\n".join(lines)


def test_text_roundtrip(example):
    inq = parse.parse_text(_text(example), example)
    assert inq is not None
    assert inq.staff.key == "general"
    assert inq.values["company"] == "株式会社○○製作所"


def test_text_is_lenient(example):
    body = _text(example)
    # 返信の引用・全角コロン・前後の空白に耐える
    quoted = "\n".join("> " + line.replace(": ", ": ", 1) for line in body.splitlines())
    inq = parse.parse_text(quoted, example)
    assert inq is not None
    assert inq.values["person"] == "山田 太郎"


def test_text_not_a_form(example):
    assert parse.parse_text("こんにちは。見積をお願いします。", example) is None


def test_text_unknown_kind(example):
    with pytest.raises(parse.Invalid):
        parse.parse_text("様式: nai\n様式版: 1\n", example)


def test_staff_prefix_match(example):
    body = _text(example).replace("宛先: 総合窓口", "宛先: 総合窓口(平日)")
    inq = parse.parse_text(body, example)
    assert inq is not None and inq.staff.key == "general"


def test_macro_js(example):
    js = forms.macro_js(example, example.form("contact"))
    assert "電話番号" in js  # 必須チェックが様式定義から生成される
    assert forms.TEXT_SHEET in js
    assert "Api.GetSheet" in js
