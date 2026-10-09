"""dataclass と設定・プロファイル。

画面が受け取るのはここにある型と dict だけ。プロトコルの語彙
(JMAP の JSON・iCalendar・vCard)はサービスの外に出さない(仕様 §5.1)。
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

APP_NAME = "stalwart-pim"


@dataclass
class Attachment:
    blob_id: str
    name: str
    type: str
    size: int = 0


@dataclass
class Mail:
    id: str
    subject: str
    from_: str
    to: list[str] = field(default_factory=list)
    cc: list[str] = field(default_factory=list)
    date: str = ""              # ISO 8601。整形は画面側
    preview: str = ""
    is_read: bool = False
    has_attachment: bool = False
    from_email: str = ""        # 返信の宛先用(表示名なしのアドレス)
    message_id: list[str] = field(default_factory=list)
    references: list[str] = field(default_factory=list)
    # 以下は本文取得(Email/get の 2 回目)で埋まる
    body_text: str | None = None
    body_html: str | None = None    # text/html しかないメールのみ
    attachments: list[Attachment] = field(default_factory=list)


@dataclass
class Event:
    uid: str
    summary: str
    start: datetime | None
    end: datetime | None
    all_day: bool = False
    location: str = ""
    description: str = ""
    href: str = ""
    etag: str = ""
    # サーバー展開(expand)された繰り返しインスタンス。表示専用で
    # 編集・削除は不可(仕様 §3.2)
    recurring: bool = False


@dataclass
class Contact:
    uid: str
    fn: str
    family: str = ""
    given: str = ""
    emails: list[str] = field(default_factory=list)
    tels: list[str] = field(default_factory=list)
    org: str = ""
    note: str = ""
    href: str = ""
    etag: str = ""


@dataclass
class Config:
    server: str = ""            # 例: https://mail.example.jp
    username: str = ""
    profile: str = "default"
    # キーリングが使えない環境でのメモリ保持(保存はしない — 仕様 §4.4)
    _password: str = ""

    @property
    def dir(self) -> Path:
        return Path.home() / ".local" / "share" / APP_NAME / self.profile

    @property
    def configured(self) -> bool:
        return bool(self.server and self.username)

    @classmethod
    def load(cls, profile: str = "default") -> "Config":
        cfg = cls(profile=profile)
        path = cfg.dir / "config.json"
        if path.exists():
            data = json.loads(path.read_text(encoding="utf-8"))
            cfg.server = data.get("server", "")
            cfg.username = data.get("username", "")
        return cfg

    def save(self) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        (self.dir / "config.json").write_text(
            json.dumps({"server": self.server, "username": self.username},
                       ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    # ---- パスワード(キーリング。サービス名にプロファイルを含めて
    #      アカウントごとに分離する — 仕様 §5.6)----

    @property
    def _keyring_service(self) -> str:
        return f"{APP_NAME}:{self.profile}"

    def get_password(self) -> str:
        if self._password:
            return self._password
        try:
            import keyring
            return keyring.get_password(self._keyring_service, self.username) or ""
        except Exception:
            return ""

    def set_password(self, password: str) -> str | None:
        """保存を試み、キーリング不可なら案内文を返す(起動は継続)。"""
        self._password = password
        try:
            import keyring
            keyring.set_password(self._keyring_service, self.username, password)
            return None
        except Exception:
            return ("キーリングが利用できないため、パスワードは保存されません"
                    "(このセッション中のみ有効)。Debian では "
                    "gnome-keyring の導入・起動を確認してください。")
