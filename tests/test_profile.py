"""様式プロファイル(AsciiDoc)の解釈——DESIGN.md §5。"""

import pytest

from amig.inquiry import profile

CONTACT = """\
= 問い合わせ

問い合わせ全般を受け付けます。

事業者名:: [varchar(100), not null]
担当者名:: [varchar(50), not null]
メールアドレス:: [varchar(255), not null, check (メールアドレス ~ '^[^@]+@[^@]+$')]
内容:: [text, not null]
  自由にお書きください。複数行可。
"""


def test_title_and_intro():
    p = profile.parse(CONTACT)
    assert p.title == "問い合わせ"
    assert "問い合わせ全般" in p.intro


def test_fields_and_constraints():
    p = profile.parse(CONTACT)
    assert [f.label for f in p.fields] == [
        "事業者名",
        "担当者名",
        "メールアドレス",
        "内容",
    ]
    assert p.field("事業者名").constraint == "varchar(100), not null"
    assert "check" in p.field("メールアドレス").constraint


def test_note_is_free_text_continuation():
    p = profile.parse(CONTACT)
    assert p.field("内容").note == "自由にお書きください。複数行可。"
    assert p.field("事業者名").note == ""


def test_comments_are_ignored():
    text = CONTACT + "\n// これは注記であり出力に現れない\n"
    p = profile.parse(text)
    assert "注記" not in repr(p)


def test_block_comment_is_ignored():
    text = (
        "= 問い合わせ\n\n"
        "////\n複数行のブロックコメント\nここも消える\n////\n\n"
        "内容:: [text, not null]\n"
    )
    p = profile.parse(text)
    assert p.field("内容").constraint == "text, not null"


def test_missing_title_raises():
    with pytest.raises(profile.ProfileError, match="見出し"):
        profile.parse("内容:: [text, not null]\n")


def test_missing_bracket_raises():
    text = "= 問い合わせ\n\n内容:: 自由記述だけで角括弧が無い\n"
    with pytest.raises(profile.ProfileError, match="制約"):
        profile.parse(text)


def test_no_fields_raises():
    with pytest.raises(profile.ProfileError, match="項目"):
        profile.parse("= 問い合わせ\n\nただの説明文だけ。\n")


def test_inline_note_after_bracket():
    text = "= 様式\n\n件数:: [integer, not null] 半角数字で入力\n"
    p = profile.parse(text)
    assert p.field("件数").note == "半角数字で入力"
