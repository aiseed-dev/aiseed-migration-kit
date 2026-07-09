"""build: content/ と data/ から dist/ を生成する。"""

import pytest

from amig import build as build_mod


def test_build_example(example):
    n = build_mod.build(example)
    dist = example.dist
    assert n == 5 + 2 + 1  # 記事5 + 一覧2 + トップ
    assert (dist / "index.html").exists()
    assert (dist / "style.css").exists()
    assert (dist / "news" / "index.html").exists()
    assert (dist / "guide" / "shiken.html").exists()
    assert (dist / "access.html").exists()  # category なし=直下

    top = (dist / "index.html").read_text(encoding="utf-8")
    assert "○○産業支援センター" in top
    assert "夏季休業のお知らせ" in top  # 新着がトップに載る
    assert "@font-face" not in top and "fonts.googleapis" not in top

    page = (dist / "news" / "2026-06-02-setsubi-koukai.html").read_text(
        encoding="utf-8"
    )
    assert "<table>" in page  # MD の表が HTML の表になる
    assert "2026年6月2日" in page

    lst = (dist / "news" / "index.html").read_text(encoding="utf-8")
    assert lst.index("夏季休業") < lst.index("設備を更新")  # 新しい順


def test_unknown_category(tmp_site):
    tmp_site.content.mkdir(parents=True)
    (tmp_site.content / "x.md").write_text(
        "---\ntitle: t\ncategory: nashi\n---\n\nhon bun\n", encoding="utf-8"
    )
    with pytest.raises(build_mod.BuildError, match="nashi"):
        build_mod.build(tmp_site)


def test_unclosed_frontmatter(tmp_site):
    tmp_site.content.mkdir(parents=True)
    (tmp_site.content / "x.md").write_text("---\ntitle: t\n", encoding="utf-8")
    with pytest.raises(build_mod.BuildError, match="閉じ"):
        build_mod.build(tmp_site)


def test_public_passthrough(tmp_site):
    tmp_site.content.mkdir(parents=True)
    (tmp_site.content / "a.md").write_text(
        "---\ntitle: a\n---\n\nhon bun\n", encoding="utf-8"
    )
    tmp_site.public.mkdir(parents=True)
    (tmp_site.public / "_redirects").write_text("/old /a.html 301\n", encoding="utf-8")
    build_mod.build(tmp_site)
    assert (tmp_site.dist / "_redirects").exists()
    assert (tmp_site.dist / "a.html").exists()
