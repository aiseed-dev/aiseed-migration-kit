# CLAUDE.md — 実装指示(mfg)

このリポジトリは製造業向け 見積・受注アプリのモノレポ。仕様は `docs/` が正。
実装前に README → docs/01 → 該当仕様の順に読むこと。仕様に無い判断が
必要になったら、推測で進めず選択肢を提示して確認する。

**仕様変更は安い。** 仕様が変わったら docs/ を直し、実装を単純に書き直して
追随させる。将来の変更に備えた設定項目・フラグ・抽象層・拡張ポイントは
作らない(変更が来たときに変えればよい)。迷ったら常に単純な方を選ぶ。

## 命名規約(個人開発・手打ちの負担を減らす)

手で打つ名前(ディレクトリ・ファイル・スクリプト・ブランチ)は
**短く、アンダースコアを使わない**。複数語はハイフンで。
例外は言語規則の箇所のみ(Python モジュールは小文字で短く)。

## リポジトリ構成

```
backend/   FastAPI(AGPL-3.0)
  app/
    main.py
    core/      設定・DB接続(aiosqlite)・PocketBase トークン検証
    schemas/   Pydantic v2
    routers/   products, cart, quotes, qr(公開・顧客向けのみ)
    services/  業務ロジック・メール送信・台帳生成・静的サイト再生成
  tests/
site/      公開静的サイト生成(Python。products → HTML。Pages 配信)
app/       顧客アプリ(Flutter: iOS / Android / Web)。
           **共有パッケージは作らない。** api_client・session・
           モデル・共通 Widget もすべて app/lib 内に自己完結で実装する
staff/     社内アプリ(Flet / Python)。backend の services を
           import し DB 直結。ログインのみ PocketBase
           (answered_by の本人記録のため)。会社サーバー内で
           `flet run --web`(社内LAN / SSH トンネル)
db/schema.sql   正のスキーマ。変更時はここを先に直す
deploy/    Caddyfile・systemd・バックアップスクリプト
```

## Python 規約

- Python 3.12+ / FastAPI / SQLite+aiosqlite(生SQL。ORM は使わない。
  db/schema.sql が唯一のスキーマ定義。接続時に foreign_keys=ON)/ Pydantic v2
- ruff(format+lint)、型ヒント必須、pytest(正常系・認可・
  バリデーションを最低限)
- メール送信は `services/mail.py` に集約。実体は localhost の
  Stalwart へ SMTP。環境変数で外部リレーに切替可能にしておく
- 帳票(xlsx)は openpyxl でファイル直接生成。QR は segno。
  外部 Office API・帳票製品は使わない

## Flutter 規約(顧客アプリのみ)

- **Riverpod / provider / bloc 等の状態管理ライブラリは使わない**
- **小さな自己完結型 Widget** を徹底:
  - 各画面・部品は StatefulWidget が自分でデータ取得と状態を持つ。
    親から渡すのは ID とコールバックのみ
  - initState で fetch、setState で描画、mounted チェック必須
  - 3状態を統一: null=読込中(スケルトン)/ error(リトライボタン。
    API 不応答時は「メンテナンス中」表示)/ データ
- 1ファイル1Widget、1Widget 200行以内を目安に分割
- グローバル状態は認証トークン等の最小限を `Session` シングルトン1つに
- ルーティング: go_router のみ(Web ディープリンク・QR の飛び先)
- 許可パッケージ: http, go_router, image_picker, cached_network_image,
  intl, mobile_scanner。追加は理由を添えて確認
- UI は日本語。Material 3

## してはいけないこと

- 在庫・価格の本 DB 保存(正は会社側基幹。無ければ「要見積」表示)
- 決済・与信の実装
- SEO が要る公開ページの Flutter 実装(site/ の静的 HTML が担う)
- 社内アプリの Flutter 実装(staff/ は Flet)
- 公開 API への /staff/* /admin/* の実装(社内機能は services+Flet。
  攻撃面を増やさない)
- 共有 UI パッケージの作成(app/ は自己完結)
- 商材固有の法対応の先回り実装(案件ごとに確認してから)
