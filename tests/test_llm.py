"""llm.complete の env 未設定時のフォールバック。"""


def test_complete_without_url_is_none(monkeypatch):
    from amig import llm

    monkeypatch.delenv("AMIG_LLM_URL", raising=False)
    assert llm.complete("x") is None
