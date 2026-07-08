"""ingest → classify → convert の一連(機械変換の下書きまで)。"""

from datetime import date
from pathlib import Path

from cmsmig import classify as classify_mod
from cmsmig import convert as convert_mod
from cmsmig import ingest as ingest_mod
from cmsmig import rules

ARTICLE = """<html><head><title>設備更新のお知らせ | テスト機関</title></head>
<body><nav><a href="/">ホーム</a><a href="/news/">お知らせ</a></nav>
<main><h1>設備を更新しました</h1>
<p>2026年6月2日</p>
<p>開放試験室の万能試験機を更新しました。最大荷重100kNまでの試験が可能です。
ご利用の際は事前にお問い合わせください。</p>
<table><tr><th>項目</th><td>内容</td></tr></table>
</main></body></html>"""

LISTING = """<html><head><title>お知らせ一覧</title></head><body><main>
<ul>
<li><a href="/news/1.html">設備を更新しました(開放試験室の万能試験機)</a></li>
<li><a href="/news/2.html">DX入門セミナーの受講者を募集します(7月開催)</a></li>
<li><a href="/news/3.html">夏季休業のお知らせ(8月13日から15日まで)</a></li>
<li><a href="/news/4.html">依頼試験の手数料を改定します(10月1日から)</a></li>
<li><a href="/news/5.html">技術相談会を開催します(毎月第2水曜日)</a></li>
</ul>
</main></body></html>"""


def _prime(tmp_site, tmp_path: Path) -> None:
    src = tmp_path / "input"
    (src / "news").mkdir(parents=True)
    (src / "news" / "setsubi.html").write_text(ARTICLE, encoding="utf-8")
    (src / "news" / "index.html").write_text(LISTING, encoding="utf-8")
    n = ingest_mod.ingest(tmp_site, [src])
    assert n == 2


def test_ingest_manifest(tmp_site, tmp_path):
    _prime(tmp_site, tmp_path)
    assert (tmp_site.raw / "news" / "setsubi.html").exists()
    manifest = (tmp_site.source / "manifest.yaml").read_text(encoding="utf-8")
    assert "news/setsubi.html" in manifest
    assert "sha256" in manifest


def test_classify(tmp_site, tmp_path):
    _prime(tmp_site, tmp_path)
    result = classify_mod.classify(tmp_site)
    assert result["news/setsubi.html"] == "article"
    assert result["news/index.html"] == "listing"
    assert (tmp_site.source / classify_mod.CLASSIFIED).exists()


def test_extract_defaults(tmp_path):
    f = tmp_path / "x.html"
    f.write_text(ARTICLE, encoding="utf-8")
    ex = rules.extract(rules.SourceDoc(path=f, rel="x.html"))
    assert ex.title == "設備を更新しました"
    assert ex.date == date(2026, 6, 2)
    assert "万能試験機" in ex.body_html
    assert "<h1" not in ex.body_html  # 題名は本文から除く


def test_convert_writes_and_protects(tmp_site, tmp_path):
    _prime(tmp_site, tmp_path)
    written, skipped = convert_mod.convert(tmp_site)
    assert (written, skipped) == (1, 0)
    md = tmp_site.content / "setsubi.md"
    text = md.read_text(encoding="utf-8")
    assert text.startswith("---\n")
    assert "title: 設備を更新しました" in text
    assert "| 項目 |" in text  # 表は MD の表に落ちる

    # 人の仕上げを守る: 2回目は上書きしない
    md.write_text("edited", encoding="utf-8")
    written, skipped = convert_mod.convert(tmp_site)
    assert (written, skipped) == (0, 1)
    assert md.read_text(encoding="utf-8") == "edited"

    written, skipped = convert_mod.convert(tmp_site, force=True)
    assert written == 1
    assert "万能試験機" in md.read_text(encoding="utf-8")


def test_slug():
    assert convert_mod.slug("news/お知らせ 2026.html") == "お知らせ-2026"
    assert convert_mod.slug("a/b/page.html") == "page"
