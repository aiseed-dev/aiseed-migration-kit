# CLAUDE.md — 実装指示(seminar)

このリポジトリは研修・セミナー管理システムのモノレポ。仕様は `docs/` が正。
仕様に無い判断は推測せず、選択肢を提示して確認する。

## 命名規約
手で打つ名前は短く、アンダースコアを使わない(複数語はハイフン)。
例外は言語規則の箇所のみ(Python モジュールは小文字で短く)。

## リポジトリ構成
```
backend/   FastAPI(AGPL-3.0)
  app/{core,models,schemas,routers,services}/  tests/
  routers: courses, docs(編集セッション発行・保存コールバック), qr
  services: mail(送信。既存SMTPリレー・抽象化),
            mailin(申込受信箱の IMAP 監視・xlsx 読み取り・登録・自動返信),
            forms(講座ごとの申込様式 xlsx 生成),
            no採番, 静的再生成, xlsx帳票(openpyxl。名簿・実績台帳),
            QR(segno)
site/      公開静的サイト生成(Python。courses → HTML+申込様式xlsx+
           ブラウザ記入ページ〔DocsAPI JS を読む静的1枚〕。Pages 配信)
office/    事務局アプリ(Flet / Python)。backend の models / services を
           import し DB 直結。ログインのみ PocketBase(操作者記録)。
           機関内サーバーで flet run --web(LAN / SSH トンネル)
db/schema.sql   正のスキーマ。変更時はここを先に直す
deploy/
```

## Python 規約
Python 3.12+ / FastAPI / SQLAlchemy 2.0 / Pydantic v2 / ruff / pytest。
型ヒント必須。xlsx の読み書きは openpyxl に統一(申込様式の読み取りは
名前付きセル参照で行い、セル座標のハードコードをしない)。
メール送信は services/mail.py、受信処理は services/mailin.py に集約。
読み取れない申込は必ず事務局の未処理キューに落とす(黙って捨てない)。
OnlyOffice Docs(Document Server)との統合は JWT 必須。ブラウザ記入も
メール添付も FAX も、**様式 xlsx を唯一のフォーム定義**とし、
読み取りは同一のパーサ(名前付きセル)に集約する。

## してはいけないこと
- 個人情報を返す公開 API・画面(ブラウザ記入は空の様式の一時コピーのみ。
  事前登録情報を Web に表示・プリフィルする機能を作らない。
  引き当てはメール経路のみ)
- 申込者のアカウント・ログイン機能(事前登録は初回申込の自動 upsert)
- 決済の実装(受講料は表示のみ)
- 個人情報を返す公開 API(公開 API は講座情報と QR のみ)
- 事務局機能の公開 API 化(/staff/* /admin/* を作らない。office/ は DB直結)
- アンケート機能の実装(紙+ローカルLLM処理が仕様。docs/05)
- 独自の Web 申込フォーム・SPA の実装(様式 xlsx が唯一のフォーム定義。
  Flutter はこのシステムでは使わない)
- WordPress プラグインの開発(置き換えが目的。WordPress 側は触らない)
