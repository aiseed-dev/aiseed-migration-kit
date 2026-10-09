"""起動・プロファイル解決・ナビゲーション・画面切替(仕様 §5.2)。

UI は宣言的スタイル(Flet 1.0 の @ft.component + use_state)。
共有するのは AppServices(設定・プロトコル層・キャッシュ・共通ヘルパ)
だけで、描画状態は各画面コンポーネントが自分のフックで持つ(仕様 §5.1)。
"""
from __future__ import annotations

import argparse

import flet as ft
import httpx

from screens.calendar_screen import calendar_screen
from screens.contacts_screen import contacts_screen
from screens.mail_screen import mail_screen
from screens.settings_screen import settings_screen
from services.cache import Cache
from services.caldav_service import CaldavService
from services.carddav_service import CarddavService
from services.jmap_service import JmapService
from services.models import Config, Contact


class AppServices:
    """プロトコル層・キャッシュ・共通ヘルパの束。描画状態は持たない。"""

    def __init__(self, page: ft.Page, profile: str):
        self.page = page
        self.config = Config.load(profile)
        self.cache = Cache(self.config)
        self._jmap = self._caldav = self._carddav = None
        self.contacts: list[Contact] = []   # 宛名補完用(表示状態ではない)
        self.picker = ft.FilePicker()
        page.services.append(self.picker)
        # 実体は app_root がマウント時に差し込む(それまでは何もしない)
        self.set_offline = lambda flag: None
        self.goto = lambda index: None

    # ---- サービス(遅延生成。設定変更で作り直す)---------------------

    @property
    def jmap(self) -> JmapService:
        if self._jmap is None:
            self._jmap = JmapService(self.config)
        return self._jmap

    @property
    def caldav(self) -> CaldavService:
        if self._caldav is None:
            self._caldav = CaldavService(self.config)
        return self._caldav

    @property
    def carddav(self) -> CarddavService:
        if self._carddav is None:
            self._carddav = CarddavService(self.config)
        return self._carddav

    def reset_services(self):
        self._jmap = self._caldav = self._carddav = None

    def load_contacts(self):
        self.contacts = self.carddav.get_contacts()
        self.cache.replace_contacts(self.contacts)

    def email_candidates(self, prefix: str) -> list[tuple[str, str]]:
        """前方一致の宛名補完(仕様 §3.3)。(表示ラベル, アドレス) を返す。"""
        prefix = prefix.lower()
        hits = []
        for c in self.contacts:
            for e in c.emails:
                if e.lower().startswith(prefix) or c.fn.lower().startswith(prefix):
                    hits.append((f"{c.fn} <{e}>", e))
        return hits

    # ---- 共通ヘルパ --------------------------------------------------

    def run_bg(self, job, quiet: bool = False):
        """バックグラウンド実行。run_thread は contextvars を引き継ぐので、
        ジョブの中から set_state してよい。ネットワーク断はバナー、その他の
        エラーは通知にして書き込みを明示的に失敗させる(仕様 §5.5)。"""
        def wrap():
            try:
                job()
                self.set_offline(False)
            except httpx.TransportError:
                self.set_offline(True)
            except Exception as e:
                if not quiet:
                    self.notify(str(e))
                if "認証に失敗" in str(e):
                    self.goto(3)   # 設定画面へ誘導(仕様 §5.5)
        self.page.run_thread(wrap)

    def notify(self, message: str):
        self.page.show_dialog(ft.SnackBar(content=ft.Text(message)))

    def open_dialog(self, dlg: ft.AlertDialog):
        self.page.show_dialog(dlg)

    def close_dialog(self):
        self.page.pop_dialog()


NAV = [("受信箱", ft.Icons.MAIL, mail_screen),
       ("予定表", ft.Icons.CALENDAR_MONTH, calendar_screen),
       ("連絡先", ft.Icons.PERSON, contacts_screen),
       ("設定", ft.Icons.SETTINGS, settings_screen)]


@ft.component
def app_root(app: AppServices):
    offline, set_offline = ft.use_state(False)
    index, set_index = ft.use_state(3 if not app.config.configured else 0)
    app.set_offline = set_offline
    app.goto = set_index

    def boot():
        # 未設定なら設定画面から(仕様 §6 段階 1)。宛名補完用の連絡先を
        # メモリに載せる(仕様 §3.3)
        if app.config.configured:
            app.contacts = app.cache.load_contacts()
            app.run_bg(app.load_contacts, quiet=True)
    ft.use_effect(boot, [])

    return ft.Column([
        ft.Container(
            visible=offline, bgcolor="#ffe0b0", padding=6,
            content=ft.Text("オフライン: サーバーに接続できません"
                            "(キャッシュの一覧のみ閲覧できます)")),
        ft.Row([
            ft.NavigationRail(
                selected_index=index,
                label_type=ft.NavigationRailLabelType.ALL, min_width=72,
                destinations=[ft.NavigationRailDestination(icon=icon,
                                                           label=label)
                              for label, icon, _ in NAV],
                on_change=lambda e: set_index(e.control.selected_index)),
            ft.VerticalDivider(width=1),
            ft.Container(NAV[index][2](app), expand=True),
        ], expand=True),
    ], expand=True, spacing=0)


def run():
    parser = argparse.ArgumentParser(description="Stalwart PIM client")
    parser.add_argument("--profile", default="default",
                        help="アカウントごとの分離(仕様 §5.6)")
    args = parser.parse_args()

    def main(page: ft.Page):
        app = AppServices(page, args.profile)
        page.title = f"Stalwart PIM — {app.config.username or args.profile}"
        page.padding = 0
        # ルートは Component で包んで載せる(コンポーネント呼び出しが
        # 使えるのはレンダラの中だけ)
        page.add(ft.Component(app_root, args=(app,)))

    ft.run(main)


if __name__ == "__main__":
    run()
