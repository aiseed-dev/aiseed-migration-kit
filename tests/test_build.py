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


def _one_page(tmp_site):
    tmp_site.content.mkdir(parents=True)
    (tmp_site.content / "a.md").write_text(
        "---\ntitle: a\n---\n\nhon bun\n", encoding="utf-8"
    )


def test_forms_published(tmp_site):
    """forms-out/ の様式は build で dist/forms/ に公開される(§5)。"""
    _one_page(tmp_site)
    tmp_site.forms_out.mkdir(parents=True)
    (tmp_site.forms_out / "contact.xlsx").write_bytes(b"dummy")
    build_mod.build(tmp_site)
    assert (tmp_site.dist / "forms" / "contact.xlsx").read_bytes() == b"dummy"


def test_forms_not_published_when_disabled(tmp_site):
    tmp_site.cfg["inquiry"]["publish_forms"] = False
    _one_page(tmp_site)
    tmp_site.forms_out.mkdir(parents=True)
    (tmp_site.forms_out / "contact.xlsx").write_bytes(b"dummy")
    build_mod.build(tmp_site)
    assert not (tmp_site.dist / "forms").exists()


def test_public_passthrough(tmp_site):
    _one_page(tmp_site)
    tmp_site.public.mkdir(parents=True)
    (tmp_site.public / "_redirects").write_text("/old /a.html 301\n", encoding="utf-8")
    build_mod.build(tmp_site)
    assert (tmp_site.dist / "_redirects").exists()
    assert (tmp_site.dist / "a.html").exists()


def test_adoc_article_renders_via_pyasciidoc(tmp_site):
    """.adoc記事もfrontmatter+本文で.mdと同じ扱い。強調はCJK対応。"""
    pytest.importorskip("pyasciidoc")
    tmp_site.content.mkdir(parents=True)
    (tmp_site.content / "b.adoc").write_text(
        "---\ntitle: b\n---\n\nこれは*重要*なお知らせです。\n", encoding="utf-8"
    )
    build_mod.build(tmp_site)
    page = (tmp_site.dist / "b.html").read_text(encoding="utf-8")
    assert "<strong>重要</strong>" in page


def test_adoc_admonition_has_bundled_css(tmp_site):
    """pyasciidocのadmonition(NOTE:等)markupに対応するCSSが同梱されている。"""
    pytest.importorskip("pyasciidoc")
    tmp_site.content.mkdir(parents=True)
    (tmp_site.content / "b.adoc").write_text(
        "---\ntitle: b\n---\n\nNOTE: 補足です。\n", encoding="utf-8"
    )
    build_mod.build(tmp_site)
    page = (tmp_site.dist / "b.html").read_text(encoding="utf-8")
    assert '<div class="admonition note">' in page
    css = (tmp_site.dist / "style.css").read_text(encoding="utf-8")
    assert ".admonition" in css and ".admonition.note" in css


def test_adoc_without_pyasciidoc_raises_clear_error(tmp_site, monkeypatch):
    """pyasciidoc未導入で.adocを使うと分かりやすいエラーになる(黙って
    フォールバックしない ── .mdと違い代替のレンダラが無いため)。"""
    import builtins

    real_import = builtins.__import__

    def fake_import(name, *a, **kw):
        if name == "pyasciidoc":
            raise ImportError("pyasciidoc not installed (test)")
        return real_import(name, *a, **kw)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    tmp_site.content.mkdir(parents=True)
    (tmp_site.content / "b.adoc").write_text(
        "---\ntitle: b\n---\n\n本文。\n", encoding="utf-8"
    )
    with pytest.raises(build_mod.BuildError, match="pyasciidoc"):
        build_mod.build(tmp_site)
