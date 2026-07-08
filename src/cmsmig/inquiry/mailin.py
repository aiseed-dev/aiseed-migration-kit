"""受付アドレスの受信処理(担当フォルダへの振り分け)。

受付アドレスの INBOX を IMAP でポーリングし、1通ずつ処理してフォルダへ
移動する。**状態=所在フォルダ**(INBOX=未着手 → staff/<key>=担当へ /
pending=未処理)。DB は持たない。機械で裁けないものは必ず pending に
落とす(黙って捨てない)。pending の添付を人が開くときはマクロ無効の
環境(OnlyOffice の閲覧等)で開く(DESIGN.md §5)。

判別:
  (a) 本文が送信用テキスト(様式のマクロ・数式が生成。「様式:」行入り)
      → 読み取り成功なら宛先(担当)のフォルダへ+受領の自動返信
  (b) xlsx 添付あり → 同上(添付はフォールバック経路)
  (c) 不備(様式由来だが読み取れない)→ 修正依頼を自動返信して pending へ
  (d) 様式由来でない → **自動返信せず** pending へ(逆流防止)
"""

import email
import email.policy
import imaplib
import logging
import os
import time
from dataclasses import dataclass
from email.message import EmailMessage
from email.utils import parseaddr

from cmsmig.inquiry import mail, parse
from cmsmig.site import Site

log = logging.getLogger(__name__)

PENDING = "pending"


@dataclass(frozen=True)
class Cfg:
    """接続設定。秘密は環境変数で渡す(site.yaml には書かない)。"""

    imap_host: str
    imap_user: str
    imap_pass: str
    submit_addr: str
    imap_port: int = 993
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_pass: str = ""
    smtp_starttls: bool = True
    from_name: str = ""
    pending: str = PENDING
    poll_sec: int = 120

    @classmethod
    def from_env(cls) -> "Cfg":
        def need(key: str) -> str:
            v = os.environ.get(key, "")
            if not v:
                raise SystemExit(f"環境変数 {key} が未設定です")
            return v

        env = os.environ.get
        return cls(
            imap_host=need("CMSMIG_IMAP_HOST"),
            imap_user=need("CMSMIG_IMAP_USER"),
            imap_pass=need("CMSMIG_IMAP_PASS"),
            submit_addr=need("CMSMIG_SUBMIT_ADDR"),
            imap_port=int(env("CMSMIG_IMAP_PORT", "993")),
            smtp_host=env("CMSMIG_SMTP_HOST", ""),
            smtp_port=int(env("CMSMIG_SMTP_PORT", "587")),
            smtp_user=env("CMSMIG_SMTP_USER", ""),
            smtp_pass=env("CMSMIG_SMTP_PASS", ""),
            smtp_starttls=env("CMSMIG_SMTP_STARTTLS", "1") != "0",
            from_name=env("CMSMIG_FROM_NAME", ""),
            pending=env("CMSMIG_PENDING", PENDING),
            poll_sec=int(env("CMSMIG_POLL_SEC", "120")),
        )


# ---- 判別(純粋なロジック。テストはここを厚く) ----


def body_text(msg: EmailMessage) -> str:
    """本文のプレーンテキストを取り出す。"""
    part = msg.get_body(preferencelist=("plain",))
    if part is None:
        return ""
    return part.get_content()


def first_xlsx(msg: EmailMessage) -> bytes | None:
    """最初の xlsx 添付(なければ None)。"""
    for part in msg.iter_attachments():
        name = part.get_filename() or ""
        if name.lower().endswith(".xlsx"):
            return part.get_payload(decode=True)
    return None


def handle(site: Site, raw: bytes) -> tuple[str, tuple[str, str, str] | None]:
    """メール1通を判別し、(移動先フォルダ, 自動返信) を返す。

    自動返信は (宛先, 件名, 本文)。None なら返信しない。
    副作用なし(IMAP 移動・SMTP 送信は poll_once が行う)。
    """
    msg = email.message_from_bytes(raw, policy=email.policy.default)
    sender = parseaddr(msg.get("From", ""))[1].lower()
    if not sender:
        return PENDING, None

    # (a) 本文の送信用テキスト(「様式:」行があれば様式由来と判断)
    try:
        inq = parse.parse_text(body_text(msg), site)
    except parse.Invalid as e:
        subject, text = mail.fix_request(e.issues)
        return PENDING, (sender, subject, text)
    if inq is None:
        # (b) xlsx 添付(フォールバック経路)
        xlsx = first_xlsx(msg)
        if xlsx is None:
            return PENDING, None  # (d) 様式由来でない → 自動返信しない
        try:
            inq = parse.parse_xlsx(xlsx, site)
        except parse.Invalid as e:
            subject, text = mail.fix_request(e.issues)
            return PENDING, (sender, subject, text)

    subject, text = mail.receipt(inq, site.title)
    return inq.staff.folder, (sender, subject, text)


# ---- IMAP(受信箱=キュー) ----


class ImapBox:
    """受付の受信箱。フォルダ=キューの状態。"""

    def __init__(self, cfg: Cfg, site: Site):
        self.cfg = cfg
        self.conn = imaplib.IMAP4_SSL(cfg.imap_host, cfg.imap_port)
        self.conn.login(cfg.imap_user, cfg.imap_pass)
        for folder in (cfg.pending, *(s.folder for s in site.staff)):
            self.conn.create(folder)  # 既存なら NO が返るだけ
        self.select()

    def select(self, folder: str = "INBOX") -> None:
        self.conn.select(folder)

    def fetch_new(self) -> list[tuple[bytes, bytes]]:
        """INBOX の全メールを (uid, 原文) で返す(ポーリング用)。"""
        self.select()
        _, data = self.conn.uid("search", None, "ALL")
        out = []
        for uid in data[0].split():
            _, fetched = self.conn.uid("fetch", uid, "(RFC822)")
            if fetched and fetched[0]:
                out.append((uid, fetched[0][1]))
        return out

    def move(self, uid: bytes, folder: str) -> None:
        """INBOX から folder へ移す。"""
        self.conn.uid("copy", uid, folder)
        self.conn.uid("store", uid, "+FLAGS", r"(\Deleted)")
        self.conn.expunge()

    def close(self) -> None:
        self.conn.logout()


def poll_once(box: ImapBox, site: Site, mailer: mail.Mailer) -> dict[str, int]:
    """受信箱を1回さらう。想定外の失敗も pending へ(黙って捨てない)。"""
    counts: dict[str, int] = {}
    for uid, raw in box.fetch_new():
        try:
            folder, reply = handle(site, raw)
            if reply is not None:
                mailer.send(reply[0], reply[1], reply[2])
        except Exception:
            log.exception("受信処理に失敗(uid=%s)。未処理へ移動", uid)
            folder = box.cfg.pending
        box.move(uid, folder)
        counts[folder] = counts.get(folder, 0) + 1
    return counts


def run(site: Site, once: bool = False) -> None:
    """ポーリング常駐(systemd で動かす)。once=True で1回だけ。"""
    logging.basicConfig(level=logging.INFO)
    cfg = Cfg.from_env()
    mailer = mail.Smtp(cfg)
    while True:
        box = ImapBox(cfg, site)
        try:
            counts = poll_once(box, site, mailer)
            if counts:
                log.info("振り分け %s", counts)
        finally:
            box.close()
        if once:
            break
        time.sleep(cfg.poll_sec)
