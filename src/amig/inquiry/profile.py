"""様式プロファイル(AsciiDoc)の解釈——DESIGN.md §5「様式プロファイル」。

様式の唯一の定義は AsciiDoc 文書(様式プロファイル)である。xlsx・記入用
テキスト・Pydantic モデル・DDL・AI 用プロンプトは、すべてここでの
解釈結果(FormProfile)から派生させる(派生生成の入口)。

構文は AsciiDoc のラベル付きリスト(``項目:: 値``)の範囲に閉じる。
独自の派生言語は作らない——標準の AsciiDoc プロセッサ(Asciidoctor 等)に
食わせても、角括弧の制約表記はただの文字列として無視され、「人間が読める
記入様式」としてレンダリングされる(DESIGN.md §5「構文の規律」の後方互換
保証)。

このモジュールは pyasciidoc(CJK 対応のレンダリング。別リポジトリ)とは
独立している——レンダリングとプロファイル解釈を分離する、という DESIGN.md
の方針どおり、markdown-it-py のトークン列には依存しない小さな行指向の
パーサとして実装する。

構文(最小):

    = 様式ラベル

    自由記述の説明(任意・案内文。表示層はここも含め AI/人が解釈する)。

    項目ラベル:: [型, 制約...]
      補足の自由記述(任意・複数行。表示用途のみで機械は読まない)。

- 角括弧の中身(制約)は SQL の語彙をそのまま書く(例:
  ``varchar(50), not null`` / ``date, not null`` /
  ``integer, check (参加人数 between 1 and 20)``)。型・制約の意味は
  このモジュールでは解釈しない——DDL・Pydantic 生成側の仕事(派生生成)
- 角括弧の外側・次の項目までの行は「note」(表示用の自由記述)として
  そのまま保持する。hint/placeholder のような表示用属性は導入しない
  (DESIGN.md §5「型と制約 = SQL 語彙」)
- ``//`` 行コメント・``////`` ブロックコメントは出力に現れない(pyasciidoc
  と同じ規約)
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from amig import adoc

# ラベルに ASCII コロンを含む行もここでは受理する(そのうえで site 側の
# 検査が「コロンは使えません」と音を立てて弾く。黙って項目が消えるより良い)
_LABEL_ITEM_RE = re.compile(r"^(\S.*?)::[ \t]*(.*)$")


def _split_bracket(rest: str) -> tuple[str, str] | None:
    """``[制約] 補足`` を (制約, 補足) に分ける。

    制約には SQL の引用符付き文字列(正規表現の ``[^@]`` 等)が入るため、
    単純な正規表現では閉じ括弧を取り違える。引用符の中の ``]`` は無視して
    対応する閉じ括弧を探す。
    """
    if not rest.startswith("["):
        return None
    depth, in_q = 1, False
    for i in range(1, len(rest)):
        ch = rest[i]
        if in_q:
            if ch == "'":
                in_q = False
            continue
        if ch == "'":
            in_q = True
        elif ch == "[":
            depth += 1
        elif ch == "]":
            depth -= 1
            if depth == 0:
                return rest[1:i].strip(), rest[i + 1 :].strip()
    return None


class ProfileError(Exception):
    """様式プロファイルの不備(担当者向けの日本語メッセージ)。"""


@dataclass(frozen=True)
class ProfileField:
    """様式プロファイルの記入項目1つ。"""

    label: str
    constraint: str  # 角括弧の中身(SQL 語彙。生文字列のまま)
    note: str = ""  # 表示用の自由記述(型・制約とは別。機械検証には使わない)


@dataclass(frozen=True)
class FormProfile:
    """様式プロファイル1件(AsciiDoc 文書の解釈結果)。"""

    title: str
    intro: str
    fields: tuple[ProfileField, ...]

    def field(self, label: str) -> ProfileField:
        for f in self.fields:
            if f.label == label:
                return f
        raise KeyError(label)


def parse(text: str) -> FormProfile:
    """様式プロファイル(AsciiDoc テキスト)を FormProfile に解釈する。

    不備(見出しが無い・項目に制約の角括弧が無い等)は ProfileError で
    知らせる——機械が保証すべき構造なので、黙って読み飛ばさない
    (DESIGN.md §0「機械が保証すべきものは決定的に」)。
    """
    lines = adoc.strip_comments(text)

    title = ""
    body_start = 0
    for idx, line in enumerate(lines):
        m = adoc.TITLE_RE.match(line)
        if m:
            title = m.group(1)
            body_start = idx + 1
            break
    if not title:
        raise ProfileError("様式プロファイルに見出し(= 様式ラベル)がありません")

    intro_lines: list[str] = []
    fields: list[ProfileField] = []

    i = body_start
    n = len(lines)
    while i < n:
        line = lines[i]
        if not line.strip():
            i += 1
            continue
        m = _LABEL_ITEM_RE.match(line)
        if not m:
            if fields:
                # 項目が始まった後の非項目行は直前の項目への継続として扱う
                fields[-1] = _append_note(fields[-1], line.strip())
            else:
                intro_lines.append(line.strip())
            i += 1
            continue

        label, rest = m.group(1).strip(), m.group(2).strip()
        bm = _split_bracket(rest)
        if bm is None:
            raise ProfileError(
                f"項目「{label}」に制約([型, 制約...])がありません"
                "(DESIGN.md §5「型と制約 = SQL 語彙」)"
            )
        constraint, note = bm
        fields.append(ProfileField(label=label, constraint=constraint, note=note))
        i += 1

    if not fields:
        raise ProfileError("様式プロファイルに項目(ラベル付きリスト)がありません")

    return FormProfile(
        title=title, intro="\n".join(intro_lines).strip(), fields=tuple(fields)
    )


def _append_note(f: ProfileField, line: str) -> ProfileField:
    note = f"{f.note}\n{line}".strip() if f.note else line
    return ProfileField(label=f.label, constraint=f.constraint, note=note)
