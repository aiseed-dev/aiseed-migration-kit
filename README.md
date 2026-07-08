# cms-migration-kit(CMS移行キット)

動的CMSのサイトを、静的配信(Cloudflare Pages)+自営スタックへ移行する
ためのツール一式。設計は [DESIGN.md](DESIGN.md) が正。

- 取り込み(ingest)→ 分類(classify)→ Markdown化(convert)→
  生成(build)→ 配信(publish)のパイプライン
- 問い合わせは申込様式(xlsx)+メール受付(inquiry)。Webフォームは作らない
- 内容の正は `sites/<name>/content/`(Markdown+frontmatter)。git で版管理する

## セットアップ(コピペ2行)

```bash
python3 -m venv .venv && .venv/bin/pip install -e '.[dev]' ./vendor/cf-publish
source .venv/bin/activate
```

cf-publish は同梱(vendor/。PyPI 公開後は通常の `pip install cf-publish` に
切り替える)。日本語 Markdown の改行・強調を良くする mdit-py-cjk-friendly が
あれば自動で使う(無くても動く):

```bash
pip install mdit-py-cjk-friendly
```

## 使い方

```bash
cmsmig new sites/mysite            # サイトの雛形を作る(site.yaml を編集)
cmsmig ingest sites/mysite ~/data  # 元データ(HTML等)を取り込む
cmsmig classify sites/mysite       # 記事/一覧に分類(結果は人が直せる)
cmsmig convert sites/mysite        # 記事を content/*.md へ機械変換(下書き)
cmsmig build sites/mysite          # dist/ を生成
cmsmig publish sites/mysite        # Cloudflare Pages へ配信(運用判断で実行)
```

- convert は**既存の .md を上書きしない**(人の仕上げを守る。--force で上書き)
- publish の認証は環境変数(CLOUDFLARE_API_TOKEN / CLOUDFLARE_ACCOUNT_ID)
  または ~/.config/cloudflare/pages.env

### 問い合わせ(申込様式+メール受付)

```bash
cmsmig forms sites/mysite            # 様式 xlsx を forms-out/ へ生成
cmsmig macro sites/mysite contact    # 様式マクロ(OnlyOffice JS)を出力
cmsmig mailin sites/mysite --once    # 受付メールを担当フォルダへ振り分け
```

- 様式の定義は site.yaml の inquiry.forms(神Excel=唯一のフォーム定義)
- **様式と受付アドレスは公開サイトに置かない**(個別に渡す。DESIGN.md §5)
- mailin の接続は環境変数で渡す: CMSMIG_IMAP_HOST / CMSMIG_IMAP_USER /
  CMSMIG_IMAP_PASS / CMSMIG_SUBMIT_ADDR(送信も使うなら CMSMIG_SMTP_*)
- 受信の状態=IMAPフォルダ(INBOX=未着手 / staff/<key>=担当へ / pending=未処理)。
  pending の添付を人が開くときはマクロ無効の環境(OnlyOffice の閲覧等)で

## 開発

```bash
.venv/bin/pytest          # テスト
.venv/bin/ruff check src tests
cmsmig build sites/example && python -m http.server -d sites/example/dist 8000
```

## 構成

```
src/cmsmig/       ingest / classify / convert / build / publish / cli
  inquiry/        forms(様式生成)/ parse(読み取り)/ mailin(振り分け)/ mail(送信)
  templates/      既定テンプレート(サイトの templates/ で上書き可)
vendor/cf-publish/  Cloudflare 配信(同梱。PyPI 公開までの暫定)
sites/example/      出力例(そのまま build できる)
```

ライセンス: AGPL-3.0
