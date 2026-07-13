"""実証: seminar-kit・mfg-kit の様式を定義ファイルの差し替えだけで表現する。

sites/seminar・sites/mfg にはコードが1行もない(site.yaml+様式プロファイル
のみ)。ここのテストが通ること自体が「同一基盤の様式定義差し替えのみで
成立する」(DESIGN.md §15)の実証になる。
"""

from pathlib import Path

import pytest

from amig import site as site_mod
from amig.inquiry import derive, forms, parse

SITES = Path(__file__).parent.parent / "sites"


@pytest.fixture
def seminar():
    return site_mod.load(SITES / "seminar")


@pytest.fixture
def mfg():
    return site_mod.load(SITES / "mfg")


# ---- seminar(研修・セミナー申込: 企業+受講者最大3名+参加場所) ----

SEMINAR_FILLED = {
    "企業・団体名": "株式会社○○製作所",
    "企業・団体名フリガナ": "カブシキガイシャマルマルセイサクショ",
    "ご担当者名": "山田 太郎",
    "ご担当者名フリガナ": "やまだ たろう",  # ひらがな → カナ制約への修復を実証
    "メールアドレス": "taro@example.co.jp",
    "郵便番号": "770-0000",
    "住所": "徳島県徳島市○○町1-1",
    "電話番号": "088-000-0000",
    "受講者1氏名": "山田 太郎",
    "受講者1フリガナ": "ヤマダ タロウ",
    "受講者1所属・役職": "製造部 課長",
    "受講者1メールアドレス": "taro@example.co.jp",
    "受講者1参加場所": "会場",
    # 受講者2・3は空欄(1名参加)——任意欄の省略が通ることの実証
}


def _text(form_key: str, staff: str, values: dict[str, str]) -> str:
    lines = [
        f"{forms.KEY_KIND}: {form_key}",
        f"{forms.KEY_VER}: {forms.FORM_VER}",
        f"宛先: {staff}",
    ]
    lines += [f"{label}: {v}" for label, v in values.items()]
    return "\n".join(lines)


def test_seminar_roundtrip_with_kana_repair(seminar):
    inq = parse.parse_text(_text("moshikomi", "事務局", SEMINAR_FILLED), seminar)
    assert inq is not None
    assert inq.staff.key == "jimukyoku"
    # ひらがなのフリガナはカナ制約への決定的な修復でカタカナになる
    assert inq.values["ご担当者名フリガナ"] == "ヤマダ タロウ"
    # 空欄の受講者2・3は values に含まれない
    assert "受講者2氏名" not in inq.values


def test_seminar_location_choices(seminar):
    values = dict(SEMINAR_FILLED, 受講者1参加場所="自宅")
    with pytest.raises(parse.Invalid) as e:
        parse.parse_text(_text("moshikomi", "事務局", values), seminar)
    assert any("受講者1参加場所" in s for s in e.value.issues)


def test_seminar_kanji_furigana_goes_invalid(seminar):
    """漢字のフリガナは修復できない(一意に導出不能)→ 検証落ち。"""
    values = dict(SEMINAR_FILLED, ご担当者名フリガナ="山田")
    with pytest.raises(parse.Invalid) as e:
        parse.parse_text(_text("moshikomi", "事務局", values), seminar)
    assert any("フリガナ" in s for s in e.value.issues)


def test_seminar_xlsx_and_ddl(seminar):
    form = seminar.form("moshikomi")
    wb = forms.build(seminar, form, "seminar@example.jp")
    ws = wb[forms.SHEET]
    formulas = [dv.formula1 for dv in ws.data_validations.dataValidation]
    assert any("サテライト" in f for f in formulas)  # 参加場所ドロップダウン
    ddl = derive.ddl(form)
    assert 'create table "受講申込"' in ddl
    assert "check (受講者1参加場所 in ('会場', 'ZOOM', 'サテライト'))" in ddl


# ---- mfg(見積依頼: 明細は1行1明細の行形式) ----

MFG_FILLED = {
    "会社名": "株式会社△△工業",
    "ご担当者名": "佐藤 花子",
    "メールアドレス": "hanako@example.co.jp",
    "電話番号": "088-111-1111",
    "明細": "P-1024 2 黒色・座面高さ45cm",
    "希望納期": "令和8年9月30日",  # 和暦 → ISO の修復を実証
}


def test_mfg_roundtrip_with_wareki_repair(mfg):
    inq = parse.parse_text(_text("mitsumori", "営業", MFG_FILLED), mfg)
    assert inq is not None
    assert inq.staff.key == "eigyo"
    assert inq.values["希望納期"] == "2026-09-30"


def test_mfg_ddl_and_filltext(mfg):
    form = mfg.form("mitsumori")
    ddl = derive.ddl(form)
    assert 'create table "見積依頼"' in ddl
    assert '"希望納期" date' in ddl
    text = derive.filltext(mfg, form, "mitsumori@example.jp")
    assert "1行に1件" in text  # 明細の記入案内(自由記述)が配布物に載る


def test_no_code_in_showcase_sites():
    """実証サイトには定義ファイルしかない(rules.py 等のコードなし)。"""
    for name in ("seminar", "mfg"):
        files = [p for p in (SITES / name).rglob("*") if p.is_file()]
        assert all(p.suffix in (".yaml", ".adoc") for p in files), files
