"""派生生成(DDL → Pydantic → 記入テキスト)——DESIGN.md §5。"""

import datetime

import pytest
from pydantic import ValidationError

from amig.inquiry import derive


def test_ddl_is_nearly_identity(example):
    ddl = derive.ddl(example.form("shiken"))
    assert 'create table "依頼試験申込"' in ddl
    assert '"数量" integer not null check (数量 between 1 and 100)' in ddl
    assert '"番号" varchar(16) primary key' in ddl  # 既存規約: 年+連番
    assert '"受信日時" timestamptz not null' in ddl


def test_model_validates(example):
    m = derive.model(example.form("shiken"))
    ok = m.model_validate(
        {
            "企業・団体名": "株式会社○○",
            "ご担当者名": "山田",
            "電話番号": "088-000-0000",
            "メールアドレス": "a@b.jp",
            "試験項目": "引張試験",
            "希望日": "2026-08-01",
            "数量": "3",
        }
    )
    dump = ok.model_dump()
    assert dump["c5"] == "引張試験"  # c5=試験項目(プロファイルの5番目)
    assert isinstance(dump["c6"], datetime.date)  # c6=希望日


def test_model_rejects_out_of_range(example):
    m = derive.model(example.form("shiken"))
    with pytest.raises(ValidationError):
        m.model_validate(
            {
                "企業・団体名": "x",
                "ご担当者名": "x",
                "電話番号": "1",
                "メールアドレス": "a@b.jp",
                "試験項目": "引張試験",
                "数量": "101",
            }
        )


def test_filltext_roundtrip(example):
    """記入用テキストに記入したものが寛容パーサで読める(往復)。"""
    from amig.inquiry import parse

    form = example.form("contact")
    text = derive.filltext(example, form, "uketsuke@example.jp")
    assert "様式: contact" in text
    assert "uketsuke@example.jp" in text
    filled = []
    answers = {
        "宛先": "総合窓口",
        "ご所属(企業・団体名)": "株式会社○○",
        "お名前": "山田 太郎",
        "電話番号": "088-000-0000",
        "メールアドレス": "a@b.jp",
        "ご用件": "相談したい",
    }
    for line in text.splitlines():
        for label, v in answers.items():
            if line == f"{label}: ":
                line = f"{label}: {v}"
        filled.append(line)
    inq = parse.parse_text("\n".join(filled), example)
    assert inq is not None
    assert inq.values["お名前"] == "山田 太郎"


def test_filltext_guidance_lines_are_ignored_by_parser(example):
    """※の案内行(選択肢・補足)はパーサに拾われない。"""
    form = example.form("shiken")
    text = derive.filltext(example, form, "uketsuke@example.jp")
    assert "※引張試験 / 硬さ試験 / 成分分析 のいずれか" in text
    for line in text.splitlines():
        if line.startswith("※"):
            assert ":" not in line.split("※")[1][:20] or "例" in line


def test_prompt_lists_constraints(example):
    """AI 用プロンプト(派生5点目)は制約を機械的に列挙し、提案に徹する。"""
    form = example.form("shiken")
    p = derive.prompt(form)
    assert "試験項目(必須): [varchar(50), not null, check (試験項目 in ('引張試験', '硬さ試験', '成分分析'))]" in p
    assert "備考(任意): [text]" in p
    assert "提案" in p  # 登録ではなく提案であることが明記される
    assert derive.PROMPT_BODY_MARK in p
    assert derive.PROMPT_BODY_MARK not in derive.prompt(form, "本文テキスト")
