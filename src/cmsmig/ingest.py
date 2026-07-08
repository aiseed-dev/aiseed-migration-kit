"""取り込み: 機関から提供された元データを source/raw/ へ写す。

クロールは行わない(データ提供が原則。クロールは了解を得た最後の手段で、
このキットの外で行う)。取り込みは決定的なファイルコピーで、何を取り込んだ
かを source/manifest.yaml に記録する(監査・差分確認用)。
"""

import hashlib
import shutil
from pathlib import Path

import yaml

from cmsmig.site import Site


def ingest(site: Site, inputs: list[Path]) -> int:
    """ファイル・ディレクトリ群を source/raw/ へコピーし、件数を返す。

    ディレクトリは相対構造を保って写す。同名ファイルは上書きする
    (source/ は取り込みの写しであり、編集の正は content/ 側)。
    """
    entries: list[dict[str, object]] = []
    site.raw.mkdir(parents=True, exist_ok=True)
    for src in inputs:
        src = Path(src)
        if src.is_dir():
            for f in sorted(src.rglob("*")):
                if f.is_file() and not f.name.startswith("."):
                    entries.append(_copy(site, f, f.relative_to(src)))
        elif src.is_file():
            entries.append(_copy(site, src, Path(src.name)))
        else:
            raise FileNotFoundError(src)
    manifest = {"files": entries}
    (site.source / "manifest.yaml").write_text(
        yaml.safe_dump(manifest, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    return len(entries)


def _copy(site: Site, src: Path, rel: Path) -> dict[str, object]:
    dst = site.raw / rel
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)
    data = dst.read_bytes()
    return {
        "path": str(rel),
        "bytes": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
    }
