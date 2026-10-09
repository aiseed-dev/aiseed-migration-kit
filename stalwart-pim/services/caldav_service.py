"""CalDAV (RFC 4791) — 予定。

繰り返しはサーバー展開(expand)に委ね、RRULE を一切解釈しない。
expand の結果は表示専用で、編集は元リソースの GET → PUT(If-Match)
だけが正しい経路(仕様 §3.2)。日時は UTC で書き、VTIMEZONE は
生成しない。終日は VALUE=DATE。
"""
from __future__ import annotations

import uuid
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from urllib.parse import urljoin
from zoneinfo import ZoneInfo

import httpx

from .models import Config, Event

D = "DAV:"
C = "urn:ietf:params:xml:ns:caldav"


class CaldavError(Exception):
    pass


# ---- iCalendar 最小実装 ---------------------------------------------

def unfold(text: str) -> list[str]:
    lines: list[str] = []
    for raw in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        if raw[:1] in (" ", "\t") and lines:
            lines[-1] += raw[1:]
        elif raw:
            lines.append(raw)
    return lines


def split_line(line: str) -> tuple[str, dict, str]:
    """行 → (プロパティ名, パラメータ, 値)。引用符内の : ; は区切りにしない。"""
    in_q = False
    colon = -1
    for i, ch in enumerate(line):
        if ch == '"':
            in_q = not in_q
        elif ch == ":" and not in_q:
            colon = i
            break
    if colon < 0:
        return line.upper(), {}, ""
    head, value = line[:colon], line[colon + 1:]
    parts: list[str] = []
    in_q = False
    start = 0
    for i, ch in enumerate(head):
        if ch == '"':
            in_q = not in_q
        elif ch == ";" and not in_q:
            parts.append(head[start:i])
            start = i + 1
    parts.append(head[start:])
    params = {}
    for p in parts[1:]:
        if "=" in p:
            k, v = p.split("=", 1)
            params[k.upper()] = v.strip('"')
    return parts[0].upper(), params, value


def unescape(value: str) -> str:
    out = []
    i = 0
    while i < len(value):
        if value[i] == "\\" and i + 1 < len(value):
            nxt = value[i + 1]
            out.append("\n" if nxt in "nN" else nxt)
            i += 2
        else:
            out.append(value[i])
            i += 1
    return "".join(out)


def escape(value: str) -> str:
    return (value.replace("\\", "\\\\").replace("\n", "\\n")
            .replace(",", "\\,").replace(";", "\\;"))


def fold(line: str) -> str:
    # 75 オクテット制限。マルチバイト境界を壊さないよう文字単位で安全側に切る
    out, cur = [], ""
    for ch in line:
        if len((cur + ch).encode()) > 73:
            out.append(cur)
            cur = " " + ch
        else:
            cur += ch
    out.append(cur)
    return "\r\n".join(out)


def parse_dt(value: str, params: dict) -> tuple[datetime | None, bool]:
    """(datetime, 終日か)。表示用にローカル時刻へ寄せる。"""
    try:
        if params.get("VALUE") == "DATE" or (len(value) == 8 and value.isdigit()):
            return datetime.strptime(value, "%Y%m%d"), True
        if value.endswith("Z"):
            dt = datetime.strptime(value, "%Y%m%dT%H%M%SZ").replace(
                tzinfo=timezone.utc)
            return dt.astimezone(), False
        dt = datetime.strptime(value, "%Y%m%dT%H%M%S")
        tzid = params.get("TZID")
        if tzid:
            try:
                dt = dt.replace(tzinfo=ZoneInfo(tzid)).astimezone()
            except Exception:
                pass  # 未知の TZID はローカル扱い(解釈しない方針の範囲)
        return dt, False
    except ValueError:
        return None, False


def format_dt(dt: datetime, all_day: bool) -> str:
    if all_day:
        return dt.strftime("%Y%m%d")
    if dt.tzinfo is None:
        dt = dt.astimezone()
    return dt.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def parse_events(ics: str, href: str = "", etag: str = "") -> list[Event]:
    events: list[Event] = []
    cur: dict | None = None
    for line in unfold(ics):
        name, params, value = split_line(line)
        if name == "BEGIN" and value.upper() == "VEVENT":
            cur = {"recurring": False}
        elif name == "END" and value.upper() == "VEVENT" and cur is not None:
            events.append(Event(
                uid=cur.get("uid", ""), summary=cur.get("summary", "(無題)"),
                start=cur.get("start"), end=cur.get("end"),
                all_day=cur.get("all_day", False),
                location=cur.get("location", ""),
                description=cur.get("description", ""),
                href=href, etag=etag, recurring=cur["recurring"]))
            cur = None
        elif cur is not None:
            if name == "UID":
                cur["uid"] = value
            elif name == "SUMMARY":
                cur["summary"] = unescape(value)
            elif name == "LOCATION":
                cur["location"] = unescape(value)
            elif name == "DESCRIPTION":
                cur["description"] = unescape(value)
            elif name == "DTSTART":
                cur["start"], cur["all_day"] = parse_dt(value, params)
            elif name == "DTEND":
                cur["end"], _ = parse_dt(value, params)
            elif name in ("RECURRENCE-ID", "RRULE"):
                cur["recurring"] = True
    return events


def build_event_ics(ev: Event) -> str:
    lines = [
        "BEGIN:VCALENDAR", "VERSION:2.0",
        "PRODID:-//aiseed//stalwart-pim//JA",
        "BEGIN:VEVENT",
        f"UID:{ev.uid}",
        "DTSTAMP:" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"),
    ]
    if ev.all_day:
        lines.append("DTSTART;VALUE=DATE:" + format_dt(ev.start, True))
        if ev.end:
            lines.append("DTEND;VALUE=DATE:" + format_dt(ev.end, True))
    else:
        lines.append("DTSTART:" + format_dt(ev.start, False))
        if ev.end:
            lines.append("DTEND:" + format_dt(ev.end, False))
    lines.append("SUMMARY:" + escape(ev.summary))
    if ev.location:
        lines.append("LOCATION:" + escape(ev.location))
    if ev.description:
        lines.append("DESCRIPTION:" + escape(ev.description))
    lines += ["END:VEVENT", "END:VCALENDAR"]
    return "\r\n".join(fold(x) for x in lines) + "\r\n"


def replace_event_props(ics: str, ev: Event) -> str:
    """元 ics の VEVENT で対応プロパティだけ差し替える。
    それ以外の行は保持する(round-trip 保証 — 仕様 §3.2)。"""
    updates: dict[str, str | None] = {
        "SUMMARY": "SUMMARY:" + escape(ev.summary),
        "LOCATION": ("LOCATION:" + escape(ev.location)) if ev.location else None,
        "DESCRIPTION": ("DESCRIPTION:" + escape(ev.description))
                       if ev.description else None,
        "DTSTART": ("DTSTART;VALUE=DATE:" + format_dt(ev.start, True))
                   if ev.all_day else "DTSTART:" + format_dt(ev.start, False),
        "DTEND": None if ev.end is None else (
            ("DTEND;VALUE=DATE:" + format_dt(ev.end, True))
            if ev.all_day else "DTEND:" + format_dt(ev.end, False)),
    }
    out: list[str] = []
    in_vevent = False
    for line in unfold(ics):
        name, _, value = split_line(line)
        if name == "BEGIN" and value.upper() == "VEVENT":
            in_vevent = True
            out.append(line)
            continue
        if name == "END" and value.upper() == "VEVENT":
            for new in updates.values():
                if new is not None:
                    out.append(new)
            updates = {}
            in_vevent = False
            out.append(line)
            continue
        if in_vevent and name in updates:
            continue  # 旧値を捨てる。新値は END 直前でまとめて入れる
        out.append(line)
    return "\r\n".join(fold(x) for x in out) + "\r\n"


# ---- DAV クライアント -----------------------------------------------

class CaldavService:
    WELL_KNOWN = "/.well-known/caldav"
    HOME_PROP = f"{{{C}}}calendar-home-set"
    RESOURCE_TYPE = f"{{{C}}}calendar"

    def __init__(self, config: Config):
        self.config = config
        self.client = httpx.Client(
            auth=(config.username, config.get_password()),
            timeout=30, follow_redirects=True)
        self.calendars: list[dict] = []   # [{"name", "url"}]

    def _raise_for_status(self, r: httpx.Response) -> None:
        if r.status_code >= 500:
            log = self.config.dir / "server-error.log"
            self.config.dir.mkdir(parents=True, exist_ok=True)
            log.write_text(r.text, encoding="utf-8")
            raise CaldavError(f"サーバーエラー。ログ: {log}")
        if r.status_code == 401:
            raise CaldavError("認証に失敗しました。設定を確認してください。")
        if r.status_code >= 400:
            raise CaldavError(f"HTTP {r.status_code}: {r.text[:200]}")

    def _request(self, method: str, url: str, body: str,
                 depth: str) -> ET.Element:
        r = self.client.request(method, url, content=body.encode(),
                                headers={"Depth": depth,
                                         "Content-Type": "application/xml; charset=utf-8"})
        self._raise_for_status(r)
        return ET.fromstring(r.content)

    def _propfind_href(self, url: str, prop_xml: str, prop_tag: str) -> str:
        body = (f'<?xml version="1.0"?><d:propfind xmlns:d="DAV:" '
                f'xmlns:c="{C}"><d:prop>{prop_xml}</d:prop></d:propfind>')
        root = self._request("PROPFIND", url, body, "0")
        el = root.find(f".//{prop_tag}/{{{D}}}href")
        if el is None or not el.text:
            raise CaldavError(f"{prop_tag} が見つかりません。")
        return urljoin(url, el.text)

    def discover(self) -> list[dict]:
        """well-known → principal → home → カレンダー一覧(仕様 §3.2)。"""
        base = self.config.server.rstrip("/")
        principal = self._propfind_href(
            base + self.WELL_KNOWN, "<d:current-user-principal/>",
            f"{{{D}}}current-user-principal")
        home = self._propfind_href(
            principal, f'<c:calendar-home-set xmlns:c="{C}"/>', self.HOME_PROP)
        body = (f'<?xml version="1.0"?><d:propfind xmlns:d="DAV:">'
                f"<d:prop><d:resourcetype/><d:displayname/></d:prop>"
                f"</d:propfind>")
        root = self._request("PROPFIND", home, body, "1")
        cals = []
        for resp in root.findall(f"{{{D}}}response"):
            types = resp.find(f".//{{{D}}}resourcetype")
            if types is None or types.find(self.RESOURCE_TYPE) is None:
                continue
            href = resp.findtext(f"{{{D}}}href", "")
            name = resp.findtext(f".//{{{D}}}displayname") or "カレンダー"
            cals.append({"name": name, "url": urljoin(home, href)})
        if not cals:
            raise CaldavError("カレンダーが見つかりません。")
        self.calendars = cals
        return cals

    def default_calendar(self) -> str:
        if not self.calendars:
            self.discover()
        return self.calendars[0]["url"]

    # ---- 取得(expand 必須 — 仕様 §3.2)------------------------------

    def get_events(self, calendar_url: str, start: datetime,
                   end: datetime) -> list[Event]:
        s = start.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        e = end.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        body = f'''<?xml version="1.0"?>
<c:calendar-query xmlns:d="DAV:" xmlns:c="{C}">
  <d:prop><d:getetag/>
    <c:calendar-data><c:expand start="{s}" end="{e}"/></c:calendar-data>
  </d:prop>
  <c:filter><c:comp-filter name="VCALENDAR"><c:comp-filter name="VEVENT">
    <c:time-range start="{s}" end="{e}"/>
  </c:comp-filter></c:comp-filter></c:filter>
</c:calendar-query>'''
        root = self._request("REPORT", calendar_url, body, "1")
        events: list[Event] = []
        for resp in root.findall(f"{{{D}}}response"):
            href = urljoin(calendar_url, resp.findtext(f"{{{D}}}href", ""))
            etag = resp.findtext(f".//{{{D}}}getetag") or ""
            data = resp.findtext(f".//{{{C}}}calendar-data") or ""
            if data:
                found = parse_events(data, href, etag)
                # 1 リソースに複数インスタンス = expand された繰り返し
                if len(found) > 1:
                    for ev in found:
                        ev.recurring = True
                events.extend(found)
        events.sort(key=lambda ev: (ev.start is None, ev.start))
        return events

    # ---- 作成・更新・削除 --------------------------------------------

    def create_event(self, calendar_url: str, ev: Event) -> None:
        ev.uid = ev.uid or str(uuid.uuid4())
        url = calendar_url.rstrip("/") + f"/{ev.uid}.ics"
        r = self.client.put(url, content=build_event_ics(ev).encode(),
                            headers={"Content-Type": "text/calendar; charset=utf-8",
                                     "If-None-Match": "*"})
        self._raise_for_status(r)

    def update_event(self, ev: Event) -> None:
        r = self.client.get(ev.href)
        self._raise_for_status(r)
        original = r.text
        # expand で受けたものを書き戻さない。元リソースが繰り返しなら拒否
        if "RRULE" in original or original.count("BEGIN:VEVENT") > 1:
            raise CaldavError("繰り返し予定はこのアプリでは編集できません。")
        etag = r.headers.get("ETag", ev.etag)
        headers = {"Content-Type": "text/calendar; charset=utf-8"}
        if etag:
            headers["If-Match"] = etag
        r = self.client.put(ev.href,
                            content=replace_event_props(original, ev).encode(),
                            headers=headers)
        if r.status_code == 412:
            raise CaldavError("他のクライアントで変更されています。"
                              "再読み込みしてやり直してください。")
        self._raise_for_status(r)

    def delete_event(self, ev: Event) -> None:
        headers = {"If-Match": ev.etag} if ev.etag else {}
        r = self.client.delete(ev.href, headers=headers)
        if r.status_code == 412:
            raise CaldavError("他のクライアントで変更されています。"
                              "再読み込みしてやり直してください。")
        self._raise_for_status(r)

    # ---- ics 招待の受理(仕様 §3.2)----------------------------------

    def add_invite(self, ics_text: str) -> str:
        # METHOD が残っていると他クライアントが招待として再解釈する
        lines = [ln for ln in unfold(ics_text)
                 if split_line(ln)[0] != "METHOD"]
        uid = ""
        for ln in lines:
            name, _, value = split_line(ln)
            if name == "UID":
                uid = value
                break
        if not uid:
            uid = str(uuid.uuid4())
            lines.insert(lines.index("BEGIN:VEVENT") + 1, f"UID:{uid}")
        url = self.default_calendar().rstrip("/") + f"/{uid}.ics"
        body = "\r\n".join(fold(x) for x in lines) + "\r\n"
        r = self.client.put(url, content=body.encode(),
                            headers={"Content-Type": "text/calendar; charset=utf-8"})
        self._raise_for_status(r)
        return uid

    # ---- キャッシュ用の差分検出(RFC 6578 — 仕様 §5.4)----------------

    def sync_changed(self, collection_url: str,
                     token: str) -> tuple[bool, str]:
        """(変化したか, 新トークン)。失敗は「変化あり」に倒す。"""
        body = (f'<?xml version="1.0"?><d:sync-collection xmlns:d="DAV:">'
                f"<d:sync-token>{token}</d:sync-token>"
                f"<d:sync-level>1</d:sync-level>"
                f"<d:prop><d:getetag/></d:prop></d:sync-collection>")
        try:
            root = self._request("REPORT", collection_url, body, "0")
            new_token = root.findtext(f"{{{D}}}sync-token") or ""
            changed = bool(root.findall(f"{{{D}}}response"))
            return changed, new_token
        except (CaldavError, httpx.HTTPError, ET.ParseError):
            return True, ""


def _cli() -> None:
    svc = CaldavService(Config.load())
    for cal in svc.discover():
        print(f"== {cal['name']}  {cal['url']}")
        now = datetime.now().astimezone()
        start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        end = (start.replace(year=start.year + 1, month=1)
               if start.month == 12 else start.replace(month=start.month + 1))
        for ev in svc.get_events(cal["url"], start, end):
            mark = "R" if ev.recurring else " "
            print(f"{mark} {ev.start}  {ev.summary}")


if __name__ == "__main__":
    _cli()
