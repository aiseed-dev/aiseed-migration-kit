# aiseed-migration-kit

組織のITを、AI とともに開いた自営スタックへ移行するためのツール一式。
ねらいは **Microsoft 365 + Azure・業務システム・CMS という三つの閉じた
世界からの開放**——一つの開いた土台(git・SQL・メール・OnlyOffice・静的公開)に
置き換え、公開標準(Markdown/YAML/SQL/Python)で繋ぐことで、この仕組み
自体からも出られる形にする。設計は [DESIGN.md](DESIGN.md) が正。

現在の実装範囲は移行の入口=公開Web(脱CMS: 静的化・Cloudflare Pages 配信)
と問い合わせ(申込様式+メール受付)。業務システム・文書・メール側の部品は
DESIGN.md §2・§13 と [seminar-kit](https://github.com/aiseed-dev/seminar-kit)
(実装例)を参照。

- 取り込み(ingest)→ 分類(classify)→ Markdown化(convert)→
  生成(build)→ 配信(publish)のパイプライン
- 問い合わせは申込様式(xlsx)+メール受付(inquiry)。Webフォームは作らない
- 内容の正は `sites/<name>/content/`(Markdown または AsciiDoc+frontmatter)。
  git で版管理する

## セットアップ(コピペ2行)

```bash
python3 -m venv .venv && .venv/bin/pip install -e '.[dev]' ./vendor/cf-publish
source .venv/bin/activate
```

cf-publish は同梱(vendor/。PyPI 公開後は通常の `pip install cf-publish` に
切り替える)。日本語 Markdown の改行・強調を良くする
[mdit-py-cjk-friendly](https://github.com/aiseed-dev/mdit-py-cjk-friendly)
があれば自動で使う(無くても動く):

```bash
pip install git+https://github.com/aiseed-dev/mdit-py-cjk-friendly
```

`content/` に `.adoc`(AsciiDoc)ファイルを置くと
[pyasciidoc](https://github.com/aiseed-dev/pyasciidoc)で変換される
(CJK対応の見出し・強調。既存のPython AsciiDoc実装は和文隣接の強調記法を
認識しないため、mdit-py-cjk-friendlyの境界判定を土台に新規実装したもの。
`.md`と違い代替レンダラが無いため、未導入で`.adoc`を使うとビルド時に
分かりやすいエラーになる):

```bash
pip install git+https://github.com/aiseed-dev/pyasciidoc
```

## 使い方

```bash
amig new sites/mysite            # サイトの雛形を作る(site.yaml を編集)
amig ingest sites/mysite ~/data  # 元データ(HTML等)を取り込む
amig classify sites/mysite       # 記事/一覧に分類(結果は人が直せる)
amig convert sites/mysite        # 記事を content/*.md へ機械変換(下書き)
amig build sites/mysite          # dist/ を生成
amig publish sites/mysite        # Cloudflare Pages へ配信(運用判断で実行)
```

- convert は**既存の .md を上書きしない**(人の仕上げを守る。--force で上書き)
- publish の認証は環境変数(CLOUDFLARE_API_TOKEN / CLOUDFLARE_ACCOUNT_ID)
  または ~/.config/cloudflare/pages.env

### 問い合わせ(申込様式+メール受付)

```bash
amig forms sites/mysite            # 様式 xlsx を forms-out/ へ生成
amig macro sites/mysite contact    # 様式マクロ(OnlyOffice JS)を出力
amig mailin sites/mysite --once    # 受付メールを担当フォルダへ振り分け
```

- 様式の定義は site.yaml の inquiry.forms(神Excel=唯一のフォーム定義)
- **様式は公開サイトからダウンロードできる**(build が forms-out/ を
  dist/forms/ に載せる。`inquiry: {publish_forms: false}` で非公開にもできる)
- **受付アドレスはページ本文に書かず、様式の中にだけ**置く(ボット収集対策。
  様式由来でないメールには自動返信しないので、スパムへの逆流も起きない。
  DESIGN.md §5)
- mailin の接続は環境変数で渡す: AMIG_IMAP_HOST / AMIG_IMAP_USER /
  AMIG_IMAP_PASS / AMIG_SUBMIT_ADDR(送信も使うなら AMIG_SMTP_*)
- 受信の状態=IMAPフォルダ(INBOX=未着手 / staff/<key>=担当へ / pending=未処理)。
  pending の添付を人が開くときはマクロ無効の環境(OnlyOffice の閲覧等)で

## 開発

```bash
.venv/bin/pytest          # テスト
.venv/bin/ruff check src tests
amig build sites/example && python -m http.server -d sites/example/dist 8000
```

## 構成

```
src/amig/       ingest / classify / convert / build / publish / cli
  inquiry/        forms(様式生成)/ parse(読み取り)/ mailin(振り分け)/ mail(送信)
  templates/      既定テンプレート(サイトの templates/ で上書き可)
vendor/cf-publish/  Cloudflare 配信(同梱。PyPI 公開までの暫定)
sites/example/      出力例(そのまま build できる)
```

ライセンス: AGPL-3.0
