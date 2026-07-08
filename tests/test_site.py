"""site.yaml の読み込みと検証。"""

import pytest
import yaml

from cmsmig import site as site_mod


def test_example_loads(example):
    assert example.title.startswith("○○産業支援センター")
    assert list(example.categories) == ["news", "guide"]
    assert [s.key for s in example.staff] == ["general", "shiken", "setsubi"]
    assert example.staff[0].folder == "staff/general"
    assert {f.key for f in example.forms} == {"contact", "shiken"}
    assert example.form("contact").field("person").required


def _write(tmp_path, cfg):
    root = tmp_path / "x"
    root.mkdir()
    (root / "site.yaml").write_text(
        yaml.safe_dump(cfg, allow_unicode=True), encoding="utf-8"
    )
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


def test_colon_in_label(tmp_path):
    cfg = {
        "title": "t",
        "inquiry": {
            "forms": [
                {
                    "key": "a",
                    "label": "様式",
                    "fields": [{"key": "x", "label": "用件:内容"}],
                }
            ]
        },
    }
    with pytest.raises(site_mod.SiteError, match="コロン"):
        site_mod.load(_write(tmp_path, cfg))


def test_missing_site_yaml(tmp_path):
    with pytest.raises(site_mod.SiteError, match="site.yaml"):
        site_mod.load(tmp_path)
