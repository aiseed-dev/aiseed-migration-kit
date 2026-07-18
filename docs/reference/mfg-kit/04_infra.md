# 04 インフラ・デプロイ(mfg)

会社の自営サーバーに導入(1社1式)。物理は2台構成。

## 2台構成(役割分離)

- **メール専用機**: Stalwart のみ(DKIM/SPF/DMARC/PTR 設定)。
  省電力機で**常時稼働**(メール配送は止められない:相手サーバーの
  再送に依存し、停止はエラー・遅延として相手に見える)
- **アプリ機**: FastAPI・SQLite(data/mfg.db の1ファイル)・PocketBase・
  staff(Flet)・(任意で OnlyOffice)。**計画停止・深夜メンテ可**
- 家庭用/事業所ルータのポート転送で振り分け:
  25/465/587/993 → メール機、443 → アプリ機
- アプリ機からのメール送信は宅内LAN経由でメール機へ SMTP

## 公開と停止対策

- Cloudflare: DNS+プロキシ(Full Strict・Origin CA 証明書)。
  443 は Cloudflare IP レンジのみ許可(nftables、リスト週次更新)
- **公開静的サイトと顧客アプリ(Flutter Web)は Cloudflare Pages 配信**
  (アプリ機停止中も画面は出る)
- 製品カタログ JSON は R2 から配信(製品保存時+日次で再生成。
  在庫・価格は含めない=必ず基幹API経由)
- API 不応答時は顧客アプリの各 Widget が error 状態で
  「メンテナンス中(◯時〜◯時)」を表示。Cloudflare の
  Always Online / カスタムエラーページも設定
- リバースプロキシは Caddy(/api→FastAPI、/auth→PocketBase、
  /images→file_server+長期キャッシュ)

## バックアップ・運用

- 毎晩: SQLite の整合バックアップ(`.backup`)+画像・添付+PocketBase
  データを rclone で R2/B2 へ。保持14日。リストアは「ファイルを
  置き戻すだけ」を手順書にして deploy/ に置く(目標復旧: 半日)
- systemd で FastAPI / PocketBase / staff を常駐。デプロイは
  git pull+restart で足りる(短時間停止は許容)
- 監視は最小限: Cloudflare Health Check(5分間隔・メール通知)+
  週次の df レポート
