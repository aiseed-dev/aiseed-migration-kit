"""概要(amig outline): 現行サイトから会社のいい面を拾えるか。

題材は、地方の製造業サイトの典型——会社概要の表、沿革、製品ページ、
認定の記載、現場の写真、飾り画像の混在。
"""

from datetime import date
from pathlib import Path

from amig import ingest as ingest_mod
from amig import outline as outline_mod

COMPANY = """<html><head><title>会社概要 | 山田金属工業株式会社</title></head>
<body><header><img src="/img/logo.png" alt="ロゴ"></header>
<main>
<h1>会社概要</h1>
<table>
<tr><th>商号</th><td>山田金属工業株式会社</td></tr>
<tr><th>代表者</th><td>代表取締役 山田太郎</td></tr>
<tr><th>創業</th><td>昭和32年4月</td></tr>
<tr><th>資本金</th><td>3,000万円</td></tr>
<tr><th>従業員数</th><td>48名</td></tr>
<tr><th>所在地</th><td>徳島県阿南市○○町1-2-3</td></tr>
<tr><th>事業内容</th><td>精密板金加工、溶接構造物の設計・製作</td></tr>
</table>
<h2>沿革</h2>
<table>
<tr><th>昭和32年</th><td>山田製作所として創業</td></tr>
<tr><th>昭和48年</th><td>阿南工場を新設</td></tr>
<tr><th>平成9年</th><td>ISO 9001 を取得</td></tr>
<tr><th>令和3年</th><td>レーザー加工機を導入</td></tr>
</table>
<p>当社は ISO 9001:2015 認証工場です。特許第 3456789 号を保有しています。</p>
<p>お問い合わせ 0884-22-1234 〒774-0011 徳島県阿南市○○町1-2-3</p>
</main></body></html>"""

PRODUCT = """<html><head><title>製品情報 | 山田金属工業株式会社</title></head>
<body><main>
<h1>製品情報</h1>
<h2>精密板金部品</h2>
<p>0.5mm から 6mm までの板厚に対応します。</p>
<img src="/img/photo_bankin.jpg" alt="精密板金部品の加工">
<h2>溶接構造物</h2>
<img src="/img/photo_yosetsu.jpg" alt="溶接作業の様子">
<h2>治具・装置</h2>
<img src="/img/btn_contact.gif" alt="お問い合わせ">
</main></body></html>"""


def _prime(tmp_site, tmp_path: Path) -> None:
    src = tmp_path / "input"
    src.mkdir()
    (src / "company.html").write_text(COMPANY, encoding="utf-8")
    (src / "product.html").write_text(PRODUCT, encoding="utf-8")
    ingest_mod.ingest(tmp_site, [src])


def test_profile_and_history(tmp_site, tmp_path):
    _prime(tmp_site, tmp_path)
    out = outline_mod.outline(tmp_site, today=date(2026, 7, 31))
    assert out.title == "山田金属工業株式会社"
    profile = {f.label: f.value for f in out.profile}
    assert profile["商号"] == "山田金属工業株式会社"
    assert profile["従業員数"] == "48名"
    assert "精密板金加工" in profile["事業内容"]
    years = {f.label for f in out.history}
    assert {"昭和32年", "平成9年", "令和3年"} <= years
    # 沿革の行は会社概要の項目に混ざらない
    assert "昭和32年" not in profile


def test_numbers_from_era(tmp_site, tmp_path):
    """和暦の創業年を西暦に換算し、続いた年数を数字にする。"""
    _prime(tmp_site, tmp_path)
    out = outline_mod.outline(tmp_site, today=date(2026, 7, 31))
    nums = {f.label: f.value for f in out.numbers}
    assert nums["創業から"] == "69年"  # 昭和32年 = 1957
    assert nums["従業員"] == "48名"


def test_proofs_and_products(tmp_site, tmp_path):
    _prime(tmp_site, tmp_path)
    out = outline_mod.outline(tmp_site, today=date(2026, 7, 31))
    assert any("ISO 9001" in p for p in out.proofs)
    assert any("特許" in p for p in out.proofs)
    assert "精密板金部品" in out.products
    assert "溶接構造物" in out.products


def test_photos_exclude_decoration(tmp_site, tmp_path):
    """現場の写真は拾い、ロゴ・ボタンは除く。"""
    _prime(tmp_site, tmp_path)
    out = outline_mod.outline(tmp_site, today=date(2026, 7, 31))
    srcs = {p.src for p in out.photos}
    assert "/img/photo_bankin.jpg" in srcs
    assert "/img/photo_yosetsu.jpg" in srcs
    assert "/img/logo.png" not in srcs
    assert "/img/btn_contact.gif" not in srcs


def test_contact(tmp_site, tmp_path):
    _prime(tmp_site, tmp_path)
    out = outline_mod.outline(tmp_site, today=date(2026, 7, 31))
    contact = {f.label: f.value for f in out.contact}
    assert contact["電話"] == "0884-22-1234"
    assert "徳島県阿南市" in contact["所在地"]


def test_write_outputs(tmp_site, tmp_path):
    """outline.yaml(事実)と outline.md(概要)を書く。"""
    _prime(tmp_site, tmp_path)
    out = outline_mod.outline(tmp_site, today=date(2026, 7, 31))
    yaml_path, md_path = outline_mod.write(tmp_site, out)
    assert yaml_path.exists() and md_path.exists()
    md = md_path.read_text(encoding="utf-8")
    assert "山田金属工業株式会社" in md
    assert "創業から" in md and "69年" in md
    assert "ISO 9001" in md
    assert "新サイトの構成(案)" in md
    assert "ここから先は、対話で決める" in md  # 決めるのは人
    facts = yaml_path.read_text(encoding="utf-8")
    assert "history" in facts and "proofs" in facts  # yaml のキーは機械可読
    assert "昭和32年" in facts


def test_empty_site(tmp_site):
    """取り込みが空でも落ちない(概要は器だけ出す)。"""
    out = outline_mod.outline(tmp_site, today=date(2026, 7, 31))
    assert out.pages == 0
    _, md_path = outline_mod.write(tmp_site, out)
    assert "新サイトの構成(案)" in md_path.read_text(encoding="utf-8")


def test_proof_dedup_and_generic_heading(tmp_site, tmp_path):
    """短い表記と詳しい表記は詳しい方だけ残し、器の見出しは製品にしない。"""
    src = tmp_path / "in"
    src.mkdir()
    (src / "product.html").write_text(
        "<html><head><title>製品・技術 | 甲社</title></head><body><main>"
        "<h1>製品・技術</h1><h2>精密板金部品</h2>"
        "<p>ISO 9001 を取得しています。ISO 9001:2015 認証工場です。</p>"
        "</main></body></html>",
        encoding="utf-8",
    )
    ingest_mod.ingest(tmp_site, [src])
    out = outline_mod.outline(tmp_site, today=date(2026, 7, 31))
    assert out.proofs == ["ISO 9001:2015"]
    assert out.products == ["精密板金部品"]
