"""様式の読み取り(全経路共通のパーサ)。

入口は2つ、検証は1つ:
  parse_xlsx(src, site)  xlsx(添付)。名前付きセルで読む
  parse_text(body, site) メール本文の送信用テキスト(`項目: 値`。様式内蔵の
                         マクロ・数式が生成する。厳密な YAML ではなく
                         行単位で寛容に読む——メールソフトの改行に耐える)
どちらも同じ検証(_assemble)に合流する。処理順は DESIGN.md §5「受信
パーサーの寛容性」のとおり: NFKC 正規化+修復(spec.repair。制約から
導出できる決定的な変換のみ)→ 制約検証(様式プロファイル由来の Pydantic
モデル)。不備は Invalid(issues つき。申込者向けの修正依頼文にそのまま
使える日本語)を送出する——呼び出し側(mailin)が pending へ落とす。
"""

import io
import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

from openpyxl import load_workbook
from openpyxl.workbook import Workbook
from pydantic import ValidationError

from amig.inquiry import derive, forms, spec
from amig.site import FormDef, Site, Staff


@dataclass(frozen=True)
class Inquiry:
    """様式から読み取った1件分(検証済み)。

    values は項目ラベル(=DDL の列名)→ 修復済みの値。修復(NFKC・和暦
    → ISO 等)が入っても原文はメール原文(IMAP)が証跡として残る。
    """

    form: FormDef
    staff: Staff
    values: dict[str, str]  # 項目ラベル → 値(未記入の任意欄は含まない)


class Invalid(Exception):
    """様式の不備。issues は申込者向けの修正依頼文にそのまま使える日本語。"""

    def __init__(self, issues: list[str]):
        self.issues = issues
        super().__init__(" / ".join(issues))


_NOT_A_FORM = "様式のファイルではないようです。ご案内した様式をご利用ください。"


def _assemble(value_of: Callable[[str], str | None], site: Site) -> Inquiry:
    """定義名→値の関数から Inquiry を組み立てる(xlsx・本文テキスト共通)。"""
    kind = value_of("form_kind")
    raw_ver = value_of("form_ver")
    if kind is None or raw_ver is None:
        raise Invalid([_NOT_A_FORM])
    form = next((f for f in site.forms if f.key == kind), None)
    if form is None:
        raise Invalid([_NOT_A_FORM])
    if not raw_ver.isdigit() or int(raw_ver) != forms.FORM_VER:
        raise Invalid(
            [
                f"様式の版が異なります(お手元の版: {raw_ver})。"
                "お手数ですが最新の様式をご利用ください。"
            ]
        )

    issues: list[str] = []
    staff = _match_staff(value_of("staff"), site.staff)
    if staff is None:
        issues.append(f"「{forms.STAFF_LABEL}」が選ばれていません")

    values: dict[str, str] = {}
    for f in form.fields:
        v = value_of(f"f_{f.key}")
        if v is not None:
            values[f.label] = spec.repair(f.spec, v)
        elif f.required:
            issues.append(f"「{f.label}」が未記入です")

    issues += _validate(form, values)
    if issues:
        raise Invalid(issues)
    assert staff is not None
    return Inquiry(form=form, staff=staff, values=values)


_ERR_JA = {
    "string_too_long": "文字数が多すぎます",
    "literal_error": "選択肢のいずれかを記入してください",
    "int_parsing": "半角数字で記入してください",
    "int_type": "半角数字で記入してください",
    "greater_than_equal": "範囲より小さい値です",
    "less_than_equal": "範囲より大きい値です",
    "date_from_datetime_parsing": "日付の形式で記入してください(例: 2026-07-13)",
    "date_parsing": "日付の形式で記入してください(例: 2026-07-13)",
    "string_pattern_mismatch": "形式が正しくないようです",
}


def _validate(form: FormDef, values: dict[str, str]) -> list[str]:
    """制約検証(様式プロファイルから派生した Pydantic モデル。正の検証)。

    未記入(required)は呼び出し側が先に検出しているため、ここでは
    記入された値の制約違反だけを日本語の issue にする。
    """
    try:
        derive.model(form).model_validate(values)
    except ValidationError as e:
        key_to_label = {f.key: f.label for f in form.fields}
        issues = []
        for err in e.errors():
            loc = str(err["loc"][0]) if err["loc"] else ""
            label = key_to_label.get(loc, loc)
            if label not in values:
                continue  # 未記入は上で報告済み
            reason = _ERR_JA.get(err["type"], "記入内容をご確認ください")
            issues.append(f"「{label}」: {reason}")
        return issues
    return []


def _match_staff(label: str | None, staff: tuple[Staff, ...]) -> Staff | None:
    """表示ラベル → 担当(前方一致まで許す。判別できなければ None)。"""
    if label is None:
        return None
    s = label.strip()
    for st in staff:
        if s == st.label:
            return st
    for st in staff:
        if s.startswith(st.label) or st.label.startswith(s):
            return st
    return None


def _value(wb: Workbook, name: str) -> str | None:
    """定義名のセル値を文字列で返す(空・空白のみは None)。"""
    dn = wb.defined_names.get(name)
    if dn is None:
        return None
    try:
        ((title, coord),) = dn.destinations
    except ValueError:
        return None
    v = wb[title][coord].value
    if v is None:
        return None
    s = str(v).strip()
    return s or None


def parse_xlsx(src: bytes | BinaryIO | Path, site: Site) -> Inquiry:
    """様式 xlsx を読み取る。様式でない・不備のときは Invalid を送出。"""
    if isinstance(src, bytes):
        src = io.BytesIO(src)
    try:
        wb = load_workbook(src, data_only=True)
    except Exception:  # 壊れた zip・xlsx 以外など、開けないものはすべて「様式でない」
        raise Invalid([_NOT_A_FORM]) from None
    return _assemble(lambda name: _value(wb, name), site)


_LINE_RE = re.compile(r"^\s*([^::]{1,20})[::]\s*(.*?)\s*$")


def parse_text(body: str, site: Site) -> Inquiry | None:
    """メール本文の送信用テキストを読み取る。

    「様式:」行が無ければ送信用テキストではない → None(呼び出し側は
    未処理へ流す)。あれば xlsx と同じ検証を通し、不備は Invalid。
    行単位の `項目: 値` を寛容に読む(前後の空白・引用符・全角コロン可)。
    """
    fields: dict[str, str] = {}
    for line in body.splitlines():
        line = line.lstrip('> "')  # 返信の引用・単一セル貼り付けの引用符に耐える
        m = _LINE_RE.match(line)
        if not m:
            continue
        key, value = m.group(1).strip(), m.group(2).strip()
        if key and value and key not in fields:
            fields[key] = value

    kind = fields.get(forms.KEY_KIND)
    if kind is None:
        return None
    form = next((f for f in site.forms if f.key == kind), None)
    keys = forms.text_keys(form) if form else {
        "form_kind": forms.KEY_KIND,
        "form_ver": forms.KEY_VER,
    }

    def value_of(name: str) -> str | None:
        label = keys.get(name)
        if label is None:
            return None
        return fields.get(label) or None

    return _assemble(value_of, site)
