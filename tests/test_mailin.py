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


def test_wrong_site_identifier_no_reply(example):
    """識別子(受付:)が本受付と違う様式は pending へ。ただし自動返信は
    しない——差出人は詐称されうるため、返信するとバックスキャッター
    発生源になる(DESIGN.md §5・§11)。"""

    body = _text(example) + "\n受付: yoso-no-site"
    folder, reply = mailin.handle(example, _mail(body=body))
    assert folder == mailin.PENDING
    assert reply is None

    # 識別子が一致すれば通常どおり受理される
    body_ok = _text(example) + f"\n受付: {example.name}"
    folder, reply = mailin.handle(example, _mail(body=body_ok))
    assert folder == "staff/general"


def test_stale_fingerprint_gets_fix_request(example):
    """様式指紋の不一致(項目構成が変わった旧様式)は正規利用者の事故
    なので、最新様式への案内を自動返信する。"""
    from amig.inquiry import forms

    body = _text(example) + f"\n{forms.KEY_REV}: 00000000"
    folder, reply = mailin.handle(example, _mail(body=body))
    assert folder == mailin.PENDING
    assert reply is not None
    assert "更新" in reply[2]


def test_propose_with_stub_llm(example, monkeypatch):
    """pending 落ちの様式由来メールに、LLM があれば解釈案が付く。"""
    import email
    import email.policy

    from amig import llm

    seen = {}

    def fake_complete(prompt):
        seen["prompt"] = prompt
        return "お名前: 山田 太郎  # 根拠: 「名まえ 山田太郎です」"

    monkeypatch.setattr(llm, "enabled", lambda: True)
    monkeypatch.setattr(llm, "complete", fake_complete)
    body = _text(example).replace("お名前: 山田 太郎\n", "名まえ 山田太郎です\n")
    note = mailin.propose(example, _mail(body=body))
    assert note is not None
    msg = email.message_from_bytes(note, policy=email.policy.default)
    assert msg["Subject"].startswith("【解釈案】")
    content = msg.get_content()
    assert "提案であり" in content  # 登録・返信に使われないことの明記
    assert "山田 太郎" in content
    # プロンプトには様式の制約と受信本文が入っている
    assert "お問い合わせ" in seen["prompt"]
    assert "名まえ 山田太郎です" in seen["prompt"]


def test_propose_without_llm_is_none(example, monkeypatch):
    """LLM 未設定なら解析ごと省いて何も足さない(フォールバック)。"""

    monkeypatch.delenv("AMIG_LLM_URL", raising=False)
    note = mailin.propose(example, _mail(body=_text(example)))
    assert note is None


def test_propose_skips_wrong_site(example, monkeypatch):
    """偽様式(識別子不一致)には解釈案を作らない(LLM を回させない)。"""
    from amig import llm

    monkeypatch.setattr(llm, "enabled", lambda: True)
    monkeypatch.setattr(llm, "complete", lambda prompt: "何か")
    body = _text(example) + "\n受付: yoso-no-site"
    assert mailin.propose(example, _mail(body=body)) is None


def test_propose_non_form_mail_is_none(example, monkeypatch):
    """様式を特定できないメールには提案しない。"""
    from amig import llm

    monkeypatch.setattr(llm, "enabled", lambda: True)
    monkeypatch.setattr(llm, "complete", lambda prompt: "何か")
    assert mailin.propose(example, _mail(body="見積をお願いします")) is None
