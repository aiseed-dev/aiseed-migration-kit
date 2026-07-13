"""SQL 語彙(型・制約)の解釈と、制約から導出する修復——DESIGN.md §5。

様式プロファイルの角括弧の中身(``varchar(50), not null`` 等)を解釈する。
語彙は SQL そのもので、独自の型体系を発明しない。語彙は閉じている——
ここに列挙した形以外は SpecError で弾く(黙って読み飛ばすと DDL・検証・
xlsx の間で解釈がずれるため。「機械が保証すべきものは決定的に」)。

受け付ける語彙(v0):
  型:    varchar(N) / text / integer / date
  制約:  not null
         check (<項目> in ('a', 'b', ...))      → 選択肢(xlsx ではドロップダウン)
         check (<項目> between A and B)          → 数値範囲
         check (<項目> ~ '<正規表現>')           → 形式(メールアドレス等)
         references <テーブル>(<列>)             → 外部キー(DDL にそのまま出す。
                                                    受付側では検証しない)

修復(repair)は正規化の独立レイヤーではなく、制約から一意に導出できる
決定的な変換だけを行う(制約の随伴物):
  すべて     → NFKC(全角英数字→半角・NBSP→空白 等)+前後の空白除去
  integer    → NFKC で全角数字が半角になる(それ以上はしない)
  date       → 和暦(令和・平成・昭和)・区切り揺れ(/・.・年月日)を ISO へ
  カナ形式   → check の正規表現がカタカナ範囲なら ひらがな→カタカナ
一意に導出できない入力(カナ欄に漢字等)は修復せず、検証で落として
pending へ(呼び出し側)。修復が入っても原文はメール原文(IMAP)が証跡。
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

__all__ = ["FieldSpec", "SpecError", "column_sql", "parse", "repair"]


class SpecError(Exception):
    """制約の語彙にない表記。メッセージは様式作成者向けの日本語。"""


@dataclass(frozen=True)
class FieldSpec:
    """1項目分の型・制約(解釈済み)。sql は原文(DDL にそのまま出す)。"""

    sql: str
    type: str  # "varchar" | "text" | "integer" | "date"
    length: int | None = None  # varchar(N) の N
    required: bool = False  # not null
    choices: tuple[str, ...] = ()  # check (X in (...))
    between: tuple[int, int] | None = None  # check (X between A and B)
    pattern: str | None = None  # check (X ~ '...')
    references: str | None = None  # references テーブル(列)


_TYPE_RE = re.compile(r"^(varchar)\s*\(\s*(\d+)\s*\)$|^(text|integer|date)$", re.I)
_CHECK_RE = re.compile(r"^check\s*\((.+)\)$", re.I | re.S)
_IN_RE = re.compile(r"^(.+?)\s+in\s*\((.+)\)$", re.I | re.S)
_BETWEEN_RE = re.compile(r"^(.+?)\s+between\s+(-?\d+)\s+and\s+(-?\d+)$", re.I)
_REGEX_RE = re.compile(r"^(.+?)\s*~\s*'(.+)'$", re.S)
_REFERENCES_RE = re.compile(r"^references\s+(.+)$", re.I)
_QUOTED_RE = re.compile(r"'((?:[^']|'')*)'")


def _split_top(s: str) -> list[str]:
    """トップレベルのカンマで分割(括弧・引用符の中は割らない)。"""
    parts, buf, depth, in_q = [], [], 0, False
    for ch in s:
        if in_q:
            buf.append(ch)
            if ch == "'":
                in_q = False
            continue
        if ch == "'":
            in_q = True
            buf.append(ch)
        elif ch == "(":
            depth += 1
            buf.append(ch)
        elif ch == ")":
            depth -= 1
            buf.append(ch)
        elif ch == "," and depth == 0:
            parts.append("".join(buf).strip())
            buf = []
        else:
            buf.append(ch)
    if buf:
        parts.append("".join(buf).strip())
    return [p for p in parts if p]


def parse(label: str, constraint: str) -> FieldSpec:
    """制約文字列(角括弧の中身)を FieldSpec に解釈する。

    check の中の項目名は label と一致していること(SQL としてそのまま
    DDL に出るため、別名だと列と食い違う)。
    """
    parts = _split_top(constraint)
    if not parts:
        raise SpecError(f"項目「{label}」の制約が空です")

    m = _TYPE_RE.match(parts[0])
    if not m:
        raise SpecError(
            f"項目「{label}」の型「{parts[0]}」は使えません"
            "(varchar(N) / text / integer / date のいずれか)"
        )
    type_ = (m.group(1) or m.group(3)).lower()
    length = int(m.group(2)) if m.group(2) else None

    required = False
    choices: tuple[str, ...] = ()
    between: tuple[int, int] | None = None
    pattern: str | None = None
    references: str | None = None

    for part in parts[1:]:
        low = re.sub(r"\s+", " ", part.strip().lower())
        if low == "not null":
            required = True
            continue
        rm = _REFERENCES_RE.match(part)
        if rm:
            references = rm.group(1).strip()
            continue
        cm = _CHECK_RE.match(part)
        if not cm:
            raise SpecError(
                f"項目「{label}」の制約「{part}」は語彙にありません"
                "(not null / check (...) / references ...)"
            )
        expr = cm.group(1).strip()
        im = _IN_RE.match(expr)
        bm = _BETWEEN_RE.match(expr)
        xm = _REGEX_RE.match(expr)
        if im:
            _check_subject(label, im.group(1))
            choices = tuple(
                q.replace("''", "'") for q in _QUOTED_RE.findall(im.group(2))
            )
            if not choices:
                raise SpecError(f"項目「{label}」の in (...) に選択肢がありません")
        elif bm:
            _check_subject(label, bm.group(1))
            between = (int(bm.group(2)), int(bm.group(3)))
        elif xm:
            _check_subject(label, xm.group(1))
            pattern = xm.group(2).replace("''", "'")
        else:
            raise SpecError(
                f"項目「{label}」の check「{expr}」は語彙にありません"
                "(in (...) / between A and B / ~ '正規表現')"
            )

    return FieldSpec(
        sql=constraint.strip(),
        type=type_,
        length=length,
        required=required,
        choices=choices,
        between=between,
        pattern=pattern,
        references=references,
    )


def _check_subject(label: str, subject: str) -> None:
    if subject.strip().strip('"') != label:
        raise SpecError(
            f"項目「{label}」の check の対象「{subject.strip()}」が項目名と"
            "一致しません(DDL の列名と食い違うため)"
        )


def column_sql(sp: FieldSpec) -> str:
    """DDL の列定義部分。角括弧内のカンマは列挙の区切り(SQL のカンマでは
    ない)ため、空白で結合して SQL にする。"""
    return " ".join(_split_top(sp.sql))


# ---------------------------------------------------------------------------
# 修復(制約タイプ → 決定的な変換の対応表)
# ---------------------------------------------------------------------------

_ERA_RE = re.compile(r"^(令和|平成|昭和)\s*(元|\d{1,2})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日?$")
_ERA_BASE = {"令和": 2018, "平成": 1988, "昭和": 1925}
_DATE_RE = re.compile(r"^(\d{4})\s*[-/.年]\s*(\d{1,2})\s*[-/.月]\s*(\d{1,2})\s*日?$")
_KATAKANA_CLASS_RE = re.compile(r"[ァ-ヶ]-[ァ-ヶ]|カタカナ")


def _hira_to_kata(s: str) -> str:
    return "".join(
        chr(ord(ch) + 0x60) if "ぁ" <= ch <= "ゖ" else ch for ch in s
    )


def repair(sp: FieldSpec, value: str) -> str:
    """制約から一意に導出できる修復だけを行う(できない入力はそのまま返し、
    検証で落ちて pending へ行く)。"""
    v = unicodedata.normalize("NFKC", value).strip()
    if sp.type == "date":
        m = _ERA_RE.match(v)
        if m:
            year = _ERA_BASE[m.group(1)] + (1 if m.group(2) == "元" else int(m.group(2)))
            return f"{year:04d}-{int(m.group(3)):02d}-{int(m.group(4)):02d}"
        m = _DATE_RE.match(v)
        if m:
            return f"{int(m.group(1)):04d}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
        return v
    if sp.pattern and _KATAKANA_CLASS_RE.search(sp.pattern):
        return _hira_to_kata(v)
    return v
