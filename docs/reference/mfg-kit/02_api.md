# 02 API 仕様(mfg)

ベースパス: /api/v1。認証: クライアントは PocketBase でログインし、
Authorization: Bearer で送る。ミドルウェアが検証し、初回アクセス時に
app_users を自動作成。ページング: ?cursor=<created_at>&limit=20。
日時は ISO 8601(UTC)。エラー形式: { "detail": "...", "code": "..." }。

## 公開(認証不要)
| メソッド/パス | 内容 |
|---|---|
| GET /categories | 分類一覧 |
| GET /products | 公開製品一覧(?category=&q=) |
| GET /products/{code} | 製品詳細(製品コードで引く。QRの飛び先) |
| GET /qr/p/{code}.png | 製品QR(銘板・紙カタログ用。segno生成) |
| GET /qr/q/{quote_no}.png | 見積番号QR(見積書・納品書に印刷) |

## 顧客(認証必須)
| メソッド/パス | 内容 |
|---|---|
| GET /cart | 見積カート |
| PUT /cart/items/{code} | 追加・数量/個別仕様の変更(キーは製品コード。UUID は外に出さない) |
| DELETE /cart/items/{code} | 削除 |
| POST /quotes | 見積依頼送信 {note}。カートを quote_items へ移し、quote_no をDB通し番号(年+連番)で採番。会社へメール通知 |
| GET /quotes | 自分の依頼一覧(状態・最終メッセージ付き) |
| GET /quotes/{id} | 依頼詳細 |
| PATCH /quotes/{id} | 顧客: cancelled / ordered(発注意向。ordered_at を記録=台帳の計上基準)。requested / answered からのみ可(終端状態からは変更不可) |
| GET/POST /quotes/{id}/messages | やり取り。添付は multipart(PDF/画像、10MBまで。file_path に保存)。送信時に相手側へ即時メール通知(顧客→会社、会社→顧客) |
| GET /quotes/{id}/files/{message_id} | 添付ダウンロード(当該 quote の顧客本人のみ。社内は Flet がファイル直読み) |
| GET /quotes/by-no/{quote_no} | 見積番号から自分の依頼を引く(QR /q/:no の飛び先。他人の番号は 404) |

## 社内機能(公開APIとしては実装しない)

社内アプリ(staff/ = Flet)は backend の services を import して
DB直結で動く。見積対応(answered_at /
answered_by の記録)・製品管理(保存時に静的カタログ再生成)・
基幹APIからの在庫/価格取得・xlsx台帳(quotes/orders)・QRラベルPDF・
顧客承認/凍結は、すべて services に実装し Flet から呼ぶ。
公開 API に /staff/* /admin/* を作らないこと(攻撃面を増やさない)。
顧客向けメッセージへの返信も services 経由(メール通知が同じ経路で飛ぶ)。

## 基幹API連携(在庫・価格の正は会社側)

- 会社の基幹(販売管理・生産管理)側に最小API(標準HTTP+JSON)を用意:
  `GET /inventory` → `[{code, qty, price?}, ...]`
- 基幹が無い会社: 在庫・価格とも「要見積」表示で運用開始し、
  基幹APIができた段階でつなぐ(段階導入)
- 本システムは在庫・価格を保存しない。表示粒度(内数)は会社ごとに決定、
  テスト段階は正確な数を表示
- つなぎ方は会社側Webが自システムに繋ぐのと同じ標準HTTP+JSONに揃える

## 静的配信・定期ジョブ

- 製品カタログ(公開分)をJSON+静的HTMLに生成し R2/Pages へ
  (製品保存時+日次)。在庫・価格は含めない
- quotes の requested 放置(既定14日)を expired に自動クローズ+通知
