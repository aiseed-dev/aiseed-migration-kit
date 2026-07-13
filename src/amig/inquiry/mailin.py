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

pending の一次処理(§7「AI の持ち場」): (c) のとき、ローカル LLM が
設定されていれば(amig.llm)、解釈案メールを pending に並べて置く。
AI は提案まで——解釈案は人が読む下書きであり、登録・返信には使われない。
LLM 未設定なら何も足さない(人がそのまま処理する)。
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

from amig.inquiry import mail, parse
from amig.site import Site

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
            imap_host=need("AMIG_IMAP_HOST"),
            imap_user=need("AMIG_IMAP_USER"),
            imap_pass=need("AMIG_IMAP_PASS"),
            submit_addr=need("AMIG_SUBMIT_ADDR"),
            imap_port=int(env("AMIG_IMAP_PORT", "993")),
            smtp_host=env("AMIG_SMTP_HOST", ""),
            smtp_port=int(env("AMIG_SMTP_PORT", "587")),
            smtp_user=env("AMIG_SMTP_USER", ""),
            smtp_pass=env("AMIG_SMTP_PASS", ""),
            smtp_starttls=env("AMIG_SMTP_STARTTLS", "1") != "0",
            from_name=env("AMIG_FROM_NAME", ""),
            pending=env("AMIG_PENDING", PENDING),
            poll_sec=int(env("AMIG_POLL_SEC", "120")),
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
    except parse.WrongSite:
        return PENDING, None  # 偽様式の疑い → 自動返信しない(逆流防止)
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
        except parse.WrongSite:
            return PENDING, None  # 偽様式の疑い → 自動返信しない
        except parse.Invalid as e:
            subject, text = mail.fix_request(e.issues)
            return PENDING, (sender, subject, text)

    subject, text = mail.receipt(inq, site.title)
    return inq.staff.folder, (sender, subject, text)


def propose(site: Site, raw: bytes) -> bytes | None:
    """pending 落ちした様式由来メールの解釈案(メール形式)を作る。

    ローカル LLM(amig.llm)が未設定・失敗なら None(何も足さない)。
    解釈案は AI の提案であり、登録・自動返信には使われない——pending
    フォルダで原文の隣に置かれ、人が確認するための下書きに徹する。
    """
    from amig import llm
    from amig.inquiry import derive, forms

    if not llm.enabled():
        return None  # LLM 未設定なら解析ごと省く(ルールベース=何もしない)

    msg = email.message_from_bytes(raw, policy=email.policy.default)
    body = body_text(msg)
    fields = parse.text_fields(body)
    # 偽様式(識別子不一致)には提案しない(攻撃者に LLM を回させない)
    sid = fields.get(forms.KEY_SITE)
    if sid is not None and sid != site.name:
        return None
    # 「様式:」行から対象の様式を特定する(特定できなければ提案しない)
    form = next(
        (f for f in site.forms if f.key == fields.get(forms.KEY_KIND)), None
    )
    if form is None:
        return None

    answer = llm.complete(derive.prompt(form, body))
    if not answer:
        return None

    note = EmailMessage()
    note["From"] = "amig <noreply@localhost>"
    note["Subject"] = f"【解釈案】{msg.get('Subject', '(件名なし)')}"
    if msg.get("Message-ID"):
        note["In-Reply-To"] = msg["Message-ID"]
    note.set_content(
        "ローカルAIによる解釈案です(提案であり、登録・返信には使われて\n"
        "いません)。原文と突き合わせて確認してください。\n\n" + answer
    )
    return note.as_bytes()


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

    def append(self, folder: str, raw: bytes) -> None:
        """folder にメールを1通追加する(解釈案の並置に使う)。"""
        self.conn.append(folder, "", imaplib.Time2Internaldate(time.time()), raw)

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
        if folder == box.cfg.pending:
            try:  # 解釈案(LLM 未設定なら None。失敗しても受付は止めない)
                note = propose(site, raw)
                if note is not None:
                    box.append(box.cfg.pending, note)
            except Exception:
                log.exception("解釈案の生成に失敗(uid=%s)。原文のみ", uid)
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
