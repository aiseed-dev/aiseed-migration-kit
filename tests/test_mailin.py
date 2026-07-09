"""mailin.handle の判別(副作用なしの純ロジック部)。"""

from email.message import EmailMessage

from amig.inquiry import mailin
from tests.test_inquiry import _text, _xlsx


def _mail(body: str = "", xlsx: bytes | None = None, sender="taro@example.co.jp") -> bytes:
    msg = EmailMessage()
    msg["From"] = f"Taro <{sender}>"
    msg["To"] = "uketsuke@example.jp"
    msg["Subject"] = "お問い合わせ"
    msg.set_content(body or "(本文なし)")
    if xlsx is not None:
        msg.add_attachment(
            xlsx,
            maintype="application",
            subtype="vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            filename="form.xlsx",
        )
    return msg.as_bytes()


def test_text_body_to_staff(example):
    folder, reply = mailin.handle(example, _mail(body=_text(example)))
    assert folder == "staff/general"
    assert reply is not None
    to, subject, body = reply
    assert to == "taro@example.co.jp"
    assert "受付" in subject
    assert "総合窓口" in body


def test_xlsx_fallback_to_staff(example):
    folder, reply = mailin.handle(example, _mail(xlsx=_xlsx(example)))
    assert folder == "staff/general"
    assert reply is not None


def test_invalid_gets_fix_request(example):
    body = _text(example).replace("お名前: 山田 太郎\n", "")
    folder, reply = mailin.handle(example, _mail(body=body))
    assert folder == mailin.PENDING
    assert reply is not None
    assert "確認" in reply[1]
    assert "お名前" in reply[2]


def test_random_mail_no_reply(example):
    """様式由来でないメールは自動返信しない(逆流防止)。"""
    folder, reply = mailin.handle(example, _mail(body="見積をお願いします"))
    assert folder == mailin.PENDING
    assert reply is None


def test_no_sender(example):
    msg = EmailMessage()
    msg.set_content(_text(example))
    folder, reply = mailin.handle(example, msg.as_bytes())
    assert folder == mailin.PENDING
    assert reply is None
