# stalwart-pim

Stalwart サーバー専用のメール・予定・連絡先クライアント。
仕様は [../docs/stalwart-pim-spec.md](../docs/stalwart-pim-spec.md)。
前提サーバーは Stalwart 0.12 以上(CalDAV / CardDAV 対応版)。

## セットアップ(3 手順)

1. Python 3.12 以上の環境を用意する(venv / conda どちらでもよい)
2. `pip install .`(Flet 1.0 系。デスクトップ実行体は初回起動時に
   自動ダウンロードされるため、初回だけネット接続が要る)
3. `python main.py` → 設定画面でサーバー URL・ユーザー名・
   アプリパスワードを入れて「接続テスト」

複数アカウントは `python main.py --profile 名前` で分離して複数起動する。
データは `~/.local/share/stalwart-pim/<プロファイル>/` に置かれる。

## 検証用 Stalwart

本番とは別に、検証用サーバーをコンテナで立てる(仕様 §7):

```
docker compose -f dev/compose.yaml up -d
docker compose -f dev/compose.yaml logs | grep password
```

初期管理者(admin)のパスワードは初回起動ログに出る。
管理画面 http://localhost:8080 でテスト用アカウントを作って使う。

## プロトコル層の単体実行

UI なしで各サービスを検証できる(仕様 §7):

```
python -m services.jmap_service      # 受信箱の一覧を標準出力に出す
python -m services.caldav_service    # 今月の予定を出す
python -m services.carddav_service   # 連絡先を出す
```
