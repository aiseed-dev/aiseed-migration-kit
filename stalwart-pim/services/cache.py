"""SQLite キャッシュ — 読み取り高速化専用(仕様 §5.4)。

正しさに関与しない層。壊れたら捨てて作り直せる。
書き込みは常にサーバー直行で、成功後にここを更新する。
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict
from datetime import datetime

from .models import Attachment, Config, Contact, Event, Mail


class Cache:
    def __init__(self, config: Config):
        config.dir.mkdir(parents=True, exist_ok=True)
        self.path = config.dir / "cache.db"
        self._init()

    def _conn(self) -> sqlite3.Connection:
        # 画面はスレッドから読むため、接続は都度開く(短命・読み中心)
        return sqlite3.connect(self.path)

    def _init(self) -> None:
        with self._conn() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS mails(
                    mailbox TEXT, id TEXT, json TEXT,
                    PRIMARY KEY(mailbox, id));
                CREATE TABLE IF NOT EXISTS events(
                    month TEXT, key TEXT, json TEXT,
                    PRIMARY KEY(month, key));
                CREATE TABLE IF NOT EXISTS contacts(
                    href TEXT PRIMARY KEY, json TEXT);
                CREATE TABLE IF NOT EXISTS sync_state(
                    key TEXT PRIMARY KEY, value TEXT);
            """)

    def clear(self) -> None:
        with self._conn() as db:
            for t in ("mails", "events", "contacts", "sync_state"):
                db.execute(f"DELETE FROM {t}")

    # ---- state -------------------------------------------------------

    def get_state(self, key: str) -> str:
        with self._conn() as db:
            row = db.execute("SELECT value FROM sync_state WHERE key=?",
                             (key,)).fetchone()
        return row[0] if row else ""

    def set_state(self, key: str, value: str) -> None:
        with self._conn() as db:
            db.execute("INSERT OR REPLACE INTO sync_state VALUES(?,?)",
                       (key, value))

    # ---- メール(一覧表示用ヘッダのみ)-------------------------------

    def load_mails(self, mailbox: str) -> list[Mail]:
        with self._conn() as db:
            rows = db.execute("SELECT json FROM mails WHERE mailbox=?",
                              (mailbox,)).fetchall()
        mails = []
        for (j,) in rows:
            d = json.loads(j)
            d.pop("attachments", None)
            mails.append(Mail(**d))
        mails.sort(key=lambda m: m.date, reverse=True)
        return mails

    def replace_mails(self, mailbox: str, mails: list[Mail]) -> None:
        with self._conn() as db:
            db.execute("DELETE FROM mails WHERE mailbox=?", (mailbox,))
            for m in mails:
                d = asdict(m)
                # 本文はキャッシュしない(仕様 §5.4。オフラインは一覧のみ)
                d["body_text"] = d["body_html"] = None
                d["attachments"] = []
                db.execute("INSERT OR REPLACE INTO mails VALUES(?,?,?)",
                           (mailbox, m.id, json.dumps(d, ensure_ascii=False)))

    # ---- 予定(月単位で置き換える)-----------------------------------

    def load_events(self, month: str) -> list[Event]:
        with self._conn() as db:
            rows = db.execute("SELECT json FROM events WHERE month=?",
                              (month,)).fetchall()
        events = []
        for (j,) in rows:
            d = json.loads(j)
            for k in ("start", "end"):
                d[k] = datetime.fromisoformat(d[k]) if d[k] else None
            events.append(Event(**d))
        events.sort(key=lambda e: (e.start is None, e.start))
        return events

    def replace_events(self, month: str, events: list[Event]) -> None:
        with self._conn() as db:
            db.execute("DELETE FROM events WHERE month=?", (month,))
            for i, ev in enumerate(events):
                d = asdict(ev)
                for k in ("start", "end"):
                    d[k] = d[k].isoformat() if d[k] else None
                key = f"{ev.href}#{i}"
                db.execute("INSERT OR REPLACE INTO events VALUES(?,?,?)",
                           (month, key, json.dumps(d, ensure_ascii=False)))

    # ---- 連絡先 ------------------------------------------------------

    def load_contacts(self) -> list[Contact]:
        with self._conn() as db:
            rows = db.execute("SELECT json FROM contacts").fetchall()
        contacts = [Contact(**json.loads(j)) for (j,) in rows]
        contacts.sort(key=lambda c: c.fn)
        return contacts

    def replace_contacts(self, contacts: list[Contact]) -> None:
        with self._conn() as db:
            db.execute("DELETE FROM contacts")
            for c in contacts:
                db.execute("INSERT OR REPLACE INTO contacts VALUES(?,?)",
                           (c.href, json.dumps(asdict(c), ensure_ascii=False)))
