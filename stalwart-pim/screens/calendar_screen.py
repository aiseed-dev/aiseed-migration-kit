"""予定表(仕様 §4.2)。月表示と当日リストの 2 種のみ。

繰り返し予定はサーバー展開(expand)を表示するだけで、
編集・削除は無効(仕様 §3.2)。
"""
from __future__ import annotations

import calendar
from datetime import date, datetime, timedelta

import flet as ft

from services.models import Event

WEEKDAYS = ["日", "月", "火", "水", "木", "金", "土"]

calendar.setfirstweekday(calendar.SUNDAY)


def events_on(events: tuple[Event, ...], d: date) -> list[Event]:
    out = []
    for ev in events:
        if ev.start is None:
            continue
        s = ev.start.date()
        e = (ev.end.date() - timedelta(days=1)) if ev.all_day and ev.end \
            else (ev.end.date() if ev.end else s)
        if s <= d <= max(s, e):
            out.append(ev)
    return out


@ft.component
def calendar_screen(app):
    today = date.today()
    ym, set_ym = ft.use_state((today.year, today.month))
    events, set_events = ft.use_state(())          # tuple[Event, ...]
    view, set_view = ft.use_state(("month", None))  # ("day", date) も

    year, month = ym
    month_key = f"{year}-{month:02d}"

    # ---- データ取得 --------------------------------------------------

    def refresh(force: bool = False):
        def job():
            cals = app.caldav.discover()
            changed = force
            tokens = {}
            for cal in cals:
                key = f"cal:{cal['url']}"
                token = app.cache.get_state(key)
                ch, new_token = app.caldav.sync_changed(cal["url"], token)
                tokens[key] = new_token
                changed = changed or ch or not token
            if not changed and events:
                return
            start = datetime(year, month, 1).astimezone()
            end = start + timedelta(days=calendar.monthrange(year, month)[1])
            got: list[Event] = []
            for cal in cals:
                got.extend(app.caldav.get_events(cal["url"], start, end))
            got.sort(key=lambda e: (e.start is None, e.start))
            set_events(tuple(got))
            app.cache.replace_events(month_key, got)
            for key, token in tokens.items():
                if token:
                    app.cache.set_state(key, token)
        app.run_bg(job)

    def load_month():
        # キャッシュを即表示 → 裏で差分確認(仕様 §5.4)
        set_events(tuple(app.cache.load_events(month_key)))
        refresh()
    ft.use_effect(load_month, [ym])

    def move_month(delta: int):
        m = month + delta
        set_ym((year + (m - 1) // 12, (m - 1) % 12 + 1))
        set_view(("month", None))

    # ---- 追加・編集・削除(ダイアログは開いている間だけの命令的な島)--

    def edit_dialog(ev: Event | None):
        base = view[1] or today
        if ev and ev.all_day:
            start_v = ev.start.strftime("%Y-%m-%d")
            end_d = (ev.end - timedelta(days=1)) if ev.end else ev.start
            end_v = end_d.strftime("%Y-%m-%d")
        elif ev:
            start_v = ev.start.strftime("%Y-%m-%d %H:%M")
            end_v = ev.end.strftime("%Y-%m-%d %H:%M") if ev.end else ""
        else:
            start_v = base.strftime("%Y-%m-%d") + " 10:00"
            end_v = base.strftime("%Y-%m-%d") + " 11:00"

        f_summary = ft.TextField(label="件名", value=ev.summary if ev else "")
        f_start = ft.TextField(label="開始(YYYY-MM-DD HH:MM)", value=start_v)
        f_end = ft.TextField(label="終了", value=end_v)
        f_allday = ft.Checkbox(label="終日(日付のみで入力)",
                               value=ev.all_day if ev else False)
        f_location = ft.TextField(label="場所", value=ev.location if ev else "")
        f_desc = ft.TextField(label="メモ", value=ev.description if ev else "",
                              multiline=True, min_lines=3, max_lines=6)

        def parse(value: str, all_day: bool) -> datetime | None:
            value = value.strip()
            if not value:
                return None
            fmt = "%Y-%m-%d" if all_day else "%Y-%m-%d %H:%M"
            return datetime.strptime(value, fmt).astimezone()

        def save(_):
            try:
                all_day = bool(f_allday.value)
                start = parse(f_start.value, all_day)
                end = parse(f_end.value, all_day)
                if start is None:
                    raise ValueError
                if all_day:
                    # DTEND は排他的終端。表示上の終了日 +1 日で持つ
                    end = (end or start) + timedelta(days=1)
            except ValueError:
                app.notify("日時は YYYY-MM-DD HH:MM"
                           "(終日は YYYY-MM-DD)で入れてください")
                return
            target = Event(uid=ev.uid if ev else "",
                           summary=f_summary.value or "(無題)",
                           start=start, end=end, all_day=all_day,
                           location=f_location.value or "",
                           description=f_desc.value or "",
                           href=ev.href if ev else "",
                           etag=ev.etag if ev else "")

            def job():
                if ev:
                    app.caldav.update_event(target)
                else:
                    app.caldav.create_event(app.caldav.default_calendar(),
                                            target)
                app.close_dialog()
                refresh(force=True)
            app.run_bg(job)

        app.open_dialog(ft.AlertDialog(
            modal=True, title=ft.Text("予定の編集" if ev else "予定の追加"),
            content=ft.Container(width=480, content=ft.Column(
                [f_summary, f_start, f_end, f_allday, f_location, f_desc],
                tight=True, scroll=ft.ScrollMode.AUTO)),
            actions=[ft.ElevatedButton("保存", on_click=save),
                     ft.TextButton("キャンセル",
                                   on_click=lambda e: app.close_dialog())]))

    def delete(ev: Event):
        def job():
            app.caldav.delete_event(ev)
            set_events(lambda prev: tuple(x for x in prev if x is not ev))
            app.cache.replace_events(
                month_key, [x for x in events if x is not ev])
        app.run_bg(job)

    # ---- 描画 --------------------------------------------------------

    def month_view():
        header = ft.Row([
            ft.IconButton(ft.Icons.CHEVRON_LEFT,
                          on_click=lambda e: move_month(-1)),
            ft.Text(f"{year}年 {month}月", size=18,
                    weight=ft.FontWeight.BOLD),
            ft.IconButton(ft.Icons.CHEVRON_RIGHT,
                          on_click=lambda e: move_month(1)),
            ft.Container(expand=True),
            ft.IconButton(ft.Icons.REFRESH, tooltip="更新",
                          on_click=lambda e: refresh(force=True)),
            ft.ElevatedButton("予定を追加",
                              on_click=lambda e: edit_dialog(None)),
        ])
        week_hdr = ft.Row([
            ft.Container(ft.Text(w, text_align=ft.TextAlign.CENTER),
                         expand=True) for w in WEEKDAYS])
        rows = []
        for week in calendar.monthcalendar(year, month):
            cells = []
            for daynum in week:
                if daynum == 0:
                    cells.append(ft.Container(expand=True))
                    continue
                d = date(year, month, daynum)
                evs = events_on(events, d)
                titles = [ft.Text(ev.summary, size=11, max_lines=1,
                                  overflow=ft.TextOverflow.ELLIPSIS)
                          for ev in evs[:3]]
                if len(evs) > 3:
                    titles.append(ft.Text(f"+{len(evs) - 3} 件", size=10,
                                          color="#888888"))
                cells.append(ft.Container(
                    expand=True, padding=4,
                    border=ft.Border.all(1, "#dddddd"),
                    bgcolor="#fff8e0" if d == today else None,
                    on_click=lambda e, d=d: set_view(("day", d)),
                    content=ft.Column(
                        [ft.Text(str(daynum), size=12,
                                 weight=ft.FontWeight.BOLD)] + titles,
                        spacing=1)))
            rows.append(ft.Row(cells, expand=True,
                               vertical_alignment=ft.CrossAxisAlignment.STRETCH))
        return [header, week_hdr] + rows

    def day_view(d: date):
        items: list[ft.Control] = []
        for ev in events_on(events, d):
            if ev.all_day:
                when = "終日"
            else:
                when = ev.start.strftime("%H:%M")
                if ev.end:
                    when += "–" + ev.end.strftime("%H:%M")
            if ev.recurring:
                actions = [ft.Text("繰り返し予定(編集は他クライアントで)",
                                   size=11, color="#888888")]
            else:
                actions = [
                    ft.TextButton("編集",
                                  on_click=lambda e, ev=ev: edit_dialog(ev)),
                    ft.TextButton("削除",
                                  on_click=lambda e, ev=ev: delete(ev)),
                ]
            detail = ([ft.Text(ev.location, size=12)] if ev.location else []) \
                + ([ft.Text(ev.description, size=12, color="#555555")]
                   if ev.description else [])
            items.append(ft.Container(
                padding=8, border=ft.Border.all(1, "#dddddd"),
                border_radius=4,
                content=ft.Column([
                    ft.Row([ft.Text(when, width=110),
                            ft.Text(ev.summary, weight=ft.FontWeight.BOLD,
                                    expand=True)] + actions),
                ] + detail, spacing=2)))
        if not items:
            items = [ft.Text("予定はありません", color="#888888")]
        return [ft.Row([
            ft.IconButton(ft.Icons.ARROW_BACK, tooltip="月表示へ",
                          on_click=lambda e: set_view(("month", None))),
            ft.Text(f"{d.year}年{d.month}月{d.day}日", size=18,
                    weight=ft.FontWeight.BOLD),
            ft.Container(expand=True),
            ft.ElevatedButton("予定を追加",
                              on_click=lambda e: edit_dialog(None)),
        ])] + items

    mode, day = view
    controls = day_view(day) if mode == "day" and day else month_view()
    return ft.Container(ft.Column(controls, expand=True), expand=True,
                        padding=8)
