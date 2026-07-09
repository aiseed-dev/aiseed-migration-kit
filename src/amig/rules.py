"""既定の抽出ルール(サイト固有 rules.py が無い・関数が無いときの既定)。

サイト側 sites/<name>/rules.py は同名の関数を定義して上書きする:
  classify(doc) -> str        "article"(記事)/ "listing"(一覧)/ "skip"
  extract(doc)  -> Extracted  記事の題名・日付・分類・本文HTML

doc は SourceDoc(取り込んだ HTML 1ファイル)。既定は汎用のヒューリス
ティックなので、CMS の実物に合わせてサイト側で上書きするのが本来の使い方。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from functools import cached_property
from pathlib import Path

from bs4 import BeautifulSoup, Tag


@dataclass
class SourceDoc:
    """取り込んだ元HTML 1ファイル。soup は遅延で1回だけ作る。"""

    path: Path
    rel: str

    @cached_property
    def soup(self) -> BeautifulSoup:
        # bytes のまま渡す(bs4 が meta charset を見て文字コードを判定する)
        return BeautifulSoup(self.path.read_bytes(), "html.parser")


@dataclass(frozen=True)
class Extracted:
    """記事1本の抽出結果。body_html を convert が Markdown にする。"""

    title: str
    body_html: str
    date: date | None = None
    category: str = ""


def classify(doc: SourceDoc) -> str:
    """リンク密度で記事か一覧かを見分ける(迷えば記事に倒す。§3)。"""
    main = _main(doc.soup)
    if main is None:
        return "skip"
    text = main.get_text(" ", strip=True)
    if not text:
        return "skip"
    link_text = " ".join(
        a.get_text(" ", strip=True) for a in main.find_all("a")
    )
    if len(text) > 100 and len(link_text) / len(text) > 0.6:
        return "listing"
    return "article"


def extract(doc: SourceDoc) -> Extracted:
    """本文らしい要素を選び、題名・日付を拾う(機械変換の下書き)。"""
    soup = doc.soup
    main = _main(soup)
    title = ""
    if soup.title and soup.title.string:
        title = soup.title.string.strip()
    h1 = (main or soup).find(["h1", "h2"])
    if h1 is not None:
        title = h1.get_text(" ", strip=True) or title
        h1.decompose()  # 本文からは除く(テンプレートが title を出す)
    return Extracted(
        title=title or doc.path.stem,
        body_html=str(main) if main is not None else "",
        date=_find_date(soup),
    )


def _main(soup: BeautifulSoup) -> Tag | None:
    """本文コンテナ(main/article → id/class の慣用名 → body)。"""
    for sel in ("main", "article", "#main", "#content", ".main", ".content"):
        el = soup.select_one(sel)
        if el is not None:
            return el
    return soup.body


_DATE_RE = re.compile(
    r"(20\d{2})[./年\-](\d{1,2})[./月\-](\d{1,2})日?"
)


def _find_date(soup: BeautifulSoup) -> date | None:
    """日付らしき最初の表記(2026-07-09 / 2026年7月9日 / 2026.7.9)。"""
    meta = soup.find("meta", attrs={"property": "article:published_time"})
    if isinstance(meta, Tag) and meta.get("content"):
        m = _DATE_RE.search(str(meta["content"]))
        if m:
            return _to_date(m)
    text = soup.get_text(" ", strip=True)[:2000]
    m = _DATE_RE.search(text)
    return _to_date(m) if m else None


def _to_date(m: re.Match[str]) -> date | None:
    try:
        return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        return None
