"""配信: dist/ を cf-publish で Cloudflare Pages へ反映する。

cf-publish は同梱(vendor/cf-publish。PyPI 公開までの暫定)。未インストール
なら導入方法を案内して止まる。認証(CLOUDFLARE_API_TOKEN 等)は cf-publish
側の流儀に従う(環境変数 or ~/.config/cloudflare/pages.env)。
実際の公開実行は運用判断で行う(勝手に配信しない)。
"""

from cmsmig.site import Site


class PublishError(Exception):
    """配信できない状態。メッセージは運用担当者向けの日本語。"""


def publish(
    site: Site,
    branch: str = "main",
    dry_run: bool = False,
) -> None:
    """dist/ を Cloudflare Pages のプロジェクトへ配信する。

    プロジェクト名は site.yaml の project(無ければサイト名)。
    """
    try:
        from cf_publish.cli import main as cf_main
    except ImportError:
        raise PublishError(
            "cf-publish が見つかりません。次で導入してください:\n"
            "  pip install vendor/cf-publish"
        ) from None
    if not site.dist.exists() or not any(site.dist.iterdir()):
        raise PublishError("dist/ が空です。先に `cmsmig build` を実行してください")
    project = str(site.cfg.get("project") or site.name)
    args = [str(site.dist), "--project", project, "--branch", branch]
    if dry_run:
        args.append("--dry-run")
    cf_main(args)
