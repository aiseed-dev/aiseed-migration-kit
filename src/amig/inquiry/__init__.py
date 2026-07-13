"""問い合わせ処理(申込様式の生成・読み取り・受信振り分け)。

seminar-kit の様式方式(forms/parse/mailin)を汎用化したもの。
様式の唯一の定義は様式プロファイル(sites/<name>/forms/*.adoc。AsciiDoc の
ラベル付きリスト+SQL 語彙。DESIGN.md §5)。xlsx・記入用テキスト・DDL・
検証モデルはすべて同じ定義から派生する(profile → spec → derive/forms)。
DB は持たない——受信は担当の IMAP フォルダへ振り分けるだけで、
状態=所在フォルダ(INBOX=未着手 / staff/<key>=担当へ / pending=未処理)。
発行キー(HMAC)は用いない(DESIGN.md §5。真正性は送信者アドレスで確認)。
"""
