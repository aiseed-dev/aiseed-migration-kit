"""AsciiDoc の行レベル共通処理(コメント除去・題名)。

様式プロファイル(inquiry/profile)と決裁文書(decision)が同じ規約で
読むための共通部品。「何がコメントか」「何が題名か」の定義を一箇所に
置き、二つの読み手の解釈が食い違わないようにする。
"""

from __future__ import annotations

import re

TITLE_RE = re.compile(r"^=[ \t]+(\S.*?)[ \t]*$")
_LINE_COMMENT_RE = re.compile(r"^//(?!/)")
_BLOCK_COMMENT_DELIM_RE = re.compile(r"^/{4,}[ \t]*$")


def strip_comments(text: str) -> list[str]:
    """コメント(``//`` 行・``////`` ブロック)を除いた行のリストを返す。"""
    out = []
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]
        if _BLOCK_COMMENT_DELIM_RE.match(line):
            i += 1
            while i < len(lines) and not _BLOCK_COMMENT_DELIM_RE.match(lines[i]):
                i += 1
            i += 1  # 閉じ側の //// も読み飛ばす
            continue
        if _LINE_COMMENT_RE.match(line):
            i += 1
            continue
        out.append(line)
        i += 1
    return out
