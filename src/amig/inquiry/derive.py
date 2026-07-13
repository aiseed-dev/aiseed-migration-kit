"""様式プロファイルからの派生生成——DESIGN.md §5「派生生成の順序」。

一つの様式定義(FormDef=プロファイルの解釈結果)から派生させる:
  ddl(form)       PostgreSQL DDL(CREATE TABLE)
  model(form)     Pydantic 検証モデル(受信パーサーが使う。正の検証)
  filltext(form)  記入用プレーンテキスト(画面コピペ・メール返信チャネル)
  prompt(form)    AI 用プロンプト(pending の解釈案。§7「AI の持ち場」)

xlsx(forms.py)・マクロ(forms.macro_js)も同じ FormDef から生成される。
制約(SQL 語彙)が最初に確定し、以降の派生はすべてそれに従属する——
どこかの層だけ検証が甘くなる食い違いを構造的に防ぐ。
"""

from __future__ import annotations

import datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field as PField, StringConstraints, create_model

from amig.site import FormDef, Site

# 既存規約: 申込番号は年+連番、insert 時採番。受信日時は証跡(IMAP)への
# 参照点。この2列はすべての様式テーブルに共通で付く。
_COMMON_COLS = [
    '"番号" varchar(16) primary key',
    '"受信日時" timestamptz not null',
]


def ddl(form: FormDef) -> str:
    """CREATE TABLE 文。列の型・制約はプロファイルの角括弧の中身から
    作る(SQL 語彙をそのまま使うため、派生はほぼ恒等変換——角括弧内の
    区切りカンマを空白にするだけ)。"""
    from amig.inquiry import spec

    cols = list(_COMMON_COLS)
    cols += [f'"{f.label}" {spec.column_sql(f.spec)}' for f in form.fields]
    body = ",\n  ".join(cols)
    return f'create table "{form.label}" (\n  {body}\n);\n'


def model(form: FormDef) -> type[BaseModel]:
    """Pydantic 検証モデル。項目名は Field.key(c1, c2, ...)、alias が
    日本語ラベル。検証はラベルの辞書を渡す: model(form).model_validate(値)。"""
    defs: dict[str, Any] = {}
    for f in form.fields:
        ann = _annotation(f.spec)
        if f.required:
            defs[f.key] = (ann, PField(alias=f.label))
        else:
            defs[f.key] = (ann | None, PField(default=None, alias=f.label))
    return create_model(f"Form_{form.key}", **defs)


def _annotation(sp) -> Any:
    if sp.choices:
        return Literal[sp.choices]
    if sp.type == "integer":
        if sp.between:
            return Annotated[int, PField(ge=sp.between[0], le=sp.between[1])]
        return int
    if sp.type == "date":
        return datetime.date
    if sp.type == "varchar":
        return Annotated[
            str,
            StringConstraints(
                min_length=1, max_length=sp.length, pattern=sp.pattern
            ),
        ]
    # text
    if sp.pattern:
        return Annotated[str, StringConstraints(min_length=1, pattern=sp.pattern)]
    return Annotated[str, StringConstraints(min_length=1)]


def filltext(site: Site, form: FormDef, submit_addr: str) -> str:
    """記入用プレーンテキスト(配布物)。画面からコピーして記入し、
    メール本文として送る。受信側は parse.parse_text(寛容パーサ)で読む。

    「: 」の右に記入する行だけがデータで、※で始まる案内行(コロンなし)は
    パーサに拾われない。
    """
    from amig.inquiry import forms

    lines = [
        f"{form.label}(記入用テキスト)",
        "",
        "下の「様式:」から最後までをメール本文に貼り付け、各行の「: 」の",
        f"右側に記入して {submit_addr} へ送信してください。",
        "※で始まる行は案内です(消しても構いません)。",
        "",
        f"{forms.KEY_KIND}: {form.key}",
        f"{forms.KEY_VER}: {forms.FORM_VER}",
        f"{forms.KEY_SITE}: {site.name}",
        f"{forms.KEY_STAFF}: ",
    ]
    if site.staff:
        labels = " / ".join(s.label for s in site.staff)
        lines.append(f"※{labels} のいずれかを記入")
    for f in form.fields:
        suffix = "" if f.required else "(任意)"
        lines.append(f"{f.label}: ")
        notes = [n for n in ([suffix] if suffix else [])]
        if f.spec.choices:
            notes.append(" / ".join(f.spec.choices) + " のいずれか")
        if f.note:
            notes.append(f.note.replace("\n", " "))
        if notes:
            lines.append("※" + " ".join(notes))
    lines.append("")
    return "\n".join(lines)


PROMPT_BODY_MARK = "(ここに受信メール本文を貼る)"


def prompt(form: FormDef, body: str | None = None) -> str:
    """pending 解釈用の AI プロンプト(派生物の5点目。§7「AI の持ち場」)。

    修復不能・検証落ちの受信本文から「解釈案」を作らせる。AI は提案まで
    ——出力は人が読む解釈案であり、登録(DB)には決して直接使わない。
    制約は様式プロファイルから機械的に列挙されるため、プロンプトと
    サーバー検証が食い違わない。body 省略時は貼り付け位置の目印を置く
    (`amig prompt` で出力して手元の LLM に使う用)。
    """
    lines = [
        f"あなたは受付事務の補助者です。様式「{form.label}」で送られたはずの",
        "メール本文が機械で読み取れませんでした。本文から各項目の値を推定し、",
        "解釈案を作ってください。",
        "",
        "ルール:",
        "- これは人間の担当者が確認するための提案です。推測で値を作らない",
        "- 本文に根拠のない項目は出力せず、末尾に「不明: 項目名」と列挙する",
        "- 出力は「項目名: 値」を1行1項目で。前置き・説明文は書かない",
        "- 各行の根拠(本文のどの部分か)を行末に # 根拠: … で添える",
        "",
        "項目と制約(値は制約を満たす形に正規化する):",
    ]
    for f in form.fields:
        req = "必須" if f.required else "任意"
        lines.append(f"- {f.label}({req}): [{f.spec.sql}]")
        if f.note:
            lines.append(f"  補足: {f.note.replace(chr(10), ' ')}")
    lines += ["", "受信本文:", body if body is not None else PROMPT_BODY_MARK, ""]
    return "\n".join(lines)
