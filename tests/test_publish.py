"""publish: cf-publish への引き渡し(実配信はしない)。"""

import sys
import types

import pytest

from amig import publish as publish_mod


def test_empty_dist(tmp_site):
    with pytest.raises(publish_mod.PublishError, match="build"):
        publish_mod.publish(tmp_site)


def test_args_passed(tmp_site, monkeypatch):
    tmp_site.dist.mkdir(parents=True, exist_ok=True)
    (tmp_site.dist / "index.html").write_text("x", encoding="utf-8")
    calls = []
    fake_cli = types.SimpleNamespace(main=lambda argv: calls.append(argv))
    fake_pkg = types.SimpleNamespace(cli=fake_cli)
    monkeypatch.setitem(sys.modules, "cf_publish", fake_pkg)
    monkeypatch.setitem(sys.modules, "cf_publish.cli", fake_cli)

    publish_mod.publish(tmp_site, dry_run=True)
    assert len(calls) == 1
    argv = calls[0]
    assert argv[0] == str(tmp_site.dist)
    assert "--project" in argv and "s" in argv  # tmp_site のディレクトリ名
    assert "--dry-run" in argv
