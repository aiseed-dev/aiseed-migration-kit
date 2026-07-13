"""ローカル LLM 呼び出し(キットで唯一の LLM 接点——DESIGN.md §7)。

LLM 呼び出しは1モジュールに集約し、モデルの差し替えはここ1箇所で済ませる
(汎用の抽象層は作らない)。LLM はオプショナル: AMIG_LLM_URL が無ければ
complete() は None を返し、呼び出し側はルールベース(=何もしない。人が
処理する)にフォールバックする。

接続先は OpenAI 互換の chat completions エンドポイント(ローカルの
Command A+ 等を vLLM / llama.cpp で提供する想定)。機微を扱うため、
外部 API を指す運用はしない(§7「なぜ機微を API に出せないか」)。

  AMIG_LLM_URL    例: http://localhost:8000/v1/chat/completions
  AMIG_LLM_MODEL  省略可(サーバー既定を使う)

依存は標準ライブラリのみ(urllib)。失敗はログに残して None(受付処理を
LLM の障害で止めない——AI は提案経路であり決定経路にない)。
"""

from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.request

log = logging.getLogger(__name__)

TIMEOUT = 120  # ローカルの大型モデルは遅い


def enabled() -> bool:
    """ローカル LLM が設定されているか(呼び出し前の早期判定に使う)。"""
    return bool(os.environ.get("AMIG_LLM_URL", "").strip())


def complete(prompt: str) -> str | None:
    """プロンプト1つ → 応答テキスト。LLM 未設定・失敗は None。"""
    url = os.environ.get("AMIG_LLM_URL", "").strip()
    if not url:
        return None
    payload: dict = {"messages": [{"role": "user", "content": prompt}]}
    model = os.environ.get("AMIG_LLM_MODEL", "").strip()
    if model:
        payload["model"] = model
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as res:
            data = json.loads(res.read().decode("utf-8"))
        return str(data["choices"][0]["message"]["content"])
    except (urllib.error.URLError, TimeoutError, KeyError, ValueError, OSError):
        log.exception("LLM 呼び出しに失敗(提案なしで続行)")
        return None
