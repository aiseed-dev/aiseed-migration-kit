"""決裁部品: 文書属性の解釈・インデックス SQL・凍結記録——DESIGN.md §14。"""

import subprocess

import yaml

from amig import decision

DOC = """\
= 出張旅費規程の一部改正
:approver: 山田 太郎
:status: 決裁済
:decided-on: 2026-07-01
// 事実的注記: 2026-06-30 の理事会で承認
:approver: 上書きされない(最初が勝つ)

本文。○○を改正する。

////
ブロックコメント内の :status: 下書き は読まれない
////
"""


def test_parse_attrs():
    title, attrs = decision.parse_attrs(DOC)
    assert title == "出張旅費規程の一部改正"
    assert attrs["approver"] == "山田 太郎"
    assert attrs["status"] == "決裁済"
    assert attrs["decided-on"] == "2026-07-01"


def test_comments_not_read():
    _, attrs = decision.parse_attrs(DOC)
    assert attrs["status"] == "決裁済"  # ブロックコメント内の「下書き」ではない


def test_scan_and_index_sql(tmp_path):
    (tmp_path / "kitei").mkdir()
    (tmp_path / "kitei" / "ryohi.adoc").write_text(DOC, encoding="utf-8")
    (tmp_path / "memo.adoc").write_text(
        "= 打合せ記録\n:status: 参考\n", encoding="utf-8"
    )
    docs = decision.scan(tmp_path)
    assert [d.path for d in docs] == ["kitei/ryohi.adoc", "memo.adoc"]

    sql = decision.index_sql(docs)
    assert 'create table if not exists "文書属性"' in sql
    assert 'delete from "文書属性";' in sql  # rebuild が正(増分更新なし)
    assert "'kitei/ryohi.adoc', 'approver', '山田 太郎'" in sql
    assert "'doctitle', '出張旅費規程の一部改正'" in sql
    assert sql.startswith("begin;") and sql.rstrip().endswith("commit;")


def test_index_sql_escapes_quotes(tmp_path):
    (tmp_path / "a.adoc").write_text(
        "= 題\n:note: It's a test\n", encoding="utf-8"
    )
    sql = decision.index_sql(decision.scan(tmp_path))
    assert "It''s a test" in sql


def test_freeze_without_git(tmp_path):
    pdf = tmp_path / "invoice.pdf"
    pdf.write_bytes(b"%PDF-1.7 fake")
    out = decision.freeze(pdf)
    rec = yaml.safe_load(out.read_text(encoding="utf-8"))
    assert rec["交付物"] == "invoice.pdf"
    assert len(rec["sha256"]) == 64
    assert "生成元コミット" not in rec


def test_freeze_with_git_source(tmp_path):
    src = tmp_path / "invoice.adoc"
    src.write_text("= 請求書\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", "add", "."],
        cwd=tmp_path,
        check=True,
    )
    subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", "x"],
        cwd=tmp_path,
        check=True,
    )
    pdf = tmp_path / "invoice.pdf"
    pdf.write_bytes(b"%PDF-1.7 fake")
    out = decision.freeze(pdf, source=src)
    rec = yaml.safe_load(out.read_text(encoding="utf-8"))
    assert rec["生成元"] == str(src)
    assert len(rec["生成元コミット"]) == 40
