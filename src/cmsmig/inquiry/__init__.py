"""問い合わせ処理(申込様式の生成・読み取り・受信振り分け)。

seminar-kit の様式方式(forms/parse/mailin)を汎用化したもの。
様式の定義は site.yaml の inquiry.forms が正(神Excel=唯一のフォーム定義)。
DB は持たない——受信は担当の IMAP フォルダへ振り分けるだけで、
状態=所在フォルダ(INBOX=未着手 / staff/<key>=担当へ / pending=未処理)。
発行キー(HMAC)は用いない(DESIGN.md §5。真正性は送信者アドレスで確認)。
"""
