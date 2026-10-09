"""CardDAV (RFC 6352) — 連絡先。vCard 3.0 のみ。

対応プロパティ(FN/N/EMAIL/TEL/ORG/NOTE)以外の行は保持して
そのまま返す(round-trip 保証 — 仕様 §3.3)。
"""
from __future__ import annotations

import uuid
import xml.etree.ElementTree as ET
from urllib.parse import urljoin

import httpx

from .caldav_service import escape, fold, split_line, unescape, unfold
from .models import Config, Contact

D = "DAV:"
CR = "urn:ietf:params:xml:ns:carddav"

# round-trip で保持する(= こちらが所有しない)行の判定に使う
OWNED = {"FN", "N", "EMAIL", "TEL", "ORG", "NOTE", "UID", "VERSION",
         "BEGIN", "END"}


class CarddavError(Exception):
    pass


def parse_vcard(text: str, href: str = "", etag: str = "") -> Contact:
    c = Contact(uid="", fn="", href=href, etag=etag)
    extra: list[str] = []
    for line in unfold(text):
        name, _, value = split_line(line)
        if name == "UID":
            c.uid = value
        elif name == "FN":
            c.fn = unescape(value)
        elif name == "N":
            parts = value.split(";")
            c.family = unescape(parts[0]) if parts else ""
            c.given = unescape(parts[1]) if len(parts) > 1 else ""
        elif name == "EMAIL":
            c.emails.append(unescape(value))
        elif name == "TEL":
            c.tels.append(unescape(value))
        elif name == "ORG":
            c.org = unescape(value.split(";")[0])
        elif name == "NOTE":
            c.note = unescape(value)
        elif name not in OWNED:
            extra.append(line)
    # 保持行は Contact に載せず、更新時に元テキストから合成し直す
    c._extra = extra  # type: ignore[attr-defined]
    return c


def build_vcard(c: Contact, extra: list[str] | None = None) -> str:
    lines = ["BEGIN:VCARD", "VERSION:3.0", f"UID:{c.uid}",
             "FN:" + escape(c.fn),
             f"N:{escape(c.family)};{escape(c.given)};;;"]
    for e in c.emails:
        lines.append("EMAIL;TYPE=INTERNET:" + escape(e))
    for t in c.tels:
        lines.append("TEL:" + escape(t))
    if c.org:
        lines.append("ORG:" + escape(c.org))
    if c.note:
        lines.append("NOTE:" + escape(c.note))
    lines += extra or []
    lines.append("END:VCARD")
    return "\r\n".join(fold(x) for x in lines) + "\r\n"


class CarddavService:
    def __init__(self, config: Config):
        self.config = config
        self.client = httpx.Client(
            auth=(config.username, config.get_password()),
            timeout=30, follow_redirects=True)
        self.addressbooks: list[dict] = []

    def _raise_for_status(self, r: httpx.Response) -> None:
        if r.status_code >= 500:
            log = self.config.dir / "server-error.log"
            self.config.dir.mkdir(parents=True, exist_ok=True)
            log.write_text(r.text, encoding="utf-8")
            raise CarddavError(f"サーバーエラー。ログ: {log}")
        if r.status_code == 401:
            raise CarddavError("認証に失敗しました。設定を確認してください。")
        if r.status_code >= 400:
            raise CarddavError(f"HTTP {r.status_code}: {r.text[:200]}")

    def _request(self, method: str, url: str, body: str,
                 depth: str) -> ET.Element:
        r = self.client.request(method, url, content=body.encode(),
                                headers={"Depth": depth,
                                         "Content-Type": "application/xml; charset=utf-8"})
        self._raise_for_status(r)
        return ET.fromstring(r.content)

    def _propfind_href(self, url: str, prop_xml: str, prop_tag: str) -> str:
        body = (f'<?xml version="1.0"?><d:propfind xmlns:d="DAV:" '
                f'xmlns:cr="{CR}"><d:prop>{prop_xml}</d:prop></d:propfind>')
        root = self._request("PROPFIND", url, body, "0")
        el = root.find(f".//{prop_tag}/{{{D}}}href")
        if el is None or not el.text:
            raise CarddavError(f"{prop_tag} が見つかりません。")
        return urljoin(url, el.text)

    def discover(self) -> list[dict]:
        base = self.config.server.rstrip("/")
        principal = self._propfind_href(
            base + "/.well-known/carddav", "<d:current-user-principal/>",
            f"{{{D}}}current-user-principal")
        home = self._propfind_href(
            principal, f'<cr:addressbook-home-set xmlns:cr="{CR}"/>',
            f"{{{CR}}}addressbook-home-set")
        body = ('<?xml version="1.0"?><d:propfind xmlns:d="DAV:">'
                "<d:prop><d:resourcetype/><d:displayname/></d:prop>"
                "</d:propfind>")
        root = self._request("PROPFIND", home, body, "1")
        books = []
        for resp in root.findall(f"{{{D}}}response"):
            types = resp.find(f".//{{{D}}}resourcetype")
            if types is None or types.find(f"{{{CR}}}addressbook") is None:
                continue
            href = resp.findtext(f"{{{D}}}href", "")
            name = resp.findtext(f".//{{{D}}}displayname") or "連絡先"
            books.append({"name": name, "url": urljoin(home, href)})
        if not books:
            raise CarddavError("アドレス帳が見つかりません。")
        self.addressbooks = books
        return books

    def default_addressbook(self) -> str:
        if not self.addressbooks:
            self.discover()
        return self.addressbooks[0]["url"]

    def get_contacts(self, book_url: str | None = None) -> list[Contact]:
        url = book_url or self.default_addressbook()
        body = (f'<?xml version="1.0"?>'
                f'<cr:addressbook-query xmlns:d="DAV:" xmlns:cr="{CR}">'
                f"<d:prop><d:getetag/><cr:address-data/></d:prop>"
                f"</cr:addressbook-query>")
        root = self._request("REPORT", url, body, "1")
        contacts = []
        for resp in root.findall(f"{{{D}}}response"):
            href = urljoin(url, resp.findtext(f"{{{D}}}href", ""))
            etag = resp.findtext(f".//{{{D}}}getetag") or ""
            data = resp.findtext(f".//{{{CR}}}address-data") or ""
            if data:
                contacts.append(parse_vcard(data, href, etag))
        contacts.sort(key=lambda c: c.fn)
        return contacts

    def create_contact(self, c: Contact) -> None:
        c.uid = c.uid or str(uuid.uuid4())
        url = self.default_addressbook().rstrip("/") + f"/{c.uid}.vcf"
        r = self.client.put(url, content=build_vcard(c).encode(),
                            headers={"Content-Type": "text/vcard; charset=utf-8",
                                     "If-None-Match": "*"})
        self._raise_for_status(r)

    def update_contact(self, c: Contact) -> None:
        r = self.client.get(c.href)
        self._raise_for_status(r)
        etag = r.headers.get("ETag", c.etag)
        extra = getattr(parse_vcard(r.text), "_extra", [])
        headers = {"Content-Type": "text/vcard; charset=utf-8"}
        if etag:
            headers["If-Match"] = etag
        r = self.client.put(c.href, content=build_vcard(c, extra).encode(),
                            headers=headers)
        if r.status_code == 412:
            raise CarddavError("他のクライアントで変更されています。"
                               "再読み込みしてやり直してください。")
        self._raise_for_status(r)

    def delete_contact(self, c: Contact) -> None:
        headers = {"If-Match": c.etag} if c.etag else {}
        r = self.client.delete(c.href, headers=headers)
        if r.status_code == 412:
            raise CarddavError("他のクライアントで変更されています。"
                               "再読み込みしてやり直してください。")
        self._raise_for_status(r)

    def sync_changed(self, token: str) -> tuple[bool, str]:
        body = ('<?xml version="1.0"?><d:sync-collection xmlns:d="DAV:">'
                f"<d:sync-token>{token}</d:sync-token>"
                "<d:sync-level>1</d:sync-level>"
                "<d:prop><d:getetag/></d:prop></d:sync-collection>")
        try:
            root = self._request("REPORT", self.default_addressbook(),
                                 body, "0")
            new_token = root.findtext(f"{{{D}}}sync-token") or ""
            changed = bool(root.findall(f"{{{D}}}response"))
            return changed, new_token
        except (CarddavError, httpx.HTTPError, ET.ParseError):
            return True, ""


def _cli() -> None:
    svc = CarddavService(Config.load())
    for c in svc.get_contacts():
        print(f"{c.fn}  {', '.join(c.emails)}  {', '.join(c.tels)}")


if __name__ == "__main__":
    _cli()
