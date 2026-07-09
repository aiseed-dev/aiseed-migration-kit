"""サイト設定(site.yaml)とサイトディレクトリの構造。

sites/<name>/
  site.yaml   サイト設定(このモジュールが読む)
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
from pathlib import Path
from types import ModuleType
from typing import Any

import yaml

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
    """様式の記入欄1つ。"""

    key: str
    label: str
    required: bool = False
    example: str = ""


@dataclass(frozen=True)
class FormDef:
    """申込様式1種類(用途別)。site.yaml の inquiry.forms が正。"""

    key: str
    label: str
    fields: tuple[Field, ...]

    def field(self, key: str) -> Field:
        for f in self.fields:
            if f.key == key:
                return f
        raise KeyError(key)


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

    @property
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

    @property
    def forms(self) -> tuple[FormDef, ...]:
        items = (self.cfg.get("inquiry") or {}).get("forms") or []
        return tuple(_form_def(it) for it in items)

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


def _form_def(it: dict[str, Any]) -> FormDef:
    key = str(it["key"])
    _check_key(key, f"inquiry.forms の key「{key}」")
    fields = []
    for f in it.get("fields") or []:
        fkey = str(f["key"])
        _check_key(fkey, f"様式「{key}」の欄 key「{fkey}」")
        label = str(f["label"])
        if ":" in label or ":" in label:
            raise SiteError(
                f"様式「{key}」の欄ラベル「{label}」にコロンは使えません"
                "(送信用テキストの区切りと衝突するため)"
            )
        fields.append(
            Field(
                key=fkey,
                label=label,
                required=bool(f.get("required", False)),
                example=str(f.get("example", "")),
            )
        )
    if not fields:
        raise SiteError(f"様式「{key}」に fields がありません")
    return FormDef(key=key, label=str(it["label"]), fields=tuple(fields))


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
