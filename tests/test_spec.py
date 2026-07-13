"""SQL 語彙(型・制約)の解釈と、制約から導出する修復。"""

import pytest

from amig.inquiry import spec


def test_varchar_not_null():
    sp = spec.parse("お名前", "varchar(50), not null")
    assert sp.type == "varchar" and sp.length == 50 and sp.required


def test_text_optional():
    sp = spec.parse("備考", "text")
    assert sp.type == "text" and not sp.required


def test_check_in():
    sp = spec.parse("区分", "varchar(10), check (区分 in ('A', 'B''C'))")
    assert sp.choices == ("A", "B'C")


def test_check_between():
    sp = spec.parse("人数", "integer, not null, check (人数 between 1 and 20)")
    assert sp.between == (1, 20) and sp.required


def test_check_regex():
    sp = spec.parse("メール", "varchar(255), check (メール ~ '^[^@]+@[^@]+$')")
    assert sp.pattern == "^[^@]+@[^@]+$"


def test_references():
    sp = spec.parse("事業者番号", "integer, references 事業者(番号)")
    assert sp.references == "事業者(番号)"


def test_unknown_type_rejected():
    with pytest.raises(spec.SpecError, match="型"):
        spec.parse("x", "string, not null")


def test_unknown_constraint_rejected():
    with pytest.raises(spec.SpecError, match="語彙"):
        spec.parse("x", "text, unique")


def test_check_subject_mismatch_rejected():
    with pytest.raises(spec.SpecError, match="一致しません"):
        spec.parse("人数", "integer, check (参加人数 between 1 and 20)")


def test_repair_nfkc():
    sp = spec.parse("x", "varchar(50)")
    assert spec.repair(sp, " ABC123 ") == "ABC123"


def test_repair_date_variants():
    sp = spec.parse("x", "date")
    assert spec.repair(sp, "2026/8/1") == "2026-08-01"
    assert spec.repair(sp, "2026年8月1日") == "2026-08-01"
    assert spec.repair(sp, "令和元年5月1日") == "2019-05-01"
    assert spec.repair(sp, "平成30年1月8日") == "2018-01-08"
    # 解釈できないものは修復しない(検証で落ちて pending へ)
    assert spec.repair(sp, "来月あたま") == "来月あたま"


def test_repair_kana_from_pattern():
    sp = spec.parse("フリガナ", "varchar(50), check (フリガナ ~ '^[ァ-ヶー ]+$')")
    assert spec.repair(sp, "やまだ たろう") == "ヤマダ タロウ"
    # 漢字は一意に導出できないので修復しない
    assert spec.repair(sp, "山田") == "山田"
