"""サイト設定(site.yaml)とサイトディレクトリの構造。

sites/<name>/
  site.yaml   サイト設定(このモジュールが読む)
  forms/      様式プロファイル .adoc(様式の唯一の定義。site.yaml の
              inquiry.forms がパスで参照する)
  rules.py    サイト固有の抽出ルール(無ければ既定 amig.rules)
  source/     取り込んだ元データ(raw/・manifest.yaml・classified.yaml)
  content/    記事 .md(編集の正。convert は既存を上書きしない)
  data/       構成・反復データ .yaml
  templates/  テンプレート上書き(任意。無ければキット既定)
  public/     そのまま配信するファイル(favicon・_redirects 等。任意)
  dist/       生成物(配信対象)
"""

import importlib.util
import re
from dataclasses import dataclass, field
from functools import cached_property
from pathlib import Path
from types import ModuleType
from typing import Any

import yaml

from amig.inquiry.spec import FieldSpec

# Excel の定義名・IMAP フォルダ名に使うため、key は英小文字に限定する
KEY_RE = re.compile(r"^[a-z][a-z0-9_]*$")


class SiteError(Exception):
    """site.yaml の不備。メッセージは運用担当者向けの日本語。"""


@dataclass(frozen=True)
class Staff:
    """問い合わせの担当(振り分け先)。"""

    key: str
    label: str
    folder: str


@dataclass(frozen=True)
class Field:
    """様式の記入欄1つ(様式プロファイルの解釈結果)。

    key は Excel の定義名用に位置から機械生成する(c1, c2, ...)。項目の
    同一性は label(=DDL の列名)が担い、key は xlsx の読み書きに閉じる。
    """

    key: str
    label: str
    spec: FieldSpec
    note: str = ""  # 表示用の自由記述(様式プロファイルの本文。機械は読まない)

    @property
    def required(self) -> bool:
        return bool(self.spec.required)


@dataclass(frozen=True)
class FormDef:
    """申込様式1種類(用途別)。様式プロファイル(AsciiDoc)が唯一の定義で、
    site.yaml の inquiry.forms はプロファイルへのパスの列(DESIGN.md §5)。"""

    key: str
    label: str
    intro: str
    fields: tuple[Field, ...]

    def field(self, label: str) -> Field:
        for f in self.fields:
            if f.label == label:
                return f
        raise KeyError(label)


@dataclass(frozen=True)
class Site:
    """読み込み済みのサイト1件。パスの規約はここに閉じる。"""

    root: Path
    cfg: dict[str, Any] = field(repr=False)

    @property
    def name(self) -> str:
        return str(self.cfg.get("name") or self.root.name)

    @property
    def title(self) -> str:
        return str(self.cfg["title"])

    @property
    def base_url(self) -> str:
        return str(self.cfg.get("base_url", "")).rstrip("/")

    @property
    def lang(self) -> str:
        return str(self.cfg.get("lang", "ja"))

    @property
    def categories(self) -> dict[str, str]:
        """category キー → 表示名。"""
        return dict(self.cfg.get("categories") or {})

    @property
    def source(self) -> Path:
        return self.root / "source"

    @property
    def raw(self) -> Path:
        return self.source / "raw"

    @property
    def content(self) -> Path:
        return self.root / "content"

    @property
    def data(self) -> Path:
        return self.root / "data"

    @property
    def templates(self) -> Path:
        return self.root / "templates"

    @property
    def public(self) -> Path:
        return self.root / "public"

    @property
    def dist(self) -> Path:
        return self.root / "dist"

    @property
    def forms_out(self) -> Path:
        """生成した様式 xlsx の置き場(build が dist/forms/ に載せる。
        inquiry.publish_forms: false で非公開にできる)。"""
        return self.root / "forms-out"

    # ---- 問い合わせ(inquiry) ----
    # staff / forms は初回アクセスでキャッシュされる(forms はファイル読み+
    # 解析のため)。mailin 稼働中に forms/*.adoc を編集した場合は再起動で
    # 反映する——1通の処理の途中で定義が入れ替わる不整合を避ける

    @cached_property
    def staff(self) -> tuple[Staff, ...]:
        items = (self.cfg.get("inquiry") or {}).get("staff") or []
        out = []
        for it in items:
            key = str(it["key"])
            _check_key(key, f"inquiry.staff の key「{key}」")
            out.append(
                Staff(
                    key=key,
                    label=str(it["label"]),
                    folder=str(it.get("folder") or f"staff/{key}"),
                )
            )
        return tuple(out)

    @cached_property
    def forms(self) -> tuple[FormDef, ...]:
        items = (self.cfg.get("inquiry") or {}).get("forms") or []
        return tuple(_form_def(self.root, it) for it in items)

    def form(self, key: str) -> FormDef:
        for f in self.forms:
            if f.key == key:
                return f
        raise SiteError(f"様式「{key}」は site.yaml の inquiry.forms にありません")

    def rules(self) -> ModuleType:
        """サイト固有 rules.py(無ければ既定の amig.rules)。

        rules.py は classify(doc)・extract(doc) の一方だけ定義してもよい
        (無い方は既定にフォールバックする。build 等が getattr で解決)。
        """
        path = self.root / "rules.py"
        if not path.exists():
            from amig import rules

            return rules
        spec = importlib.util.spec_from_file_location(f"rules_{self.name}", path)
        assert spec and spec.loader
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod


def _check_key(key: str, where: str) -> None:
    if not KEY_RE.match(key):
        raise SiteError(f"{where} は英小文字・数字・_ で指定してください(先頭は英字)")


def _form_def(root: Path, it: Any) -> FormDef:
    """様式プロファイル(.adoc)を読み込み FormDef にする。

    it は site.yaml の inquiry.forms の1要素=プロファイルへの相対パス。
    様式 key はファイル名(拡張子なし)。
    """
    from amig.inquiry import profile, spec

    path = root / str(it)
    key = path.stem
    _check_key(key, f"様式ファイル名「{path.name}」の key「{key}」")
    if not path.exists():
        raise SiteError(f"様式プロファイル {path} がありません")
    try:
        prof = profile.parse(path.read_text(encoding="utf-8"))
    except profile.ProfileError as e:
        raise SiteError(f"様式「{key}」({path.name}): {e}") from None

    from amig.inquiry import forms as forms_mod
    from amig.inquiry.parse import LABEL_MAX

    reserved = {
        forms_mod.KEY_KIND,
        forms_mod.KEY_VER,
        forms_mod.KEY_SITE,
        forms_mod.KEY_REV,
        forms_mod.KEY_STAFF,
    }
    fields = []
    seen: set[str] = set()
    for i, pf in enumerate(prof.fields):
        if ":" in pf.label or "：" in pf.label:
            raise SiteError(
                f"様式「{key}」の項目「{pf.label}」にコロンは使えません"
                "(送信用テキストの区切りと衝突するため)"
            )
        if len(pf.label) > LABEL_MAX:
            raise SiteError(
                f"様式「{key}」の項目「{pf.label}」が長すぎます"
                f"(送信用テキストで読み取れる項目名は {LABEL_MAX} 文字まで)"
            )
        if pf.label in reserved:
            raise SiteError(
                f"様式「{key}」の項目「{pf.label}」は予約された項目名です"
                "(様式・様式版・受付・様式指紋・宛先は使えません)"
            )
        if pf.label in seen:
            raise SiteError(
                f"様式「{key}」の項目「{pf.label}」が重複しています"
                "(項目名は DDL の列名になるため一意にしてください)"
            )
        seen.add(pf.label)
        try:
            sp = spec.parse(pf.label, pf.constraint)
        except spec.SpecError as e:
            raise SiteError(f"様式「{key}」({path.name}): {e}") from None
        fields.append(Field(key=f"c{i + 1}", label=pf.label, spec=sp, note=pf.note))
    return FormDef(
        key=key, label=prof.title, intro=prof.intro, fields=tuple(fields)
    )


def load(root: str | Path) -> Site:
    """サイトディレクトリを読み込む(site.yaml 必須)。"""
    root = Path(root)
    path = root / "site.yaml"
    if not path.exists():
        raise SiteError(f"{path} がありません(サイトディレクトリを指定してください)")
    cfg = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if "title" not in cfg:
        raise SiteError(f"{path} に title がありません")
    for key in cfg.get("categories") or {}:
        _check_key(str(key), f"categories の key「{key}」")
    site = Site(root=root, cfg=cfg)
    site.staff  # 検証を先に走らせる(不備は読み込み時に判る)
    site.forms
    return site
