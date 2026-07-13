"""site.yaml の読み込みと検証(様式は forms/*.adoc の様式プロファイル)。"""

import pytest
import yaml

from amig import site as site_mod


def test_example_loads(example):
    assert example.title.startswith("○○産業支援センター")
    assert list(example.categories) == ["news", "guide"]
    assert [s.key for s in example.staff] == ["general", "shiken", "setsubi"]
    assert example.staff[0].folder == "staff/general"
    assert {f.key for f in example.forms} == {"contact", "shiken"}
    contact = example.form("contact")
    assert contact.label == "お問い合わせ"
    assert contact.field("お名前").required
    assert contact.field("お名前").spec.type == "varchar"


def test_example_shiken_constraints(example):
    shiken = example.form("shiken")
    assert shiken.field("試験項目").spec.choices == ("引張試験", "硬さ試験", "成分分析")
    assert shiken.field("数量").spec.between == (1, 100)
    assert shiken.field("希望日").spec.type == "date"
    assert not shiken.field("備考").required


def _write(tmp_path, cfg, forms: dict[str, str] | None = None):
    root = tmp_path / "x"
    root.mkdir()
    (root / "site.yaml").write_text(
        yaml.safe_dump(cfg, allow_unicode=True), encoding="utf-8"
    )
    for name, text in (forms or {}).items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return root


def test_missing_title(tmp_path):
    with pytest.raises(site_mod.SiteError, match="title"):
        site_mod.load(_write(tmp_path, {"lang": "ja"}))


def test_bad_staff_key(tmp_path):
    cfg = {
        "title": "t",
        "inquiry": {"staff": [{"key": "総合", "label": "総合窓口"}]},
    }
    with pytest.raises(site_mod.SiteError, match="英小文字"):
        site_mod.load(_write(tmp_path, cfg))


def test_missing_profile_file(tmp_path):
    cfg = {"title": "t", "inquiry": {"forms": ["forms/nai.adoc"]}}
    with pytest.raises(site_mod.SiteError, match="ありません"):
        site_mod.load(_write(tmp_path, cfg))


def test_colon_in_label(tmp_path):
    cfg = {"title": "t", "inquiry": {"forms": ["forms/a.adoc"]}}
    # ラベル内の全角コロン(U+FF1A)は送信用テキストの区切りと衝突する
    forms = {"forms/a.adoc": "= 様式\n\n用件：内容:: [text, not null]\n"}
    with pytest.raises(site_mod.SiteError, match="コロン"):
        site_mod.load(_write(tmp_path, cfg, forms))


def test_bad_constraint_vocabulary(tmp_path):
    cfg = {"title": "t", "inquiry": {"forms": ["forms/a.adoc"]}}
    forms = {"forms/a.adoc": "= 様式\n\nお名前:: [string, required]\n"}
    with pytest.raises(site_mod.SiteError, match="型"):
        site_mod.load(_write(tmp_path, cfg, forms))


def test_missing_site_yaml(tmp_path):
    with pytest.raises(site_mod.SiteError, match="site.yaml"):
        site_mod.load(tmp_path)


def test_label_too_long_rejected(tmp_path):
    """テキストチャネルで読み取れない長さ(20文字超)のラベルは弾く。"""
    cfg = {"title": "t", "inquiry": {"forms": ["forms/a.adoc"]}}
    label = "あ" * 21
    forms = {"forms/a.adoc": f"= 様式\n\n{label}:: [text, not null]\n"}
    with pytest.raises(site_mod.SiteError, match="長すぎます"):
        site_mod.load(_write(tmp_path, cfg, forms))


def test_reserved_label_rejected(tmp_path):
    """予約された項目名(受付・様式等)は識別子行と衝突するため弾く。"""
    cfg = {"title": "t", "inquiry": {"forms": ["forms/a.adoc"]}}
    forms = {"forms/a.adoc": "= 様式\n\n受付:: [text, not null]\n"}
    with pytest.raises(site_mod.SiteError, match="予約された"):
        site_mod.load(_write(tmp_path, cfg, forms))


def test_duplicate_label_rejected(tmp_path):
    cfg = {"title": "t", "inquiry": {"forms": ["forms/a.adoc"]}}
    forms = {
        "forms/a.adoc": "= 様式\n\n備考:: [text]\n備考:: [text]\n"
    }
    with pytest.raises(site_mod.SiteError, match="重複"):
        site_mod.load(_write(tmp_path, cfg, forms))


def test_ascii_colon_in_label_is_loud(tmp_path):
    """ASCII コロン入りラベルは黙って消えず、エラーになる(旧実装と同等)。"""
    cfg = {"title": "t", "inquiry": {"forms": ["forms/a.adoc"]}}
    forms = {"forms/a.adoc": "= 様式\n\n納期:目安:: [date]\n"}
    with pytest.raises(site_mod.SiteError, match="コロン"):
        site_mod.load(_write(tmp_path, cfg, forms))
