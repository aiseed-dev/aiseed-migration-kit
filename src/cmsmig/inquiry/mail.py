"""メール送信(送信手段の抽象化)と定型文。

送信はすべてこのモジュールを通す。段階1は機関の既存 SMTP リレー、
段階2で自営メールサーバー(Stalwart)へ——接続先の変更は設定
(環境変数)だけで済む。DESIGN.md §6。
"""

import smtplib
from email.message import EmailMessage
from email.utils import formataddr
from typing import TYPE_CHECKING, Protocol

from cmsmig.inquiry.parse import Inquiry

if TYPE_CHECKING:
    from cmsmig.inquiry.mailin import Cfg


class Mailer(Protocol):
    """送信手段の差し替え点(本番 SMTP / テストはフェイク)。"""

    def send(
        self, to: str, subject: str, body: str, *, reply_to: str | None = None
    ) -> None: ...


class Smtp:
    """機関の SMTP リレーで送る(設定は環境変数。mailin.Cfg 参照)。"""

    def __init__(self, cfg: "Cfg"):
        self.cfg = cfg

    def send(
        self, to: str, subject: str, body: str, *, reply_to: str | None = None
    ) -> None:
        cfg = self.cfg
        msg = EmailMessage()
        msg["From"] = formataddr((cfg.from_name, cfg.submit_addr))
        msg["To"] = to
        msg["Subject"] = subject
        if reply_to:
            msg["In-Reply-To"] = reply_to
            msg["References"] = reply_to
        msg.set_content(body)
        with smtplib.SMTP(cfg.smtp_host, cfg.smtp_port) as smtp:
            if cfg.smtp_starttls:
                smtp.starttls()
            if cfg.smtp_user:
                smtp.login(cfg.smtp_user, cfg.smtp_pass)
            smtp.send_message(msg)


# ---- 定型文(自動返信)。すべて (件名, 本文) を返す ----


def receipt(inq: Inquiry, org_title: str) -> tuple[str, str]:
    """受領メール。担当から折り返す旨を伝える。"""
    subject = f"【受付】{inq.form.label}({org_title})"
    body = f"""{inq.form.label}を受け付けました。

担当({inq.staff.label})が内容を確認し、折り返しご連絡いたします。
このメールは自動送信です。内容の追加・訂正は、このメールへの返信で
お知らせください。

{org_title}
"""
    return subject, body


def fix_request(issues: list[str]) -> tuple[str, str]:
    """読み取れない・不備の様式への修正依頼(原文は未処理フォルダへ)。"""
    lines = "\n".join(f"・{s}" for s in issues)
    subject = "【要確認】お送りいただいた内容について"
    body = f"""お送りいただいたメールを受け取りましたが、以下の点が確認できませんでした。

{lines}

お手数ですが、内容をご確認のうえ再送をお願いいたします。
このご案内に心当たりがない場合は、そのままお待ちください。
担当が内容を確認してご連絡いたします。
"""
    return subject, body


# 様式由来でないメール(未処理行き)には自動返信しない。
# 受付アドレスは非公開のため通常は届かない——届くのはスパムか人づての
# 正規メールで、前者への自動返信はバックスキャッターになる(seminar-kit と
# 同じ決定)。未処理フォルダで人が判断する。
