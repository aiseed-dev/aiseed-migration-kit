"""受信箱(仕様 §4.1)。フォルダ / 一覧+検索 / 本文の 3 ペイン。

宣言的スタイル: 状態は use_state で持ち、置き換えたら再描画される。
Mail は書き換えず、変更はコピーを作って一覧ごと置き換える
(同値比較で再描画が決まるため — 仕様 §5.1)。
"""
from __future__ import annotations

import copy
import mimetypes
import re
import webbrowser
from html import unescape as html_unescape
from pathlib import Path

import flet as ft

from services.models import Attachment, Mail

PAGE_SIZE = 50


def strip_html(html: str) -> str:
    text = re.sub(r"(?is)<(script|style)\b.*?</\1>", "", html)
    text = re.sub(r"(?i)<br\s*/?>", "\n", text)
    text = re.sub(r"(?i)</(p|div|tr|li|h[1-6])>", "\n", text)
    text = re.sub(r"<[^>]+>", "", text)
    return html_unescape(text).strip()


def quote_body(mail: Mail) -> str:
    body = mail.body_text or strip_html(mail.body_html or "")
    quoted = "\n".join("> " + ln for ln in body.splitlines())
    return f"\n\n{mail.date} {mail.from_}:\n{quoted}\n"


def bare_addr(addr: str) -> str:
    return re.sub(r".*<|>", "", addr)


def split_addrs(value: str | None) -> list[str]:
    return [a.strip() for a in (value or "").split(",") if a.strip()]


# ---- メール作成(ダイアログの中身 — 仕様 §3.1)-----------------------

@ft.component
def compose_form(app, prefill: dict):
    suggestions, set_suggestions = ft.use_state(())   # ((label, addr), ...)
    paths, set_paths = ft.use_state(())               # ローカル添付のパス
    to_ref = ft.use_ref()
    cc_ref = ft.use_ref()
    subject_ref = ft.use_ref()
    body_ref = ft.use_ref()
    forward_atts: list[Attachment] = prefill.get("atts") or []
    draft_id = prefill.get("draft_id")

    def on_to_change(e):
        # 前方一致の宛名補完。メモリ上の連絡先から(仕様 §3.3)
        prefix = (e.control.value or "").split(",")[-1].strip().lower()
        set_suggestions(tuple(app.email_candidates(prefix)[:5])
                        if prefix else ())

    def pick(addr: str):
        tf = to_ref.current
        parts = [p.strip() for p in (tf.value or "").split(",")]
        parts[-1] = addr
        tf.value = ", ".join(p for p in parts if p)
        set_suggestions(())

    async def add_files(_):
        files = await app.picker.pick_files(allow_multiple=True)
        if files:
            new = tuple(f.path for f in files if f.path)
            set_paths(lambda prev: prev + new)

    def submit(draft: bool):
        to = split_addrs(to_ref.current.value)
        cc = split_addrs(cc_ref.current.value)
        subject = subject_ref.current.value or ""
        body = body_ref.current.value or ""
        if not draft and not to:
            app.notify("宛先を入れてください")
            return

        def job():
            atts = list(forward_atts)
            for path in paths:
                ctype = (mimetypes.guess_type(path)[0]
                         or "application/octet-stream")
                atts.append(app.jmap.upload(Path(path).read_bytes(), ctype,
                                            Path(path).name))
            args = dict(to=to, cc=cc, subject=subject, body=body,
                        attachments=atts,
                        in_reply_to=prefill.get("in_reply_to") or [],
                        references=prefill.get("references") or [])
            if draft:
                app.jmap.save_draft(replaces_id=draft_id, **args)
                app.notify("下書きを保存しました")
            else:
                app.jmap.send(replaces_draft_id=draft_id, **args)
                app.notify("送信しました")
            app.close_dialog()
        app.run_bg(job)

    return ft.Column([
        ft.TextField(label="宛先(, 区切り)", ref=to_ref,
                     value=", ".join(prefill.get("to") or []),
                     on_change=on_to_change),
        ft.Column([ft.TextButton(label,
                                 on_click=lambda e, a=addr: pick(a))
                   for label, addr in suggestions], spacing=0),
        ft.TextField(label="Cc", ref=cc_ref,
                     value=", ".join(prefill.get("cc") or [])),
        ft.TextField(label="件名", ref=subject_ref,
                     value=prefill.get("subject") or ""),
        ft.TextField(label="本文", ref=body_ref,
                     value=prefill.get("body") or "",
                     multiline=True, min_lines=10, max_lines=16),
        ft.Row([ft.Text(f"📎 {a.name}") for a in forward_atts]
               + [ft.Text(f"📎 {Path(p).name}") for p in paths], wrap=True),
        ft.Row([
            ft.TextButton("添付", on_click=add_files),
            ft.TextButton("下書き保存", on_click=lambda e: submit(True)),
            ft.ElevatedButton("送信", on_click=lambda e: submit(False)),
            ft.TextButton("キャンセル",
                          on_click=lambda e: app.close_dialog()),
        ]),
    ], tight=True, scroll=ft.ScrollMode.AUTO)


def open_compose(app, **prefill):
    # レンダラ外(イベントハンドラ)からのコンポーネント利用は
    # Component で包む
    app.open_dialog(ft.AlertDialog(
        modal=True, title=ft.Text("メール作成"),
        content=ft.Container(width=640, content=ft.Component(
            compose_form, args=(app, prefill)))))


# ---- 受信箱 ----------------------------------------------------------

@ft.component
def mail_screen(app):
    folders, set_folders = ft.use_state(())
    current, set_current = ft.use_state(None)     # dict | None
    mails, set_mails = ft.use_state(
        lambda: tuple(app.cache.load_mails("inbox")))
    selected, set_selected = ft.use_state(None)   # Mail | None(本文込み)
    search_ref = ft.use_ref()

    # ---- データ取得 --------------------------------------------------

    def fetch(folder, text="", position=0, append=False):
        def job():
            got, state = app.jmap.query_mails(folder["id"], text=text,
                                              position=position,
                                              limit=PAGE_SIZE)
            final = tuple(got)
            if append:
                def merge(prev):
                    nonlocal final
                    final = prev + tuple(got)
                    return final
                set_mails(merge)
            else:
                set_mails(final)
            if folder.get("role") == "inbox" and not text:
                app.cache.replace_mails("inbox", list(final))
                app.cache.set_state("mail", state)
        app.run_bg(job)

    def refresh():
        had_cache = bool(mails)

        def job():
            fl = tuple(app.jmap.get_mailboxes())
            set_folders(fl)
            cur = next((f for f in fl if f["role"] == "inbox"), fl[0])
            set_current(cur)
            # キャッシュを即表示 → state を突き合わせ(仕様 §5.4)
            state = app.cache.get_state("mail")
            if had_cache and state and not app.jmap.email_changed(state):
                return
            fetch(cur)
        app.run_bg(job)

    ft.use_effect(refresh, [])

    def select_folder(f):
        set_current(f)
        set_selected(None)
        if search_ref.current:
            search_ref.current.value = ""
        fetch(f)

    def search(e):
        if current:
            fetch(current, text=e.control.value or "")

    # ---- メール操作 --------------------------------------------------

    def select_mail(m: Mail):
        set_selected(copy.copy(m))    # まずヘッダを即表示

        def job():
            full = app.jmap.fetch_body(copy.copy(m))
            if not full.is_read:
                app.jmap.set_read(full.id)
                full.is_read = True
            set_selected(full)
            set_mails(lambda prev: tuple(full if x.id == full.id else x
                                         for x in prev))
        app.run_bg(job)

    def delete(m: Mail):
        def job():
            app.jmap.move_to_trash(m.id)
            set_selected(None)
            set_mails(lambda prev: tuple(x for x in prev if x.id != m.id))
            app.notify("ゴミ箱へ移動しました")
        app.run_bg(job)

    def reply(m: Mail, all_: bool):
        me = app.config.username
        cc = []
        if all_:
            addrs = [bare_addr(a) for a in m.to + m.cc]
            cc = [a for a in addrs if a and me not in a and a != m.from_email]
        subject = m.subject if m.subject.lower().startswith("re:") \
            else "Re: " + m.subject
        open_compose(app, to=[m.from_email], cc=cc, subject=subject,
                     body=quote_body(m), in_reply_to=m.message_id,
                     references=m.references + m.message_id)

    def forward(m: Mail):
        open_compose(app, subject="Fwd: " + m.subject, body=quote_body(m),
                     atts=list(m.attachments))

    def edit_draft(m: Mail):
        open_compose(app, to=[bare_addr(a) for a in m.to],
                     cc=[bare_addr(a) for a in m.cc], subject=m.subject,
                     body=m.body_text or strip_html(m.body_html or ""),
                     atts=list(m.attachments), draft_id=m.id)

    def open_browser(m: Mail, allow_remote: bool):
        html = re.sub(r"(?is)<script\b.*?</script>", "", m.body_html or "")
        img = "img-src data: http: https:" if allow_remote else "img-src data:"
        csp = f"default-src 'none'; style-src 'unsafe-inline'; {img}"
        doc = ('<!doctype html><meta charset="utf-8">'
               f'<meta http-equiv="Content-Security-Policy" content="{csp}">'
               + html)
        tmp = app.config.dir / "tmp"
        tmp.mkdir(parents=True, exist_ok=True)
        path = tmp / f"{m.id}.html"
        path.write_text(doc, encoding="utf-8")
        webbrowser.open(path.as_uri())

    async def save_att(a: Attachment):
        path = await app.picker.save_file(file_name=a.name)
        if not path:
            return

        def job():
            Path(path).write_bytes(app.jmap.download(a))
            app.notify(f"保存しました: {path}")
        app.run_bg(job)

    def add_invite(a: Attachment):
        def job():
            ics = app.jmap.download(a).decode("utf-8", "replace")
            app.caldav.add_invite(ics)
            app.notify("予定に追加しました")
        app.run_bg(job)

    # ---- 描画 --------------------------------------------------------

    def mail_tile(m: Mail):
        weight = ft.FontWeight.NORMAL if m.is_read else ft.FontWeight.BOLD
        return ft.Container(
            key=m.id,
            padding=ft.Padding.symmetric(vertical=6, horizontal=8),
            bgcolor="#f0f0f0" if selected and m.id == selected.id else None,
            on_click=lambda e, m=m: select_mail(m),
            content=ft.Column([
                ft.Row([
                    ft.Text(m.from_, weight=weight, expand=True, max_lines=1,
                            overflow=ft.TextOverflow.ELLIPSIS),
                    ft.Text("📎" if m.has_attachment else "", size=12),
                    ft.Text(m.date[:16].replace("T", " "), size=12,
                            color="#666666"),
                ]),
                ft.Text(m.subject, weight=weight, max_lines=1,
                        overflow=ft.TextOverflow.ELLIPSIS),
            ], spacing=2))

    list_items = [mail_tile(m) for m in mails]
    if len(mails) >= PAGE_SIZE and current:
        list_items.append(ft.TextButton(
            "さらに読み込む",
            on_click=lambda e: fetch(
                current, text=(search_ref.current.value or ""
                               if search_ref.current else ""),
                position=len(mails), append=True)))

    def body_pane():
        m = selected
        if m is None:
            return [ft.Text("メールを選択してください", color="#888888")]
        buttons = [
            ft.OutlinedButton("返信", on_click=lambda e: reply(m, False)),
            ft.OutlinedButton("全員に返信", on_click=lambda e: reply(m, True)),
            ft.OutlinedButton("転送", on_click=lambda e: forward(m)),
            ft.OutlinedButton("削除", on_click=lambda e: delete(m)),
        ]
        if current and current.get("role") == "drafts":
            buttons.insert(0, ft.ElevatedButton(
                "下書きを編集", on_click=lambda e: edit_draft(m)))
        body: list[ft.Control] = []
        if m.body_text:
            body.append(ft.Text(m.body_text, selectable=True))
        elif m.body_html:
            # 簡易表示 + ブラウザ(外部参照は既定で遮断 — 仕様 §4.1)
            buttons += [
                ft.OutlinedButton("ブラウザで開く",
                                  on_click=lambda e: open_browser(m, False)),
                ft.OutlinedButton("画像も読み込んで開く",
                                  on_click=lambda e: open_browser(m, True)),
            ]
            body.append(ft.Text(strip_html(m.body_html), selectable=True))
        else:
            body.append(ft.Text("(読み込み中または本文なし)",
                                color="#888888"))
        atts = []
        for a in m.attachments:
            row = [ft.OutlinedButton(
                f"💾 {a.name}",
                on_click=lambda e, a=a: app.page.run_task(save_att, a))]
            if a.type.startswith("text/calendar") or a.name.endswith(".ics"):
                row.append(ft.ElevatedButton(
                    "予定に追加", on_click=lambda e, a=a: add_invite(a)))
            atts.append(ft.Row(row))
        header = [ft.Text(m.subject, weight=ft.FontWeight.BOLD, size=16),
                  ft.Text(f"差出人: {m.from_}"),
                  ft.Text(f"宛先: {', '.join(m.to)}")]
        if m.cc:
            header.append(ft.Text(f"Cc: {', '.join(m.cc)}"))
        return header + [ft.Row(buttons, wrap=True),
                         ft.Divider(height=1)] + atts + body

    return ft.Row([
        ft.Container(width=180, content=ft.Column([
            ft.Row([
                ft.Text("フォルダ", weight=ft.FontWeight.BOLD, expand=True),
                ft.IconButton(ft.Icons.REFRESH, tooltip="更新",
                              on_click=lambda e: refresh()),
                ft.IconButton(ft.Icons.EDIT, tooltip="作成",
                              on_click=lambda e: open_compose(app)),
            ]),
            ft.ListView([
                ft.TextButton(
                    f["name"], key=f["id"],
                    style=ft.ButtonStyle(
                        bgcolor="#e0e0e0" if current
                        and f["id"] == current["id"] else None),
                    on_click=lambda e, f=f: select_folder(f))
                for f in folders], spacing=2, expand=True),
        ])),
        ft.VerticalDivider(width=1),
        ft.Column([
            ft.TextField(hint_text="検索(サーバー検索)", dense=True,
                         ref=search_ref, on_submit=search),
            ft.Container(ft.ListView(list_items, spacing=0, expand=True),
                         expand=3),
            ft.Divider(height=1),
            ft.Container(ft.Column(body_pane(), expand=True,
                                   scroll=ft.ScrollMode.AUTO),
                         expand=4, padding=8),
        ], expand=True),
    ], expand=True)
