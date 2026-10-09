"""連絡先(仕様 §4.3)。左: 検索 + 一覧(FN 昇順)、右: 詳細・編集。

一覧・検索はメモリ上の連絡先に対して行う(仕様 §3.3)。
編集項目は対応プロパティ(FN/N/EMAIL/TEL/ORG/NOTE)のみ。
フォームは選択の href を key にして、選択が替わったら作り直す
(編集途中のテキストを持ち越さない)。
"""
from __future__ import annotations

import flet as ft

from services.models import Contact


@ft.component
def contacts_screen(app):
    contacts, set_contacts = ft.use_state(
        lambda: tuple(app.cache.load_contacts()))
    query, set_query = ft.use_state("")
    selected, set_selected = ft.use_state(None)   # Contact | None
    fn_ref = ft.use_ref()
    family_ref = ft.use_ref()
    given_ref = ft.use_ref()
    emails_ref = ft.use_ref()
    tels_ref = ft.use_ref()
    org_ref = ft.use_ref()
    note_ref = ft.use_ref()

    # ---- データ取得 --------------------------------------------------

    def refresh(force: bool = False):
        had = bool(contacts)

        def job():
            token = app.cache.get_state("card")
            if not force and had and token:
                changed, _ = app.carddav.sync_changed(token)
                if not changed:
                    return
            got = app.carddav.get_contacts()
            _, new_token = app.carddav.sync_changed("")
            app.contacts = got            # 宛名補完の供給源(仕様 §3.3)
            app.cache.replace_contacts(got)
            if new_token:
                app.cache.set_state("card", new_token)
            set_contacts(tuple(got))
        app.run_bg(job)

    ft.use_effect(refresh, [])

    # ---- 保存・削除 --------------------------------------------------

    def form_contact() -> Contact:
        base = selected or Contact(uid="", fn="")
        family = family_ref.current.value or ""
        given = given_ref.current.value or ""
        return Contact(
            uid=base.uid, href=base.href, etag=base.etag,
            fn=fn_ref.current.value or f"{family} {given}".strip(),
            family=family, given=given,
            emails=[x.strip() for x
                    in (emails_ref.current.value or "").splitlines()
                    if x.strip()],
            tels=[x.strip() for x
                  in (tels_ref.current.value or "").splitlines()
                  if x.strip()],
            org=org_ref.current.value or "",
            note=note_ref.current.value or "")

    def save(_):
        target = form_contact()
        if not target.fn:
            app.notify("表示名か姓名を入れてください")
            return

        def job():
            if target.href:
                app.carddav.update_contact(target)
            else:
                app.carddav.create_contact(target)
            app.notify("保存しました")
            set_selected(None)
            refresh(force=True)
        app.run_bg(job)

    def delete(_):
        c = selected
        if c is None or not c.href:
            return

        def confirmed(_):
            app.close_dialog()

            def job():
                app.carddav.delete_contact(c)
                set_selected(None)
                refresh(force=True)
            app.run_bg(job)

        app.open_dialog(ft.AlertDialog(
            modal=True, title=ft.Text("削除の確認"),
            content=ft.Text(f"「{c.fn}」を削除しますか?"),
            actions=[ft.ElevatedButton("削除する", on_click=confirmed),
                     ft.TextButton("キャンセル",
                                   on_click=lambda e: app.close_dialog())]))

    # ---- 描画 --------------------------------------------------------

    q = query.lower()
    hits = [c for c in contacts
            if not q or q in c.fn.lower()
            or any(q in e.lower() for e in c.emails)]

    sel = selected
    form = ft.Column(key=sel.href if sel else "new", controls=[
        ft.TextField(label="表示名 (FN)", ref=fn_ref,
                     value=sel.fn if sel else ""),
        ft.Row([
            ft.TextField(label="姓", width=160, ref=family_ref,
                         value=sel.family if sel else ""),
            ft.TextField(label="名", width=160, ref=given_ref,
                         value=sel.given if sel else ""),
        ]),
        ft.TextField(label="メール(1 行 1 件)", ref=emails_ref,
                     value="\n".join(sel.emails) if sel else "",
                     multiline=True, min_lines=2, max_lines=4),
        ft.TextField(label="電話(1 行 1 件)", ref=tels_ref,
                     value="\n".join(sel.tels) if sel else "",
                     multiline=True, min_lines=2, max_lines=4),
        ft.TextField(label="所属 (ORG)", ref=org_ref,
                     value=sel.org if sel else ""),
        ft.TextField(label="メモ (NOTE)", ref=note_ref,
                     value=sel.note if sel else "",
                     multiline=True, min_lines=2, max_lines=6),
        ft.Row([
            ft.ElevatedButton("保存", on_click=save),
            ft.OutlinedButton("削除", on_click=delete),
        ]),
    ], expand=True, scroll=ft.ScrollMode.AUTO)

    return ft.Row([
        ft.Container(width=260, content=ft.Column([
            ft.Row([
                ft.TextField(hint_text="検索", dense=True, value=query,
                             on_change=lambda e: set_query(
                                 e.control.value or "")),
                ft.IconButton(ft.Icons.REFRESH, tooltip="更新",
                              on_click=lambda e: refresh(force=True)),
            ]),
            ft.ListView([
                ft.TextButton(
                    c.fn or "(名前なし)", key=c.href or c.uid,
                    style=ft.ButtonStyle(
                        bgcolor="#e0e0e0" if sel
                        and c.href == sel.href else None),
                    on_click=lambda e, c=c: set_selected(c))
                for c in hits], spacing=2, expand=True),
            ft.ElevatedButton("新規作成",
                              on_click=lambda e: set_selected(None)),
        ])),
        ft.VerticalDivider(width=1),
        ft.Container(form, expand=True, padding=8),
    ], expand=True)
