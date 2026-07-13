"""amig コマンド(パイプラインの入口)。

    amig new sites/<name>            サイトの雛形を作る
    amig ingest <site> <入力...>     元データを source/raw/ へ取り込む
    amig classify <site>             記事/一覧に分類(classified.yaml)
    amig convert <site> [--force]    記事を content/*.md へ機械変換
    amig build <site>                dist/ を生成
    amig publish <site> [--dry-run]  Cloudflare Pages へ配信
    amig forms <site>                申込様式(xlsx+記入テキスト)を forms-out/ へ生成
    amig macro <site> <form>         様式マクロ(OnlyOffice JS)を出力
    amig ddl <site> [<form>]         PostgreSQL DDL(CREATE TABLE)を出力
    amig prompt <site> <form>        pending 解釈用の AI プロンプトを出力
    amig mailin <site> [--once]      受付メールの振り分け(IMAP)
    amig docindex <dir>              決裁文書の属性インデックス(SQL)を出力
    amig freeze <file> [--source]    交付物の凍結記録(SHA-256+生成元)を作成
"""

import argparse
import sys
from pathlib import Path

from amig import build as build_mod
from amig import classify as classify_mod
from amig import convert as convert_mod
from amig import ingest as ingest_mod
from amig import publish as publish_mod
from amig import site as site_mod

NEW_SITE_YAML = """\
title: サイト名(機関名)
# base_url: https://example.pages.dev
# project: example        # Cloudflare Pages のプロジェクト名(既定はサイト名)
lang: ja

categories:
  news: お知らせ

# 問い合わせを使うときに設定する。様式は build で dist/forms/ に公開される。
# 受付アドレスはページに書かれず、様式の中にだけ入る。
# 様式の定義は forms/*.adoc(様式プロファイル。sites/example/forms/ 参照)
# inquiry:
#   address: uketsuke@example.jp
#   staff:
#     - {key: general, label: 総合窓口}
#   forms:
#     - forms/contact.adoc
"""


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(prog="amig", description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("new", help="サイトの雛形を作る")
    sp.add_argument("site")

    sp = sub.add_parser("ingest", help="元データを source/raw/ へ取り込む")
    sp.add_argument("site")
    sp.add_argument("inputs", nargs="+")

    sp = sub.add_parser("classify", help="記事/一覧に分類する")
    sp.add_argument("site")

    sp = sub.add_parser("convert", help="記事を content/*.md へ機械変換する")
    sp.add_argument("site")
    sp.add_argument("--force", action="store_true", help="既存の .md も上書きする")

    sp = sub.add_parser("build", help="dist/ を生成する")
    sp.add_argument("site")

    sp = sub.add_parser("publish", help="Cloudflare Pages へ配信する")
    sp.add_argument("site")
    sp.add_argument("--branch", default="main")
    sp.add_argument("--dry-run", action="store_true")

    sp = sub.add_parser(
        "forms", help="申込様式(xlsx+記入用テキスト)を forms-out/ へ生成する"
    )
    sp.add_argument("site")

    sp = sub.add_parser("macro", help="様式マクロ(OnlyOffice JS)を出力する")
    sp.add_argument("site")
    sp.add_argument("form")

    sp = sub.add_parser("ddl", help="PostgreSQL DDL(CREATE TABLE)を出力する")
    sp.add_argument("site")
    sp.add_argument("form", nargs="?", help="省略時は全様式")

    sp = sub.add_parser(
        "prompt", help="pending 解釈用の AI プロンプトを出力する(§7)"
    )
    sp.add_argument("site")
    sp.add_argument("form")

    sp = sub.add_parser("mailin", help="受付メールを担当フォルダへ振り分ける")
    sp.add_argument("site")
    sp.add_argument("--once", action="store_true", help="1回だけさらって終了")

    sp = sub.add_parser(
        "docindex", help="決裁文書(.adoc)の属性インデックス(SQL)を出力する"
    )
    sp.add_argument("dir")

    sp = sub.add_parser(
        "freeze", help="交付物の凍結記録(<file>.hash.yaml)を作成する"
    )
    sp.add_argument("file")
    sp.add_argument("--source", help="生成元の決裁文書(.adoc)")

    args = p.parse_args(argv)
    try:
        _run(args)
    except (site_mod.SiteError, build_mod.BuildError, publish_mod.PublishError) as e:
        print(f"エラー: {e}", file=sys.stderr)
        raise SystemExit(1) from None


def _run(args: argparse.Namespace) -> None:
    if args.cmd == "new":
        root = Path(args.site)
        if (root / "site.yaml").exists():
            raise site_mod.SiteError(f"{root}/site.yaml は既にあります")
        for d in ("content", "data", "public"):
            (root / d).mkdir(parents=True, exist_ok=True)
        (root / "site.yaml").write_text(NEW_SITE_YAML, encoding="utf-8")
        print(f"作成しました: {root}(site.yaml を編集してください)")
        return

    # 決裁部品(§14)はサイト設定に依存しない
    if args.cmd == "docindex":
        from amig import decision

        root = Path(args.dir)
        if not root.is_dir():
            raise site_mod.SiteError(f"{root} はディレクトリではありません")
        print(decision.index_sql(decision.scan(root)), end="")
        return
    if args.cmd == "freeze":
        from amig import decision

        artifact = Path(args.file)
        if not artifact.is_file():
            raise site_mod.SiteError(f"{artifact} がありません")
        out = decision.freeze(
            artifact, source=Path(args.source) if args.source else None
        )
        print(f"凍結記録を作成: {out}")
        return

    site = site_mod.load(args.site)

    if args.cmd == "ingest":
        n = ingest_mod.ingest(site, [Path(x) for x in args.inputs])
        print(f"取り込み {n} 件 → {site.raw}")
    elif args.cmd == "classify":
        result = classify_mod.classify(site)
        print(f"分類 {classify_mod.counts(result)} → {site.source / '(classified.yaml)'}")
    elif args.cmd == "convert":
        written, skipped = convert_mod.convert(site, force=args.force)
        note = "(--force で上書き)" if skipped and not args.force else ""
        print(f"変換 {written} 件 / 既存を保護 {skipped} 件{note} → {site.content}")
    elif args.cmd == "build":
        n = build_mod.build(site)
        print(f"生成 {n} ページ → {site.dist}")
    elif args.cmd == "publish":
        publish_mod.publish(site, branch=args.branch, dry_run=args.dry_run)
    elif args.cmd == "forms":
        _forms(site)
    elif args.cmd == "macro":
        from amig.inquiry import forms as forms_mod

        print(forms_mod.macro_js(site, site.form(args.form)))
    elif args.cmd == "ddl":
        from amig.inquiry import derive

        targets = [site.form(args.form)] if args.form else list(site.forms)
        print("\n".join(derive.ddl(f) for f in targets), end="")
    elif args.cmd == "prompt":
        from amig.inquiry import derive

        print(derive.prompt(site.form(args.form)))
    elif args.cmd == "mailin":
        from amig.inquiry import mailin

        mailin.run(site, once=args.once)


def _forms(site: site_mod.Site) -> None:
    from amig.inquiry import derive
    from amig.inquiry import forms as forms_mod

    addr = str((site.cfg.get("inquiry") or {}).get("address") or "")
    if not addr:
        raise site_mod.SiteError("site.yaml の inquiry.address(受付アドレス)が未設定です")
    if not site.staff:
        raise site_mod.SiteError("site.yaml の inquiry.staff(担当)が未設定です")
    if not site.forms:
        raise site_mod.SiteError("site.yaml の inquiry.forms(様式)が未設定です")
    site.forms_out.mkdir(parents=True, exist_ok=True)
    for form in site.forms:
        wb = forms_mod.build(site, form, addr)
        out = site.forms_out / f"{form.key}.xlsx"
        wb.save(out)
        txt = site.forms_out / f"{form.key}.txt"
        txt.write_text(derive.filltext(site, form, addr), encoding="utf-8")
        print(f"様式を生成: {out} / {txt.name}({form.label})")
    print(
        "次回の build で dist/forms/ に公開されます"
        "(受付アドレスはページに書かず、様式の中にだけ入っています)"
    )
