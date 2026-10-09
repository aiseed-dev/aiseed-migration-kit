"""JMAP (RFC 8620 / 8621) — メール。

MIME は組まない・読まない(仕様 §3.1)。送信は Email/set create で
JSON のまま組み立て、受信は bodyValues で構造化済みの本文を受け取る。
"""
from __future__ import annotations

from urllib.parse import quote

import httpx

from .models import Attachment, Config, Mail

CORE = "urn:ietf:params:jmap:core"
MAIL = "urn:ietf:params:jmap:mail"
SUBMISSION = "urn:ietf:params:jmap:submission"

HEADER_PROPS = ["id", "subject", "from", "to", "cc", "receivedAt", "preview",
                "keywords", "hasAttachment", "messageId", "references"]

ROLE_LABELS = {"inbox": "受信箱", "drafts": "下書き", "sent": "送信済み",
               "trash": "ゴミ箱", "junk": "迷惑メール", "archive": "アーカイブ"}


class JmapError(Exception):
    pass


def _addr_str(a: dict) -> str:
    name, email = a.get("name"), a.get("email", "")
    return f"{name} <{email}>" if name else email


class JmapService:
    def __init__(self, config: Config):
        self.config = config
        self.client = httpx.Client(
            auth=(config.username, config.get_password()),
            timeout=30, follow_redirects=True)
        self.session: dict | None = None
        self._identity: dict | None = None
        self._mailboxes: list[dict] = []

    # ---- セッション --------------------------------------------------

    def connect(self) -> dict:
        url = self.config.server.rstrip("/") + "/.well-known/jmap"
        r = self.client.get(url)
        self._raise_for_status(r)
        self.session = r.json()
        return self.session

    @property
    def account_id(self) -> str:
        if self.session is None:
            self.connect()
        return self.session["primaryAccounts"][MAIL]

    def call(self, method_calls: list) -> list:
        if self.session is None:
            self.connect()
        r = self.client.post(
            self.session["apiUrl"],
            json={"using": [CORE, MAIL, SUBMISSION],
                  "methodCalls": method_calls})
        self._raise_for_status(r)
        responses = r.json()["methodResponses"]
        for name, args, _ in responses:
            if name == "error":
                raise JmapError(f"{args.get('type')}: {args.get('description', '')}")
        return responses

    def _raise_for_status(self, r: httpx.Response) -> None:
        if r.status_code >= 500:
            # 本文はログへ、画面にはパスだけ(仕様 §5.5)
            log = self.config.dir / "server-error.log"
            self.config.dir.mkdir(parents=True, exist_ok=True)
            log.write_text(r.text, encoding="utf-8")
            raise JmapError(f"サーバーエラー。ログ: {log}")
        if r.status_code == 401:
            raise JmapError("認証に失敗しました。設定を確認してください。")
        r.raise_for_status()

    @staticmethod
    def _check_set(args: dict) -> None:
        for key in ("notCreated", "notUpdated", "notDestroyed"):
            if args.get(key):
                err = next(iter(args[key].values()))
                raise JmapError(f"{err.get('type')}: {err.get('description', '')}")

    # ---- フォルダ ----------------------------------------------------

    def get_mailboxes(self) -> list[dict]:
        resp = self.call([["Mailbox/get",
                           {"accountId": self.account_id, "ids": None}, "0"]])
        boxes = resp[0][1]["list"]
        order = {"inbox": 0, "drafts": 1, "sent": 2, "trash": 3}
        boxes.sort(key=lambda b: (order.get(b.get("role"), 9), b["name"]))
        self._mailboxes = [
            {"id": b["id"], "role": b.get("role"),
             "name": ROLE_LABELS.get(b.get("role"), b["name"])}
            for b in boxes]
        return self._mailboxes

    def mailbox_id(self, role: str) -> str:
        if not self._mailboxes:
            self.get_mailboxes()
        for b in self._mailboxes:
            if b["role"] == role:
                return b["id"]
        raise JmapError(f"サーバーに {role} ロールのフォルダがありません。")

    # ---- 一覧・検索・本文 --------------------------------------------

    def query_mails(self, mailbox_id: str | None = None, text: str = "",
                    position: int = 0, limit: int = 50) -> tuple[list[Mail], str]:
        """ヘッダ一覧と Email の state 文字列を返す。"""
        filt: dict = {}
        if mailbox_id:
            filt["inMailbox"] = mailbox_id
        if text:
            filt["text"] = text
        resp = self.call([
            ["Email/query", {
                "accountId": self.account_id, "filter": filt or None,
                "sort": [{"property": "receivedAt", "isAscending": False}],
                "position": position, "limit": limit}, "q"],
            ["Email/get", {
                "accountId": self.account_id,
                "#ids": {"resultOf": "q", "name": "Email/query", "path": "/ids"},
                "properties": HEADER_PROPS}, "g"],
        ])
        args = resp[1][1]
        return [self._to_mail(e) for e in args["list"]], args["state"]

    @staticmethod
    def _to_mail(e: dict) -> Mail:
        frm = (e.get("from") or [{}])[0]
        return Mail(
            id=e["id"], subject=e.get("subject") or "(件名なし)",
            from_=_addr_str(frm), from_email=frm.get("email", ""),
            to=[_addr_str(a) for a in e.get("to") or []],
            cc=[_addr_str(a) for a in e.get("cc") or []],
            date=e.get("receivedAt") or "", preview=e.get("preview") or "",
            is_read="$seen" in (e.get("keywords") or {}),
            has_attachment=bool(e.get("hasAttachment")),
            message_id=e.get("messageId") or [],
            references=e.get("references") or [])

    def fetch_body(self, mail: Mail) -> Mail:
        resp = self.call([["Email/get", {
            "accountId": self.account_id, "ids": [mail.id],
            "properties": ["textBody", "htmlBody", "attachments", "bodyValues"],
            "fetchAllBodyValues": True,
            "maxBodyValueBytes": 1024 * 1024}, "0"]])
        items = resp[0][1]["list"]
        if not items:
            raise JmapError("メールが見つかりません(削除された可能性)。")
        e = items[0]
        bv = e.get("bodyValues") or {}

        def joined(parts):
            return "\n".join(bv[p["partId"]]["value"]
                             for p in parts if p.get("partId") in bv)

        text = joined(e.get("textBody") or [])
        mail.body_text = text or None
        # text/plain 優先。無いときだけ html を持つ(仕様 §4.1)
        mail.body_html = None if text else (joined(e.get("htmlBody") or []) or None)
        mail.attachments = [
            Attachment(blob_id=a["blobId"], name=a.get("name") or "添付",
                       type=a.get("type") or "application/octet-stream",
                       size=a.get("size") or 0)
            for a in e.get("attachments") or [] if a.get("blobId")]
        return mail

    # ---- 既読・削除 --------------------------------------------------

    def set_read(self, mail_id: str) -> None:
        resp = self.call([["Email/set", {
            "accountId": self.account_id,
            "update": {mail_id: {"keywords/$seen": True}}}, "0"]])
        self._check_set(resp[0][1])

    def move_to_trash(self, mail_id: str) -> None:
        trash = self.mailbox_id("trash")
        resp = self.call([["Email/set", {
            "accountId": self.account_id,
            "update": {mail_id: {"mailboxIds": {trash: True}}}}, "0"]])
        self._check_set(resp[0][1])

    # ---- 送信・下書き(MIME を組まない — 仕様 §3.1)------------------

    def identity(self) -> dict:
        if self._identity is None:
            resp = self.call([["Identity/get",
                               {"accountId": self.account_id}, "0"]])
            ids = resp[0][1]["list"]
            if not ids:
                raise JmapError("送信 Identity がサーバーにありません。")
            self._identity = ids[0]
        return self._identity

    def _email_object(self, to: list[str], cc: list[str], subject: str,
                      body: str, attachments: list[Attachment],
                      in_reply_to: list[str], references: list[str],
                      mailbox_id: str, keywords: dict) -> dict:
        ident = self.identity()
        obj: dict = {
            "mailboxIds": {mailbox_id: True}, "keywords": keywords,
            "from": [{"email": ident["email"], "name": ident.get("name")}],
            "to": [{"email": a} for a in to],
            "subject": subject,
            "textBody": [{"partId": "t", "type": "text/plain"}],
            "bodyValues": {"t": {"value": body}},
        }
        if cc:
            obj["cc"] = [{"email": a} for a in cc]
        if in_reply_to:
            obj["inReplyTo"] = in_reply_to
        if references:
            obj["references"] = references
        if attachments:
            obj["attachments"] = [
                {"blobId": a.blob_id, "type": a.type, "name": a.name,
                 "disposition": "attachment"} for a in attachments]
        return obj

    def save_draft(self, to, cc, subject, body, attachments,
                   in_reply_to=None, references=None,
                   replaces_id: str | None = None) -> str:
        obj = self._email_object(to, cc, subject, body, attachments,
                                 in_reply_to or [], references or [],
                                 self.mailbox_id("drafts"),
                                 {"$draft": True, "$seen": True})
        call: dict = {"accountId": self.account_id, "create": {"d": obj}}
        if replaces_id:
            call["destroy"] = [replaces_id]
        resp = self.call([["Email/set", call, "0"]])
        self._check_set(resp[0][1])
        return resp[0][1]["created"]["d"]["id"]

    def send(self, to, cc, subject, body, attachments,
             in_reply_to=None, references=None,
             replaces_draft_id: str | None = None) -> None:
        obj = self._email_object(to, cc, subject, body, attachments,
                                 in_reply_to or [], references or [],
                                 self.mailbox_id("drafts"),
                                 {"$draft": True, "$seen": True})
        sent = self.mailbox_id("sent")
        create_call: dict = {"accountId": self.account_id, "create": {"m": obj}}
        if replaces_draft_id:
            create_call["destroy"] = [replaces_draft_id]
        resp = self.call([
            ["Email/set", create_call, "0"],
            # 送信済みへの複製は自動では行われない。onSuccessUpdateEmail で
            # 移動と $draft 除去を指示する(仕様 §3.1)
            ["EmailSubmission/set", {
                "accountId": self.account_id,
                "create": {"s": {"emailId": "#m",
                                 "identityId": self.identity()["id"]}},
                "onSuccessUpdateEmail": {"#s": {
                    "mailboxIds": {sent: True},
                    "keywords/$draft": None}}}, "1"],
        ])
        self._check_set(resp[0][1])
        self._check_set(resp[1][1])

    # ---- 添付 --------------------------------------------------------

    def upload(self, data: bytes, content_type: str, name: str) -> Attachment:
        url = self.session["uploadUrl"].replace("{accountId}",
                                                quote(self.account_id))
        r = self.client.post(url, content=data,
                             headers={"Content-Type": content_type})
        self._raise_for_status(r)
        j = r.json()
        return Attachment(blob_id=j["blobId"], name=name,
                          type=j.get("type") or content_type,
                          size=j.get("size") or len(data))

    def download(self, att: Attachment) -> bytes:
        url = (self.session["downloadUrl"]
               .replace("{accountId}", quote(self.account_id))
               .replace("{blobId}", quote(att.blob_id))
               .replace("{name}", quote(att.name))
               .replace("{type}", quote(att.type, safe="")))
        r = self.client.get(url)
        self._raise_for_status(r)
        return r.content

    # ---- キャッシュ用の差分検出(仕様 §5.4)--------------------------

    def email_changed(self, since_state: str) -> bool:
        try:
            resp = self.call([["Email/changes", {
                "accountId": self.account_id, "sinceState": since_state}, "0"]])
            a = resp[0][1]
            return bool(a.get("created") or a.get("updated")
                        or a.get("destroyed") or a.get("hasMoreChanges"))
        except (JmapError, httpx.HTTPError):
            # 差分が取れないなら全件取り直す側に倒す
            return True


def _cli() -> None:
    svc = JmapService(Config.load())
    inbox = svc.mailbox_id("inbox")
    mails, state = svc.query_mails(inbox)
    print(f"state: {state}")
    for m in mails:
        mark = " " if m.is_read else "*"
        print(f"{mark} {m.date}  {m.from_}  {m.subject}")


if __name__ == "__main__":
    _cli()
