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
            "forms": ["forms/contact.adoc"],
        },
    }
    (root / "site.yaml").write_text(
        yaml.safe_dump(cfg, allow_unicode=True), encoding="utf-8"
    )
    (root / "forms").mkdir()
    (root / "forms" / "contact.adoc").write_text(
        "= お問い合わせ\n\nお名前:: [varchar(50), not null]\n備考:: [text]\n",
        encoding="utf-8",
    )
    return site_mod.load(root)
