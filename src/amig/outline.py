"""概要: 現行サイトから会社の「いい面」を拾い、新サイトの構成案を作る。

再構築の入口は、弱点の指摘ではなく**その会社の価値の再発見**にする。
現行サイトには、会社が積み上げてきたもの——沿革・製品・技術・認定・現場の
写真——が既に載っている。多くは古い体裁に埋もれて読まれないだけで、材料は
揃っている。このモジュールはそれを機械的に拾い、構成案(概要)にする。

出力は二つ:

  source/outline.yaml   拾った事実(機械可読。人が見て直せる中間成果物)
  outline.md            概要=新サイトの構成案(人が読む。対話の出発点)

抽出は決定的なルールベースで、LLM を必要としない。磨くのは人と AI の対話
(§7 の使い分け——会社の公開サイトの内容は公開・非機微なのでクラウド側)。
outline.md の末尾には、その対話の出発点になる問いを添える。キットは提案
までを担い、決めるのは人である。

研修での使い方(DESIGN.md §18):
  事前  amig ingest → amig outline   概要を用意しておく
  当日  概要を見ながら人と AI が対話して content/ を仕上げ、amig build
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

import yaml
from bs4 import BeautifulSoup, Tag

from amig import classify as classify_mod
from amig.rules import SourceDoc
from amig.site import Site

OUTLINE_YAML = "outline.yaml"
OUTLINE_MD = "outline.md"

# 会社概要の表で使われる見出し語(この順で概要に並べる)
PROFILE_LABELS = (
    "商号",
    "社名",
    "会社名",
    "名称",
    "代表者",
    "代表",
    "社長",
    "創業",
    "設立",
    "資本金",
    "従業員",
    "社員数",
    "従業員数",
    "所在地",
    "本社",
    "住所",
    "事業内容",
    "営業品目",
    "業務内容",
    "取扱品目",
    "取引先",
    "納入先",
    "取引銀行",
    "許可",
    "登録",
    "認定",
    "認証",
)

# 製品名として拾わない総称(ページの題名の見出し)。この文字だけで出来た
# 見出しは「製品・技術」「事業内容」のような器の名前で、製品名ではない
GENERIC_HEAD = re.compile(
    r"^[製品商事業技術術サービス情報一覧紹介案内内容概要・･/／\s]+$"
)

# 信頼の証拠として拾う語(会社の外から与えられた裏付け)
PROOF_RE = re.compile(
    r"(ISO\s?\d{4,5}(?::\d{4})?"
    r"|JIS\s?[A-Z]\s?\d{4}"
    r"|大臣認定|国土交通大臣|経済産業大臣"
    r"|特許第?\s?\d[\d,\-]*号?|実用新案|意匠登録"
    r"|[^\s。、]{0,12}(?:賞|表彰)を?受賞|グッドデザイン"
    r"|[A-Za-z]{0,6}マーク取得|認定工場|優良\w{0,6}認定)"
)

# 年(和暦・西暦)で始まる行=沿革とみなす
ERA_RE = re.compile(
    r"^\s*((?:明治|大正|昭和|平成|令和)\s?\d{1,2}|1[89]\d{2}|20\d{2})\s?年?"
)
YEAR_RE = re.compile(r"(1[89]\d{2}|20\d{2})")
ERA_BASE = {"明治": 1867, "大正": 1911, "昭和": 1925, "平成": 1988, "令和": 2018}

# 製品・技術のページらしさ(ファイル名・題名で判定)
PRODUCT_HINT = re.compile(
    r"(product|item|service|gijutsu|tech|seihin|事業|製品|商品|技術|サービス)"
)

# 飾り画像として除く名前(拾うのは現場・製品の写真)
DECOR_RE = re.compile(
    r"(logo|icon|btn|button|banner|bnr|arrow|bg_|_bg|spacer|line|bullet|nav)", re.I
)


@dataclass(frozen=True)
class Fact:
    """項目1つ(会社概要の1行、数字1つ)。"""

    label: str
    value: str


@dataclass(frozen=True)
class Photo:
    """現場・製品らしい写真1枚(新サイトで使う候補)。"""

    src: str
    alt: str
    page: str


@dataclass
class Outline:
    """現行サイトから拾った、会社のいい面。"""

    title: str = ""
    profile: list[Fact] = field(default_factory=list)
    history: list[Fact] = field(default_factory=list)
    products: list[str] = field(default_factory=list)
    proofs: list[str] = field(default_factory=list)
    numbers: list[Fact] = field(default_factory=list)
    photos: list[Photo] = field(default_factory=list)
    contact: list[Fact] = field(default_factory=list)
    pages: int = 0

    def to_dict(self) -> dict:
        return {
            "title": self.title,
            "pages": self.pages,
            "profile": [{"label": f.label, "value": f.value} for f in self.profile],
            "history": [{"year": f.label, "event": f.value} for f in self.history],
            "products": list(self.products),
            "proofs": list(self.proofs),
            "numbers": [{"label": f.label, "value": f.value} for f in self.numbers],
            "photos": [
                {"src": p.src, "alt": p.alt, "page": p.page} for p in self.photos
            ],
            "contact": [{"label": f.label, "value": f.value} for f in self.contact],
        }


def outline(site: Site, today: date | None = None) -> Outline:
    """source/raw/ を読み、会社のいい面を拾う。"""
    docs = classify_mod.docs(site)
    out = Outline(pages=len(docs))
    seen_photo: set[str] = set()
    for doc in docs:
        soup = doc.soup
        if not out.title:
            out.title = _site_title(soup)
        _collect_pairs(soup, out)
        _collect_products(doc, soup, out)
        _collect_proofs(soup, out)
        _collect_photos(doc, soup, out, seen_photo)
        _collect_contact(soup, out)
    out.numbers = _numbers(out, today or date.today())
    return out


def write(site: Site, out: Outline) -> tuple[Path, Path]:
    """outline.yaml と outline.md を書き、そのパスを返す。"""
    site.source.mkdir(parents=True, exist_ok=True)
    yaml_path = site.source / OUTLINE_YAML
    yaml_path.write_text(
        yaml.safe_dump(out.to_dict(), allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    md_path = site.root / OUTLINE_MD
    md_path.write_text(to_markdown(out), encoding="utf-8")
    return yaml_path, md_path


# ---- 抽出 ----


def _site_title(soup: BeautifulSoup) -> str:
    """会社名らしい文字列(title から飾りを落とす)。"""
    if not (soup.title and soup.title.string):
        return ""
    t = re.split(r"[|｜/／–—\-]", soup.title.string.strip())
    parts = [p.strip() for p in t if p.strip()]
    if not parts:
        return ""
    # 「株式会社」を含む部分があればそれを採る(無ければ最も長い部分)
    for p in parts:
        if re.search(r"(株式会社|有限会社|合同会社)", p):
            return p
    return max(parts, key=len)


def _pairs(soup: BeautifulSoup) -> list[tuple[str, str]]:
    """表・定義リストの「見出し→値」の組を全部集める。"""
    out: list[tuple[str, str]] = []
    for tr in soup.find_all("tr"):
        cells = tr.find_all(["th", "td"])
        if len(cells) >= 2:
            k = cells[0].get_text(" ", strip=True)
            v = " ".join(c.get_text(" ", strip=True) for c in cells[1:])
            if k and v:
                out.append((k, v))
    for dl in soup.find_all("dl"):
        dt = None
        for el in dl.find_all(["dt", "dd"]):
            if el.name == "dt":
                dt = el.get_text(" ", strip=True)
            elif dt:
                v = el.get_text(" ", strip=True)
                if v:
                    out.append((dt, v))
    return out


def _collect_pairs(soup: BeautifulSoup, out: Outline) -> None:
    """会社概要の項目と沿革を、同じ「見出し→値」の組から仕分ける。"""
    known = {f.label for f in out.profile}
    years = {f.label for f in out.history}
    for k, v in _pairs(soup):
        if len(v) > 300:
            continue
        m = ERA_RE.match(k)
        if m:
            year = m.group(1).replace(" ", "") + "年"  # 和暦・西暦とも「〜年」に揃える
            if year not in years:
                years.add(year)
                out.history.append(Fact(label=year, value=_clip(v, 120)))
            continue
        label = _profile_label(k)
        if label and label not in known:
            known.add(label)
            out.profile.append(Fact(label=label, value=_clip(v, 200)))


def _profile_label(k: str) -> str:
    """会社概要の見出しなら正規化して返す(違えば空)。"""
    k = k.replace("　", "").strip("：: ")
    for label in PROFILE_LABELS:
        if label in k:
            return k if len(k) <= 8 else label
    return ""


def _collect_products(doc: SourceDoc, soup: BeautifulSoup, out: Outline) -> None:
    """製品・技術のページから、見出しに出る名前を拾う。"""
    hay = f"{doc.rel} {soup.title.string if soup.title and soup.title.string else ''}"
    if not PRODUCT_HINT.search(hay):
        return
    for h in soup.find_all(["h1", "h2", "h3"]):
        name = re.sub(r"\s+", " ", h.get_text(" ", strip=True))
        if not (2 <= len(name) <= 40) or GENERIC_HEAD.match(name):
            continue
        if name not in out.products:
            out.products.append(name)


def _collect_proofs(soup: BeautifulSoup, out: Outline) -> None:
    """認定・認証・受賞・特許——外から与えられた裏付けを拾う。"""
    text = soup.get_text(" ", strip=True)
    for m in PROOF_RE.finditer(text):
        s = re.sub(r"\s+", " ", m.group(1)).strip()
        if not s:
            continue
        # 同じ認証の短い表記(ISO 9001)と詳しい表記(ISO 9001:2015)は
        # 詳しい方だけ残す
        if any(s in p for p in out.proofs):
            continue
        out.proofs = [p for p in out.proofs if p not in s]
        out.proofs.append(s)


def _collect_photos(
    doc: SourceDoc, soup: BeautifulSoup, out: Outline, seen: set[str]
) -> None:
    """現場・製品らしい写真(飾り・アイコンは除く)。"""
    for img in soup.find_all("img"):
        if not isinstance(img, Tag):
            continue
        src = str(img.get("src") or "").strip()
        if not src or src.startswith("data:") or DECOR_RE.search(src):
            continue
        if src in seen:
            continue
        seen.add(src)
        alt = str(img.get("alt") or "").strip()
        out.photos.append(Photo(src=src, alt=alt, page=doc.rel))


def _collect_contact(soup: BeautifulSoup, out: Outline) -> None:
    """電話・郵便番号——問い合わせ導線の材料。"""
    text = soup.get_text(" ", strip=True)
    known = {f.value for f in out.contact}
    m = re.search(r"0\d{1,4}[-(]\d{1,4}[-)]\d{3,4}", text)
    if m and m.group(0) not in known:
        out.contact.append(Fact(label="電話", value=m.group(0)))
    m = re.search(r"〒\s?\d{3}-?\d{4}[^。\n]{0,60}", text)
    if m and m.group(0) not in known:
        out.contact.append(Fact(label="所在地", value=_clip(m.group(0), 80)))


def _numbers(out: Outline, today: date) -> list[Fact]:
    """語れる数字(続いた年数・沿革の厚み・写真の数)を作る。"""
    nums: list[Fact] = []
    year = _founded_year(out)
    if year:
        nums.append(Fact(label="創業から", value=f"{today.year - year}年"))
    if out.history:
        nums.append(Fact(label="沿革に残る出来事", value=f"{len(out.history)}件"))
    if out.proofs:
        nums.append(Fact(label="認定・認証・受賞", value=f"{len(out.proofs)}件"))
    for f in out.profile:
        if "従業員" in f.label or "社員" in f.label:
            m = re.search(r"\d[\d,]*", f.value)
            if m:
                nums.append(Fact(label="従業員", value=f"{m.group(0)}名"))
            break
    return nums


def _founded_year(out: Outline) -> int | None:
    """創業・設立の西暦(和暦も換算)。沿革の最古の年で補う。"""
    for f in out.profile:
        if "創業" in f.label or "設立" in f.label:
            y = _to_year(f.value)
            if y:
                return y
    years = [y for y in (_to_year(f.label) for f in out.history) if y]
    return min(years) if years else None


def _to_year(s: str) -> int | None:
    m = re.search(r"(明治|大正|昭和|平成|令和)\s?(\d{1,2})", s)
    if m:
        return ERA_BASE[m.group(1)] + int(m.group(2))
    m = YEAR_RE.search(s)
    return int(m.group(1)) if m else None


def _clip(s: str, n: int) -> str:
    s = re.sub(r"\s+", " ", s).strip()
    return s if len(s) <= n else s[: n - 1] + "…"


# ---- 概要(構成案)----


def to_markdown(out: Outline) -> str:
    """拾った事実を、新サイトの構成案にする。"""
    name = out.title or "この会社"
    L: list[str] = []
    A = L.append

    A(f"# 概要 — {name}")
    A("")
    A("新しいWebサイトの構成案")
    A("")
    A("現行サイトから、この会社が積み上げてきたものを拾った。以下は構成案であり、")
    A("決めるのは会社の人である。対話で削り、足し、言い直して仕上げる。")
    A("")

    A("## この会社が持っているもの")
    A("")
    if out.numbers:
        A("語れる数字:")
        A("")
        for f in out.numbers:
            A(f"- {f.label}: **{f.value}**")
        A("")
    if out.proofs:
        A("外からの裏付け(認定・認証・受賞・特許):")
        A("")
        for p in out.proofs[:12]:
            A(f"- {p}")
        A("")
    if out.products:
        A("製品・技術として現行サイトに出ている名前:")
        A("")
        for p in out.products[:20]:
            A(f"- {p}")
        A("")
    if out.photos:
        A(f"現場・製品の写真が {len(out.photos)} 枚ある(新サイトの主役になる材料):")
        A("")
        for p in out.photos[:8]:
            A(f"- `{p.src}`" + (f" — {p.alt}" if p.alt else ""))
        A("")

    A("## 会社案内(現行サイトの記載)")
    A("")
    if out.profile:
        A("| 項目 | 内容 |")
        A("|---|---|")
        for f in out.profile:
            A(f"| {f.label} | {f.value} |")
        A("")
    else:
        A("会社概要の表は見つからなかった。新サイトでは最初に作る。")
        A("")
    if out.history:
        A("### 沿革")
        A("")
        for f in sorted(out.history, key=lambda x: _to_year(x.label) or 0):
            A(f"- {f.label} — {f.value}")
        A("")

    A("## 新サイトの構成(案)")
    A("")
    A("1. **トップ** — 会社を一行で言い切る。上の「語れる数字」と写真を最初に置く")
    A("2. **会社案内** — 会社概要の表と沿革。続いてきた年数そのものが信用になる")
    A("3. **製品・技術** — 1製品1ページ。写真・用途・仕様・問い合わせ導線")
    A("4. **信頼の証拠** — 認定・認証・受賞。取引の判断材料として前に出す")
    A("5. **お問い合わせ** — 様式は1つの定義から派生させる(DESIGN.md §5)")
    A("")
    if out.contact:
        A("現行の連絡先: " + " / ".join(f"{f.label} {f.value}" for f in out.contact))
        A("")
    A(f"現行サイトのページ数: {out.pages}")
    A("")

    A("## ここから先は、対話で決める")
    A("")
    A("以下は出発点の問いである。答えを持っているのは会社の人であり、")
    A("AI は言葉にするのを手伝う。")
    A("")
    A(f"- この会社を一行で言うと何か。「{name}は、〇〇の会社です」の〇〇に何が入るか")
    A("- 取引先が他社ではなくこの会社を選んだ理由は何か。過去に言われた言葉はあるか")
    A("- 上の製品のうち、これから伸ばすものはどれか。逆に載せなくてよいものはどれか")
    A("- 現場の写真で、見せたい工程・設備はどれか。人の写っている写真はあるか")
    A("- 問い合わせで一番多い質問は何か(それが最初に答えるべき見出しになる)")
    A("")

    return "\n".join(L) + "\n"
