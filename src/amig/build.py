"""生成: content/(.md/.adoc)と data/(.yaml)から dist/ を作る(Jinja2)。

- 記事 .md/.adoc は frontmatter(title/date/category)+ 本文
- category が無い記事は content/ 直下の単独ページ(/<slug>.html)
- category ごとの一覧と、トップ(index.html)を生成する
- テンプレートはサイトの templates/ が優先、無い分はキット既定
- public/ はそのまま dist/ に写す(favicon・_redirects・_headers 等)

日本語の約物・改行の扱いは mdit-py-cjk-friendly があれば有効にする
(無くても動く。CJK 文中の強調と、和文ソフト改行の空白抑止が改善する)。
.adoc(AsciiDoc)は pyasciidoc があれば使える(見出し・CJK対応の強調のみの
v0スコープ。無ければ .adoc を書いた時点で分かりやすいエラーにする ──
.md と違い代替のレンダラが無いため、フォールバックできない)。
"""

from __future__ import annotations

import hashlib
import shutil
from dataclasses import dataclass, field
from datetime import date
from importlib.resources import files
from pathlib import Path
from typing import Any

import yaml
from jinja2 import ChoiceLoader, Environment, FileSystemLoader
from markdown_it import MarkdownIt

from amig.site import Site


def _markdown() -> MarkdownIt:
    md = MarkdownIt("commonmark").enable("table").enable("strikethrough")
    try:
        from mdit_py_cjk_friendly import cjk_friendly

        md.use(cjk_friendly)
    except ImportError:
        pass
    return md


def _asciidoc_render(body: str, where: str) -> str:
    try:
        from pyasciidoc import render as asciidoc_render
    except ImportError as exc:
        raise BuildError(
            f"{where}: .adoc を使うには pyasciidoc が要ります(pip install pyasciidoc)"
        ) from exc
    return asciidoc_render(body)


@dataclass(frozen=True)
class Page:
    """生成対象のページ1枚(content/ の .md 1ファイル)。"""

    slug: str
    category: str
    title: str
    html: str
    date: date | None = None
    meta: dict[str, Any] = field(default_factory=dict, repr=False)

    @property
    def url(self) -> str:
        if self.category:
            return f"/{self.category}/{self.slug}.html"
        return f"/{self.slug}.html"


class BuildError(Exception):
    """content/ の不備。メッセージは編集者向けの日本語。"""


def split_frontmatter(text: str, where: str) -> tuple[dict[str, Any], str]:
    """先頭の --- YAML --- を (meta, 本文) に分ける。無ければ meta={}。"""
    if not text.startswith("---"):
        return {}, text
    parts = text.split("---", 2)
    if len(parts) < 3:
        raise BuildError(f"{where}: frontmatter の --- が閉じていません")
    meta = yaml.safe_load(parts[1]) or {}
    if not isinstance(meta, dict):
        raise BuildError(f"{where}: frontmatter は key: value の形式で書きます")
    return meta, parts[2]


def load_pages(site: Site) -> list[Page]:
    """content/ の全 .md/.adoc を読み、日付の新しい順に返す。"""
    md = _markdown()
    pages: list[Page] = []
    if not site.content.exists():
        return pages
    files_ = sorted({*site.content.rglob("*.md"), *site.content.rglob("*.adoc")})
    for f in files_:
        rel = f.relative_to(site.content)
        meta, body = split_frontmatter(f.read_text(encoding="utf-8"), str(rel))
        category = str(meta.get("category") or "")
        if not category and rel.parent != Path("."):
            category = rel.parent.parts[0]
        if category and category not in site.categories:
            raise BuildError(
                f"{rel}: category「{category}」が site.yaml の categories に"
                "ありません"
            )
        d = meta.get("date")
        if isinstance(d, str):
            d = date.fromisoformat(d)
        html = (
            _asciidoc_render(body, str(rel))
            if f.suffix == ".adoc"
            else md.render(body)
        )
        pages.append(
            Page(
                slug=f.stem,
                category=category,
                title=str(meta.get("title") or f.stem),
                html=html,
                date=d if isinstance(d, date) else None,
                meta=meta,
            )
        )
    pages.sort(key=lambda p: (p.date or date.min, p.title), reverse=True)
    return pages


def load_data(site: Site) -> dict[str, Any]:
    """data/*.yaml → {ファイル名(拡張子なし): 中身}。"""
    out: dict[str, Any] = {}
    if site.data.exists():
        for f in sorted(site.data.glob("*.yaml")):
            out[f.stem] = yaml.safe_load(f.read_text(encoding="utf-8"))
    return out


def _env(site: Site) -> Environment:
    loaders = []
    if site.templates.exists():
        loaders.append(FileSystemLoader(site.templates))
    loaders.append(FileSystemLoader(str(files("amig") / "templates")))
    env = Environment(loader=ChoiceLoader(loaders), autoescape=True)
    env.filters["jdate"] = jdate
    return env


def jdate(d: date | None) -> str:
    """日付の日本語表記(テンプレート用フィルタ)。"""
    return f"{d.year}年{d.month}月{d.day}日" if d else ""


def build(site: Site) -> int:
    """dist/ を作り直し、書いたページ数を返す。"""
    pages = load_pages(site)
    data = load_data(site)
    env = _env(site)
    by_cat: dict[str, list[Page]] = {}
    for p in pages:
        if p.category:
            by_cat.setdefault(p.category, []).append(p)
    ctx = {
        "site": {
            "title": site.title,
            "base_url": site.base_url,
            "lang": site.lang,
            "categories": site.categories,
        },
        "data": data,
        "pages": pages,
        "by_cat": by_cat,
    }

    if site.dist.exists():
        shutil.rmtree(site.dist)
    site.dist.mkdir(parents=True)

    # キット既定の静的ファイル(style.css)→ サイト public/ が上書き
    static = files("amig") / "templates" / "static"
    for f in static.iterdir():
        if f.is_file():
            (site.dist / f.name).write_bytes(f.read_bytes())

    n = 0
    page_t = env.get_template("page.html")
    for p in pages:
        out = site.dist / p.url.lstrip("/")
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(page_t.render(page=p, **ctx), encoding="utf-8")
        n += 1

    list_t = env.get_template("list.html")
    for cat, items in by_cat.items():
        out = site.dist / cat / "index.html"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(
            list_t.render(
                category=cat,
                category_label=site.categories.get(cat, cat),
                items=items,
                **ctx,
            ),
            encoding="utf-8",
        )
        n += 1

    index_t = env.get_template("index.html")
    (site.dist / "index.html").write_text(index_t.render(**ctx), encoding="utf-8")
    n += 1

    if site.public.exists():
        shutil.copytree(site.public, site.dist, dirs_exist_ok=True)

    # 様式(xlsx+記入用テキスト)を公開する(受付アドレスはページに書かず
    # 様式の中にだけ。§5)。配布様式の真正性のため SHA-256 を併記する
    # (dist/forms/sha256.txt。偽様式対策。§11)。public/ のコピーより後に
    # 書く——public/forms/ の古いファイルが生成物やハッシュ表を上書きして
    # 真正性記録が実物と食い違うのを防ぐ(生成物が常に勝つ)
    inquiry = site.cfg.get("inquiry") or {}
    if inquiry.get("publish_forms", True) and site.forms_out.exists():
        forms_dir = site.dist / "forms"
        forms_dir.mkdir(parents=True, exist_ok=True)
        hashes = []
        for pat in ("*.xlsx", "*.txt"):
            for f in sorted(site.forms_out.glob(pat)):
                data = f.read_bytes()
                (forms_dir / f.name).write_bytes(data)
                hashes.append(f"{hashlib.sha256(data).hexdigest()}  {f.name}")
        if hashes:
            (forms_dir / "sha256.txt").write_text(
                "\n".join(hashes) + "\n", encoding="utf-8"
            )
    return n
