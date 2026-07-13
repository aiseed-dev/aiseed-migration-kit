"""決裁部品(文書管理)——DESIGN.md §14。

決裁項目(承認者・状態・決裁日等)は**文書内の AsciiDoc 属性が正**
(``:approver:`` ``:status:`` ``:decided-on:`` 等)。PostgreSQL は全文書の
属性を集約した検索・一覧用インデックス(派生物)で、このモジュールが
出力する SQL でいつでも再生成できる(rebuild)。

決裁フローの実体は Forgejo のプルリクエスト(起案=ブランチ/審査=PR
レビュー/決裁=マージ/施行=build して配信)であり、キットはフローを
持たない——解釈と派生(インデックス・凍結記録)だけを担う。

属性の語彙は規格化しない(様式プロファイルの表示層と同じ考え方)。
どの属性を必須とするかは各機関の規約(文書側)の問題で、機械は
「書かれた属性を集める」ことだけを保証する。

交付物(請求書・修了証等)は PDF/A で凍結し、freeze() が交付物の
SHA-256 と生成元(AsciiDoc+コミットハッシュ)を並べた記録を作る
(§0「データの置き場」の交付の凍結)。PDF/A への変換自体は本モジュールの
範囲外(既存の帳票生成の出口で行う)。
"""

from __future__ import annotations

import hashlib
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

import yaml

from amig import adoc

# AsciiDoc の文書属性 ``:name: value``。名前の文字種は AsciiDoc の慣例
# (英数字・ハイフン・アンダースコア)に限る
_ATTR_RE = re.compile(r"^:([A-Za-z0-9][A-Za-z0-9_-]*):[ \t]*(.*)$")


class DecisionError(Exception):
    """決裁部品の入力不備(担当者向けの日本語メッセージ)。"""


@dataclass(frozen=True)
class Doc:
    """決裁文書1件の解釈結果(属性が正)。"""

    path: str  # インデックスの起点からの相対パス
    title: str
    attrs: dict[str, str]


def parse_attrs(text: str) -> tuple[str, dict[str, str]]:
    """文書から題名と属性を読む。

    属性は文書のどこにあっても拾う(慣例はヘッダー)が、同名の属性は
    最初のものが勝つ。コメント(``//``・``////``)の中は読まない
    (何がコメントかの定義は様式プロファイルと共通: amig.adoc)。
    """
    title = ""
    attrs: dict[str, str] = {}
    for line in adoc.strip_comments(text):
        m = _ATTR_RE.match(line)
        if m and m.group(1) not in attrs:
            attrs[m.group(1)] = m.group(2).strip()
        elif not title:
            t = adoc.TITLE_RE.match(line)
            if t:
                title = t.group(1)
    return title, attrs


def scan(root: Path) -> list[Doc]:
    """root 以下の .adoc を走査して属性を集める(パス順で決定的)。"""
    docs = []
    for path in sorted(root.rglob("*.adoc")):
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            raise DecisionError(
                f"{path} が UTF-8 で読めません(文字コードを確認してください)"
            ) from None
        title, attrs = parse_attrs(text)
        docs.append(Doc(path=str(path.relative_to(root)), title=title, attrs=attrs))
    return docs


def _q(s: str) -> str:
    """SQL 文字列リテラル(単一引用符をエスケープ)。"""
    return "'" + s.replace("'", "''") + "'"


TABLE = "文書属性"


def index_sql(docs: list[Doc]) -> str:
    """検索・一覧用インデックスを再生成する SQL(全削除+全挿入)。

    DB は派生物であり、この出力を流し直せばいつでも文書(git)から
    再構築できる。増分更新は作らない——rebuild が正。
    """
    out = [
        "begin;",
        f'create table if not exists "{TABLE}" (',
        '  "パス" text not null,',
        '  "属性" text not null,',
        '  "値" text not null,',
        '  primary key ("パス", "属性")',
        ");",
        f'delete from "{TABLE}";',
    ]
    for doc in docs:
        # 明示の :doctitle: 属性があればそちらが勝つ(属性が正。§14)
        rows = dict(doc.attrs)
        if doc.title:
            rows.setdefault("doctitle", doc.title)
        for name, value in rows.items():
            out.append(
                f'insert into "{TABLE}" ("パス", "属性", "値") '
                f"values ({_q(doc.path)}, {_q(name)}, {_q(value)});"
            )
    out.append("commit;")
    return "\n".join(out) + "\n"


def _git(path: Path, *args: str) -> str:
    """path のディレクトリで git を実行し stdout を返す(失敗・ハングは空)。"""
    try:
        r = subprocess.run(
            ["git", *args, "--", path.name],
            cwd=path.parent,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return r.stdout.strip() if r.returncode == 0 else ""


def _git_commit(path: Path) -> str:
    """path を最後に変更したコミットのハッシュ(git 管理外なら空)。"""
    return _git(path, "log", "-n", "1", "--format=%H")


def _git_dirty(path: Path) -> bool:
    """path に未コミットの変更(未追跡含む)があるか。git 管理外は False。"""
    return bool(_git(path, "status", "--porcelain"))


def freeze(artifact: Path, source: Path | None = None) -> Path:
    """交付物の凍結記録(<交付物>.hash.yaml)を作り、そのパスを返す。

    記録: 交付物の SHA-256+生成元(AsciiDoc のパスとコミットハッシュ)。
    交付物そのもの(PDF/A)と並べて保存し、後から「この交付物はこの決裁
    文書のこの版から生成された」を検証できるようにする。

    凍結の名のとおり、既存の記録は上書きしない(convert が既存 .md を
    上書きしないのと同型の保護)。生成元に未コミットの変更がある場合も
    拒否する——記録するコミットの内容と実際の生成元が食い違い、検証の
    ための記録自体が誤証拠になるため(決裁=マージ済みの文書から交付する)。
    """
    out = artifact.with_name(artifact.name + ".hash.yaml")
    if out.exists():
        raise DecisionError(
            f"凍結記録 {out} が既にあります(凍結記録は上書きしません。"
            "作り直す場合は先に既存の記録を確認・退避してください)"
        )
    if source is not None:
        if not source.is_file():
            raise DecisionError(f"生成元 {source} がありません")
        if _git_dirty(source):
            raise DecisionError(
                f"生成元 {source} に未コミットの変更があります。"
                "コミット(決裁)してから凍結してください"
            )
    digest = hashlib.sha256(artifact.read_bytes()).hexdigest()
    record: dict[str, str] = {"交付物": artifact.name, "sha256": digest}
    if source is not None:
        record["生成元"] = str(source)
        commit = _git_commit(source)
        if commit:
            record["生成元コミット"] = commit
    out.write_text(
        yaml.safe_dump(record, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    return out
