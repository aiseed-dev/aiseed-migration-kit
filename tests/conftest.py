from pathlib import Path

import pytest
import yaml

from amig import site as site_mod

EXAMPLE = Path(__file__).parent.parent / "sites" / "example"


@pytest.fixture
def example() -> site_mod.Site:
    """同梱の example サイト(読み取りのみで使う)。"""
    return site_mod.load(EXAMPLE)


@pytest.fixture
def tmp_site(tmp_path: Path) -> site_mod.Site:
    """変換・生成テスト用の使い捨てサイト。"""
    root = tmp_path / "s"
    root.mkdir()
    cfg = {
        "title": "テスト機関",
        "categories": {"news": "お知らせ"},
        "inquiry": {
            "address": "uketsuke@example.jp",
            "staff": [{"key": "general", "label": "総合窓口"}],
            "forms": [
                {
                    "key": "contact",
                    "label": "お問い合わせ",
                    "fields": [
                        {"key": "person", "label": "お名前", "required": True},
                        {"key": "note", "label": "備考"},
                    ],
                }
            ],
        },
    }
    (root / "site.yaml").write_text(
        yaml.safe_dump(cfg, allow_unicode=True), encoding="utf-8"
    )
    return site_mod.load(root)
