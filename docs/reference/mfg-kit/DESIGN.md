# DESIGN.md — 実装設計(mfg)

仕様は `docs/` が正。本書は仕様書に書かれていない**実装判断**と**実装順序**を
決めるためのもの。仕様と食い違ったら仕様書を直してから本書を合わせる。

## 設計の原則(共通思想への接続。2026-07-09 追記)

正本は aiseed-migration-kit の DESIGN.md(§0 三つの閉じた世界からの開放・
§7 境界と検査可能性・§8 配置の理由)。mfg での具体化:

- **計算を自分の境界に**: データは SQLite 一ファイル(data/mfg.db)+
  ファイル添付——建物の中の、コピーひとつでバックアップできる境界。
  外に出るのは公開カタログ(静的 HTML/JSON、Cloudflare Pages)だけ
- **認証は層で分ける**: PocketBase は本人記録(answered_by)のための
  基本認証のみ。スタッフか否かの正は app_users.role(付与はユーザー管理
  view)。単一 IdP は作らない
- **AI はランタイムに置かない**: 開発時の道具に限る。処理経路(採番・
  見積・台帳)は凍結された決定的なコード。オープンウェイトは OSS と違い
  検査できないので、検査は出力(git 上のコード)に対して行う
- **配置**: 仮想化が効く仕事(公開カタログ)は外へ、状態を持つ仕事
  (API・DB・staff)は会社サーバーへ。1社1式・限界費用ゼロ

## 実装フェーズ

動くものを段階的に積む。各フェーズに完了条件を置く。

| Phase | 内容 | 完了条件 |
|---|---|---|
| 0 | 土台: venv・ruff・pytest・schema.sql 適用・core/(設定・DB接続)・GET /healthz | pytest が通り /healthz が 200 |
| 1 | 公開API: categories / products / qr ルーター(生SQL)・シードスクリプト | シード投入後、curl で製品一覧・QR PNG が取れる |
| 2 | 顧客API: PocketBase トークン検証・app_users 自動作成・cart → quotes(採番)→ messages・mail.py | pytest で 依頼送信→採番→メール送信(モック)の一連が通る |
| 3 | 静的生成: services/staticgen + site/ テンプレ → dist/(HTML+catalog JSON)。cf-publish で Pages/R2 へ | ローカルで dist/ が生成され、手元ブラウザでカタログが見える |
| 4 | 社内アプリ: staff/(Flet)。見積対応→製品管理→在庫参照→xlsx台帳→QRラベルPDF→ユーザー管理 の順 | 見積対応と xlsx 出力が実データで動く |
| 5 | 顧客アプリ: app/(Flutter)。カタログ(静的JSON)→ 認証 → カート→依頼→やり取り → /scan | Web ビルドで依頼〜回答受領〜発注の一巡ができる |
| 6 | deploy/: Caddyfile・systemd unit・expired 自動クローズ(systemd timer)・バックアップ・リストア手順書 | 手順書どおりに再構築できる |

社内アプリ(4)を Flutter(5)より先にするのは、見積回答ができないと
顧客アプリの一巡(依頼→回答→発注)を通しで確認できないため。

## 実装判断(仕様書に無いことの決定)

### DB(2026-07-08 変更: SQLite)
- **SQLite(WAL)+aiosqlite+生SQL。ORM は使わない。**
  PostgreSQL から変更した(規模に対して運用が重い。1社1式・同一
  サーバー内アクセスのみなら SQLite で足り、テストも本番と同じ
  エンジンで今すぐ回せる)
- データは `data/mfg.db` の1ファイル。PocketBase も SQLite なので、
  会社に渡すデータは「SQLite ファイル2つ+画像・添付フォルダ」に揃う
- db/schema.sql を唯一のスキーマ定義にする(ORM モデルとの二重管理を
  しない)。specs 等の JSON は TEXT 保存でアプリが json.loads/dumps
- 接続ごとに PRAGMA foreign_keys=ON・busy_timeout。DB 作成時に WAL 化
- 応答の整形は Pydantic(schemas/)が担う
- 大規模顧客で PostgreSQL が必要になったらその時に書き直す
  (先回りの抽象層は作らない)

### 採番(確定済み。SQLite 版に更新)
- `BEGIN IMMEDIATE` のトランザクション内で
  `MAX(quote_seq)+1`(書き込みロックで自然に直列化。リトライ経路なし)
- `quote_no = f"{year}-{seq:05d}"`(例 2026-00042)。年は **JST** で判定

### PocketBase トークン検証
- ミドルウェアが Bearer トークンを PocketBase の `auth-refresh` へ投げて検証し、
  結果(user id・メール・表示名)を **TTL 60秒でメモリキャッシュ**
  (毎リクエストの PB 往復を避ける。ローカル署名検証は PB の実装詳細に
  依存するため採らない)
- 初回アクセス時に app_users へ INSERT(role='customer', 承認待ちは
  is_suspended ではなく「admin が承認するまで見積依頼不可」フラグを
  持たせず、**登録=利用可・admin は凍結のみ**とする ※要確認1
- staff / admin の役付けは admin が社内アプリから行う(app_users.role 更新)

### 既読(read_at)
- 受け手がスレッドを開いた時点で、相手側からの未読メッセージへ一括
  `read_at = now()`
  - 顧客: GET /quotes/{id}/messages 実行時
  - 社内: Flet の見積詳細 view 表示時(services 経由)
- 通知はメール即時送信(確定済み)なので read_at は画面表示専用

### 添付ファイル
- 保存先: `data/files/{quote_id}/{message_id}.{ext}`(ext は pdf/png/jpg のみ許可、
  MIME とマジックバイトで検査、10MB 上限)
- ダウンロード API: `GET /quotes/{id}/files/{message_id}`(認可: 当該 quote の
  顧客本人のみ。社内は Flet がファイル直読み)— 02_api.md に追記する ※要確認2
- Caddy の /images は**製品写真専用**(公開・長期キャッシュ)。添付は
  認可が要るため必ず API 経由

### 見積番号 QR の飛び先
- QR 内容は `https://{app_domain}/q/{quote_no}`
- 顧客アプリに `/q/:no` ルートを追加し、`GET /quotes/by-no/{quote_no}`
  (認証必須・所有者のみ)で id を引いて詳細へ遷移。他人の番号は 404
- 製品 QR は `https://{app_domain}/p/{code}`(既存ルート)
- 03_apps.md のルート表に /q/:no を追記する ※要確認2

### 静的生成と配信
- `services/staticgen.py`: products(公開・有効分)から
  - `catalog.json`(分類+製品一覧。顧客アプリのカタログ系 Widget が読む)
  - 製品ページ等の静的 HTML(site/ の Jinja2 テンプレートを使用)
  を `site/dist/` に生成
- アップロードは **cf-publish**(自作 CLI)で Pages(HTML)と R2(JSON)へ。
  製品保存時は staff アプリ→services が生成+アップロードまで実行、
  日次は systemd timer
- 開発中の Pages/R2 への実アップロードは行わない(生成まで。公開操作は
  ユーザー承認事項)
- HTML はシステムフォントスタック・Web フォント不使用

### メール
- `services/mail.py` に集約。smtplib で `SMTP_HOST`(既定 localhost:587)へ。
  環境変数: SMTP_HOST / SMTP_PORT / SMTP_USER / SMTP_PASS / MAIL_FROM /
  COMPANY_MAIL_TO(会社側通知先)
- プレーンテキスト・日本語。テンプレは mail.py 内の関数
  (依頼受付/新着メッセージ/expired 通知の3種から開始)
- 開発・テストではコンソール出力バックエンドに切替(環境変数)

### 定期ジョブ(expired)
- `backend/app/jobs/expire.py` を CLI(`python -m app.jobs.expire`)として実装し、
  systemd timer で日次実行。requested のまま `EXPIRE_DAYS`(既定14)経過を
  expired へ更新し、顧客・会社へメール通知
- アプリ内スケジューラは使わない(再起動・多重起動の考慮を消す)

### 開発環境
- Python 3.12 + `./.venv`(backend / staff / site 共通、リポジトリ直下)
- DB は SQLite ファイル(`scripts/db-init` で作成。インストール物なし)。
  PocketBase のみ単一バイナリをローカルに置く(Docker 不使用)。
  設定は `.env`(DB_PATH / PB_URL)。本番も同型
- `.zed/tasks.json` に api(uvicorn)・staff(flet run --web)・test(pytest)・
  gen(静的生成)のタスクを登録
- 主要コマンドは Makefile ではなく `scripts/`(短いハイフン名)に置く

### テスト方針(pytest)
- 対象: 正常系・認可(他人の quote に触れない)・バリデーション+採番の直列化
  (同時 INSERT で重複しないこと)
- DB はテストごとに tmp の SQLite ファイルへ schema.sql を適用して実行
  (本番と同じエンジン。サンプルデータは site/sample.json を共用)
- PocketBase 検証はミドルウェアを差し替えてスタブ化(PB 本体はテスト不要)

## 確認済み事項(2026-07-07 決定)

1. **顧客登録**: 登録(メール確認)=即利用可。admin は事後凍結のみ。
   is_approved は追加しない(S-07 の「承認」は凍結解除を含む運用語と解釈)
2. **仕様書への追記**: 添付ダウンロード API を 02_api.md、/q/:no ルートを
   03_apps.md に反映済み
3. **開発機**: Docker 不使用。DB は SQLite に変更(2026-07-08、
   本番ごと。上記「DB」参照)。PocketBase のみローカルに置く

## しない(CLAUDE.md の再確認)

在庫・価格の本 DB 保存 / 決済 / 公開 API の /staff/* / 共有 UI パッケージ /
状態管理ライブラリ(Flutter)/ 外部 Office API / Web フォント /
ランタイムへの AI 組み込み
