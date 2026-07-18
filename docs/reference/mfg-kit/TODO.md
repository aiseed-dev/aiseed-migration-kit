# 残タスク(2026-07-08 時点)

Phase 0〜6 は実装済み(テスト51件+Flutter analyze/test/build 通過)。
残りは結線・確認系と、意図的に保留した項目。

## 次にやる(結線・確認)

1. **PocketBase 実機疎通+ログイン差し替え** ✅ 2026-07-09(コミット 0d53cc1)
   - PB 疎通済み(superuser: admin@example.com / テストユーザー:
     customer@example.com・staff@example.com、パスワードは dev-pass-1234)
   - staff/ ログインは PB auth-with-password に差し替え済み。初回ブートストラップ
     (staff/admin 不在時の最初のログイン=admin)・customer 拒否・誤パスワード
     拒否を実機検証済み(staff@example.com が admin として登録済み)
   - `company` フィールドは**足さない**判断(NULL で動く。必要になったら追加)
   - 残: 顧客アプリのログイン/登録の実機確認(下記 2 の E2E と一緒に)
2. **ブラウザ E2E**: Flutter web ⇔ API ⇔ 静的JSON(CATALOG_URL)を通しで。
   依頼→staff 回答→発注の一巡

## 保留(採用判断が要る)

3. 顧客側の添付**ダウンロード**(認可ヘッダー付き取得。Web は blob 保存が必要)
4. トークン**永続化**(shared_preferences の追加確認。現状リロードで再ログイン)
5. 顧客側の **PDF 添付送信**(file_picker の追加確認。現状は画像のみ)

## 導入時(会社ごと・ユーザー操作)

6. cf-publish で site/dist を Pages/R2 へ公開(外部接続はユーザー操作)
7. Cloudflare 設定(DNS・Full Strict・Origin CA・nftables の CF レンジ許可)
8. 実サーバー構築: deploy/ の unit 配置・restore.md の実地確認・
   メール専用機(Stalwart+DKIM/SPF/DMARC/PTR)
9. `site/company.json`・`.env`(COMPANY_MAIL_TO / APP_BASE_URL / KIKAN_URL 等)を
   導入先の値に
