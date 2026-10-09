"""設定 + 接続テスト(仕様 §4.4)。

接続テストは JMAP / CalDAV / CardDAV の疎通に加え、本アプリの前提で
ある expand と sync-collection の対応を確認する(仕様 §3.0)。
"""
from __future__ import annotations

from datetime import datetime, timedelta

import flet as ft


@ft.component
def settings_screen(app):
    results, set_results = ft.use_state(())   # 行のタプル
    server_ref = ft.use_ref()
    user_ref = ft.use_ref()
    password_ref = ft.use_ref()

    def save() -> bool:
        cfg = app.config
        cfg.server = (server_ref.current.value or "").strip().rstrip("/")
        cfg.username = (user_ref.current.value or "").strip()
        if not cfg.configured:
            app.notify("サーバー URL とユーザー名を入れてください")
            return False
        cfg.save()
        warning = cfg.set_password(password_ref.current.value or "")
        if warning:
            app.notify(warning)   # キーリング不可でも起動は継続(§4.4)
        else:
            app.notify("保存しました")
        app.reset_services()      # 次のアクセスから新しい認証情報を使う
        return True

    def test(_):
        if not save():
            return
        set_results(("接続テスト中…",))

        def add(ok: bool, text: str):
            set_results(lambda prev: prev + (("✓ " if ok else "✗ ") + text,))

        def job():
            # 1. JMAP
            try:
                session = app.jmap.connect()
                add(True, f"JMAP: セッション取得(apiUrl: {session['apiUrl']})")
            except Exception as e:
                add(False, f"JMAP: {e}")
            # 2. CalDAV + 前提検証(expand / sync-collection — §3.0)
            first_cal = None
            try:
                cals = app.caldav.discover()
                first_cal = cals[0]["url"]
                add(True, f"CalDAV: カレンダー {len(cals)} 件"
                          f"({', '.join(c['name'] for c in cals)})")
            except Exception as e:
                add(False, f"CalDAV: {e}")
            if first_cal:
                try:
                    now = datetime.now().astimezone()
                    app.caldav.get_events(first_cal, now,
                                          now + timedelta(days=7))
                    add(True, "CalDAV: calendar-query(expand 指定)応答 OK")
                except Exception as e:
                    add(False, f"CalDAV: expand 付き calendar-query 失敗 — "
                               f"サーバーが前提を満たしません(§3.0): {e}")
                _, token = app.caldav.sync_changed(first_cal, "")
                if token:
                    add(True, "CalDAV: sync-collection 対応")
                else:
                    add(False, "CalDAV: sync-collection 非対応の可能性"
                               "(キャッシュは全件取得で動作)")
            # 3. CardDAV
            try:
                books = app.carddav.discover()
                n = len(app.carddav.get_contacts())
                add(True, f"CardDAV: アドレス帳 {len(books)} 件・"
                          f"連絡先 {n} 件")
            except Exception as e:
                add(False, f"CardDAV: {e}")
        app.run_bg(job)

    return ft.Container(padding=16, content=ft.Column([
        ft.Text("設定", size=18, weight=ft.FontWeight.BOLD),
        ft.TextField(label="サーバー URL", ref=server_ref,
                     hint_text="https://mail.example.jp",
                     value=app.config.server),
        ft.TextField(label="ユーザー名", ref=user_ref,
                     value=app.config.username),
        ft.TextField(label="アプリパスワード", ref=password_ref,
                     password=True, can_reveal_password=True,
                     value=app.config.get_password()),
        ft.Row([
            ft.ElevatedButton("保存", on_click=lambda e: save()),
            ft.OutlinedButton("接続テスト", on_click=test),
        ]),
        ft.Divider(),
        ft.Column([
            ft.Text(line, color="#c00000" if line.startswith("✗") else None)
            for line in results], spacing=2),
    ], scroll=ft.ScrollMode.AUTO, width=560))
