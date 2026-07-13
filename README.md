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
（PyPI公開済）があれば自動で使う(無くても動く):

```bash
pip install mdit-py-cjk-friendly
```

`content/` に `.adoc`(AsciiDoc)ファイルを置くと
[pyasciidoc](https://github.com/aiseed-dev/pyasciidoc)（PyPI公開済）で
変換される(CJK対応の見出し・強調・admonition・箇条書き。既存のPython
AsciiDoc実装は和文隣接の強調記法を認識しないため、mdit-py-cjk-friendly
の境界判定を土台に新規実装したもの。`.md`と違い代替レンダラが無いため、
未導入で`.adoc`を使うとビルド時に分かりやすいエラーになる):

```bash
pip install pyasciidoc
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
amig forms sites/mysite            # 様式(xlsx+記入用テキスト)を forms-out/ へ生成
amig macro sites/mysite contact    # 様式マクロ(OnlyOffice JS)を出力
amig ddl sites/mysite              # PostgreSQL DDL(CREATE TABLE)を出力
amig mailin sites/mysite --once    # 受付メールを担当フォルダへ振り分け
```

- 様式の唯一の定義は **様式プロファイル**(`forms/*.adoc`。AsciiDoc の
  ラベル付きリスト+SQL 語彙の制約。sites/example/forms/ 参照)。site.yaml の
  inquiry.forms はプロファイルへのパスの列。xlsx・記入用テキスト・DDL・
  検証モデルはすべて同じ定義から派生する(DESIGN.md §5)
- **様式は公開サイトからダウンロードできる**(build が forms-out/ を
  dist/forms/ に載せる。`inquiry: {publish_forms: false}` で非公開にもできる)
- **受付アドレスはページ本文に書かず、様式の中にだけ**置く(ボット収集対策。
  様式由来でないメールには自動返信しないので、スパムへの逆流も起きない。
  DESIGN.md §5)
- mailin の接続は環境変数で渡す: AMIG_IMAP_HOST / AMIG_IMAP_USER /
  AMIG_IMAP_PASS / AMIG_SUBMIT_ADDR(送信も使うなら AMIG_SMTP_*)
- 受信の状態=IMAPフォルダ(INBOX=未着手 / staff/<key>=担当へ / pending=未処理)。
  pending の添付を人が開くときはマクロ無効の環境(OnlyOffice の閲覧等)で

### 決裁部品(文書管理)

```bash
amig docindex docs/                # 決裁文書の属性インデックス(SQL)を出力
amig freeze 請求書.pdf --source docs/請求書.adoc   # 交付物の凍結記録を作成
```

- 決裁項目(`:approver:` `:status:` `:decided-on:` 等)は**文書内の AsciiDoc
  属性が正**。docindex の SQL を流し直せば DB(検索・一覧用の派生物)は
  いつでも再生成できる
- 決裁フローの実体は Forgejo のプルリクエスト(起案=ブランチ/決裁=マージ)。
  キットはフローを持たない(DESIGN.md §14)
- freeze は交付物(PDF/A)の SHA-256 と生成元(AsciiDoc+コミットハッシュ)を
  並べた記録(`<file>.hash.yaml`)を作る

## 開発

```bash
.venv/bin/pytest          # テスト
.venv/bin/ruff check src tests
amig build sites/example && python -m http.server -d sites/example/dist 8000
```

## 構成

```
src/amig/       ingest / classify / convert / build / publish / decision(決裁部品)/ cli
  inquiry/        profile(様式プロファイル解釈)/ spec(SQL 語彙・修復)/
                  derive(DDL・検証モデル・記入テキスト)/ forms(xlsx 生成)/
                  parse(読み取り)/ mailin(振り分け)/ mail(送信)
  templates/      既定テンプレート(サイトの templates/ で上書き可)
vendor/cf-publish/  Cloudflare 配信(同梱。PyPI 公開までの暫定)
sites/example/      出力例(そのまま build できる)
```

ライセンス: AGPL-3.0

AGPL を選ぶ理由: 本キットはネットワーク越しにサービス化して使う場面
(SaaS 化・再配布)を想定しておらず、そこで生じうる「改変して配布・提供
しても差分を公開しない」を防ぐのが AGPL の役割である。組織が自組織の
サイト・業務のために社内で導入・改変して使うだけであれば、外部へ配布・
サービス提供しない限り、AGPL のソース公開義務は発生しない(社内利用は
「配布」にあたらない)。中小組織(SME)が自組織の運用のためだけに使う
通常の利用では、追加の法的義務を負わない。
