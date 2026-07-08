"""分類: source/raw/ の HTML を記事/一覧/skip に仕分ける。

結果は source/classified.yaml に書く(人が見て直せる中間成果物。
convert はこれを読む。無ければ convert がその場で分類する)。
分類ロジックはサイトの rules.py(無ければ既定 cmsmig.rules)。
"""

import yaml

from cmsmig.rules import SourceDoc
from cmsmig.site import Site

CLASSIFIED = "classified.yaml"


def docs(site: Site) -> list[SourceDoc]:
    """source/raw/ の HTML ファイル一覧(相対パス順)。"""
    if not site.raw.exists():
        return []
    out = []
    for f in sorted(site.raw.rglob("*")):
        if f.is_file() and f.suffix.lower() in (".html", ".htm"):
            out.append(SourceDoc(path=f, rel=str(f.relative_to(site.raw))))
    return out


def classify(site: Site) -> dict[str, str]:
    """全 HTML を分類し、classified.yaml に保存して返す。"""
    rules = site.rules()
    fn = getattr(rules, "classify", None)
    if fn is None:
        from cmsmig import rules as default

        fn = default.classify
    result = {doc.rel: str(fn(doc)) for doc in docs(site)}
    (site.source / CLASSIFIED).write_text(
        yaml.safe_dump(result, allow_unicode=True, sort_keys=True),
        encoding="utf-8",
    )
    return result


def load_classified(site: Site) -> dict[str, str] | None:
    """保存済みの分類(無ければ None)。"""
    path = site.source / CLASSIFIED
    if not path.exists():
        return None
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def counts(result: dict[str, str]) -> dict[str, int]:
    out: dict[str, int] = {}
    for kind in result.values():
        out[kind] = out.get(kind, 0) + 1
    return out
