"""変換: 分類済みの記事 HTML を content/ の Markdown に落とす(機械変換)。

これは下書きである(機械変換+人の仕上げ。§10)。人が仕上げた content/ を
壊さないため、**既存の .md は上書きしない**(--force 指定時のみ上書き)。
AI による下書き変換(公開=Claude / 内部秘=ローカル)を使う場合も、
出力はこの content/ に置いて人が確認し、git に凍結する。
"""

import re
import unicodedata
from pathlib import Path

import yaml
from markdownify import markdownify

from cmsmig import classify as classify_mod
from cmsmig.rules import Extracted, SourceDoc
from cmsmig.site import Site


def convert(site: Site, force: bool = False) -> tuple[int, int]:
    """分類済みの記事を .md に変換する。(書いた件数, 飛ばした件数)を返す。"""
    result = classify_mod.load_classified(site) or classify_mod.classify(site)
    rules = site.rules()
    extract = getattr(rules, "extract", None)
    if extract is None:
        from cmsmig import rules as default

        extract = default.extract
    written = skipped = 0
    for doc in classify_mod.docs(site):
        if result.get(doc.rel) != "article":
            continue
        ex: Extracted = extract(doc)
        dst = _target(site, doc, ex)
        if dst.exists() and not force:
            skipped += 1
            continue
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_text(to_page(ex), encoding="utf-8")
        written += 1
    return written, skipped


def to_page(ex: Extracted) -> str:
    """抽出結果を frontmatter つき Markdown にする。"""
    meta: dict[str, object] = {"title": ex.title}
    if ex.date:
        meta["date"] = ex.date
    if ex.category:
        meta["category"] = ex.category
    fm = yaml.safe_dump(meta, allow_unicode=True, sort_keys=False).strip()
    body = to_markdown(ex.body_html)
    return f"---\n{fm}\n---\n\n{body}\n"


def to_markdown(html: str) -> str:
    """HTML 断片 → Markdown(表は MD の表に落ちる)。"""
    md = markdownify(html, heading_style="ATX", bullets="-")
    return re.sub(r"\n{3,}", "\n\n", md).strip()


def slug(rel: str) -> str:
    """元ファイルの相対パス → スラグ(旧URLの手がかりを保つ)。

    拡張子を除き、空白は '-'。日本語などはそのまま残す(旧URL維持は
    最終的に _redirects で行うため、ここでは可読性を優先)。
    """
    stem = Path(rel).with_suffix("").name
    stem = unicodedata.normalize("NFKC", stem)
    stem = re.sub(r"\s+", "-", stem.strip())
    return stem or "page"


def _target(site: Site, doc: SourceDoc, ex: Extracted) -> Path:
    name = f"{slug(doc.rel)}.md"
    if ex.category:
        return site.content / ex.category / name
    return site.content / name
